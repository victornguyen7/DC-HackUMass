"""Back-test: "if we had used the plan" on every recorded night (SPEC §5.2).

For each past (non-adopted) night, re-simulate that night's menu and crowd
(crowd from the recorded student count, not from hidden parameters) under:

    baseline plan, cooked as written   -> how much of the gap is just cooking to the forecast
    recommended plan                   -> what the What-If plan adds on top

Recorded waste is pre-consumer (expired + left at close); plate waste is not
affected by production planning and is left out. Every simulated number is a
range over SEEDS nights. Adopted nights get predicted (forecast crowd, before
the night) vs recorded instead.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

from .config import ScenarioConfig
from .data import load_baseline
from .metrics import NightMetrics
from .presets import PRESETS
from .service import Service
from .synthetic import RECOMMENDED_PLAN, dish_cost, dish_oz, load_nights, night_of, out_dir

SEEDS = 10
FORECAST_CROWD = (0.88, 1.0, 1.12)  # about +/-1.5 sd of night-to-night crowd swings


def _with_crowd(cfg: ScenarioConfig, students: float, expected: float) -> ScenarioConfig:
    mult = round(students / expected / 0.01) * 0.01  # 1% steps keep configs cacheable
    return cfg.updated(crowd=cfg.crowd.model_dump() | {"multiplier": cfg.crowd.multiplier * mult})


def _expected_students(cfg: ScenarioConfig) -> float:
    late = cfg.late_crowd.expected_students * cfg.late_crowd.multiplier if cfg.late_crowd is not None else 0.0
    return cfg.crowd.expected_students * cfg.crowd.multiplier + late


def _night_waste(cfg: ScenarioConfig, m: NightMetrics, oz: dict, cost: dict) -> dict:
    batch = [(k, d) for k, d in m.dishes.items() if not d.to_order]
    return {
        "portions": sum(d.waste for _, d in batch),
        "lb": sum(d.waste * oz[k] / 16 for k, d in batch),
        "usd": sum(d.waste * cost[k] for k, d in batch),
        "stockout_min": m.stockout_min_total,
    }


def _dish_waste(m: NightMetrics, oz: dict, cost: dict) -> dict[str, tuple[float, float]]:
    """Per batch-cooked dish: (lb, usd) wasted on one simulated night."""
    return {k: (d.waste * oz[k] / 16, d.waste * cost[k]) for k, d in m.dishes.items() if not d.to_order}


def _recorded_by_dish() -> dict[tuple[str, str], dict]:
    """(date, dish_id) -> recorded pre-consumer waste (expired + left at close) from the synthetic log."""
    from .synthetic import load_waste_log

    out: dict[tuple[str, str], dict] = {}
    for w in load_waste_log():
        if w["reason"] not in ("expired", "close"):
            continue
        r = out.setdefault((night_of(w["timestamp"]), w["dish_id"]),
                           {"dish": w["dish"], "station": w["station"], "lb": 0.0, "portions": 0})
        r["lb"] += float(w["weight_lb"])
        r["portions"] += int(w["portions"] or 0)
    return out


def _pct(a) -> dict:
    p10, p50, p90 = np.percentile(np.asarray(a, dtype=float), [10, 50, 90])
    return {"p10": round(float(p10), 1), "p50": round(float(p50), 1), "p90": round(float(p90), 1)}


def run_backtest(svc: Service, plan: str = RECOMMENDED_PLAN, seeds: int = SEEDS) -> dict:
    nights = load_nights()
    if not nights:
        raise FileNotFoundError("no synthetic history; run `dcsim waste-gen` first")
    rows, adopted_rows = [], []
    # per-seed totals across all back-tested nights -> range of the headline reduction
    tot_rec = {"lb": 0.0, "usd": 0.0, "portions": 0.0}
    tot = {k: np.zeros(seeds) for k in ("base_lb", "plan_lb", "plan_usd", "plan_portions", "plan_stockout", "base_stockout")}
    rec_stockout = 0.0
    recorded = _recorded_by_dish()
    # per dish over back-tested nights: recorded lb, plan lb / usd (mean of seeds), nights served
    dishes: dict[str, dict] = {}

    for n in nights:
        base = load_baseline(n["menu_source_date"], events=False)  # history night: no snapshot-date events
        oz, cost = dish_oz(base), dish_cost(base)
        if n["adopted"]:
            planned = PRESETS[plan](base)  # forecast before the night: expected crowd
            # Forecast range covers night-to-night randomness AND crowd forecast error (+/-12%).
            exp = _expected_students(planned)
            pred = [
                _night_waste(c, m, oz, cost)
                for f in FORECAST_CROWD
                for c in [_with_crowd(planned, exp * f, exp)]
                for m in svc.ensure_nights(c, seeds)
            ]
            lo_hi = _pct([p["lb"] for p in pred])
            adopted_rows.append({
                "date": n["date"], "weekday": n["weekday"], "plan": plan,
                "predicted_waste_lb": _pct([p["lb"] for p in pred]),
                "recorded_waste_lb": n["waste_lb"],
                "predicted_stockout_min": _pct([p["stockout_min"] for p in pred]),
                "recorded_stockout_min": n["stockout_min"],
                "predicted_students": round(_expected_students(planned)),
                "recorded_students": int(n["students"]),
                "within_range": lo_hi["p10"] <= n["waste_lb"] <= lo_hi["p90"],
            })
            continue

        exp = _expected_students(base)
        b_cfg = _with_crowd(base, n["students"], exp)
        p_cfg = _with_crowd(PRESETS[plan](base), n["students"], exp)
        b = [_night_waste(b_cfg, m, oz, cost) for m in svc.ensure_nights(b_cfg, seeds)]
        p_nights = svc.ensure_nights(p_cfg, seeds)
        p = [_night_waste(p_cfg, m, oz, cost) for m in p_nights]
        sim_lb = _pct([x["lb"] for x in p])
        per_seed = [_dish_waste(m, oz, cost) for m in p_nights]
        names = {d.id: (d.name, d.station) for d in base.menu}
        by_station: dict[str, dict] = {}
        for k in set().union(*per_seed) | {dk for (dt_, dk) in recorded if dt_ == n["date"]}:
            if k not in names:
                continue
            plan_lb = float(np.mean([s.get(k, (0.0, 0.0))[0] for s in per_seed]))
            plan_usd = float(np.mean([s.get(k, (0.0, 0.0))[1] for s in per_seed]))
            rec_lb = recorded.get((n["date"], k), {}).get("lb", 0.0)
            # Same basis as the nightly recorded cost (portions x dish cost), not re-derived from rounded lb (UI_ISSUES U16)
            rec_usd = recorded.get((n["date"], k), {}).get("portions", 0) * cost.get(k, 0.0)
            d = dishes.setdefault(k, {"dish_id": k, "dish": names[k][0], "station": names[k][1], "nights": 0,
                                      "recorded_lb": 0.0, "plan_lb": 0.0, "recorded_usd": 0.0, "plan_usd": 0.0})
            d["nights"] += 1
            d["recorded_lb"] += rec_lb
            d["plan_lb"] += plan_lb
            d["recorded_usd"] += rec_usd
            d["plan_usd"] += plan_usd
            st = by_station.setdefault(names[k][1], {"recorded_lb": 0.0, "plan_lb": 0.0})
            st["recorded_lb"] += rec_lb
            st["plan_lb"] += plan_lb
        rows.append({
            "date": n["date"], "weekday": n["weekday"], "students": int(n["students"]),
            "recorded_waste_lb": n["waste_lb"], "recorded_stockout_min": n["stockout_min"],
            "baseline_plan_waste_lb": _pct([x["lb"] for x in b]),
            "plan_waste_lb": sim_lb,
            "plan_stockout_min": _pct([x["stockout_min"] for x in p]),
            "plan_wins": sim_lb["p50"] < n["waste_lb"],
            "by_station": {k: {"recorded_lb": round(v["recorded_lb"], 1), "plan_lb": round(v["plan_lb"], 1)}
                           for k, v in sorted(by_station.items())},
        })
        tot_rec["lb"] += n["waste_lb"]
        tot_rec["usd"] += n["waste_cost_usd"]
        tot_rec["portions"] += n["waste_portions"]
        rec_stockout += n["stockout_min"]
        tot["base_lb"] += [x["lb"] for x in b]
        tot["plan_lb"] += [x["lb"] for x in p]
        tot["plan_usd"] += [x["usd"] for x in p]
        tot["plan_portions"] += [x["portions"] for x in p]
        tot["plan_stockout"] += [x["stockout_min"] for x in p]
        tot["base_stockout"] += [x["stockout_min"] for x in b]

    weeks = len(rows) / 7
    dish_rows = []
    for d in dishes.values():
        dish_rows.append({
            "dish_id": d["dish_id"], "dish": d["dish"], "station": d["station"], "nights": d["nights"],
            "recorded_lb_per_night": round(d["recorded_lb"] / d["nights"], 2),
            "plan_lb_per_night": round(d["plan_lb"] / d["nights"], 2),
            "change_pct": round((d["plan_lb"] / d["recorded_lb"] - 1) * 100, 1) if d["recorded_lb"] > 0 else None,
            "usd_saved_per_week": round((d["recorded_usd"] - d["plan_usd"]) / weeks, 1),
        })
    dish_rows.sort(key=lambda r: -r["recorded_lb_per_night"] * r["nights"])
    red = 1 - tot["plan_lb"] / tot_rec["lb"]
    from_forecast = 1 - tot["base_lb"] / tot_rec["lb"]
    headline = {
        "nights": len(rows),
        "plan_wins": sum(r["plan_wins"] for r in rows),
        "reduction_pct": _pct(red * 100),
        "of_which_cooking_to_forecast_pct": _pct(from_forecast * 100),
        "of_which_plan_changes_pct": _pct((red - from_forecast) * 100),
        "lb_per_week": _pct((tot_rec["lb"] - tot["plan_lb"]) / weeks),
        "usd_per_week": _pct((tot_rec["usd"] - tot["plan_usd"]) / weeks),
        # The average simulated week (mean over seeds): what the per-dish rows add up to (UI_ISSUES U16).
        "lb_per_week_mean": round(float((tot_rec["lb"] - np.mean(tot["plan_lb"])) / weeks), 1),
        "usd_per_week_mean": round(float((tot_rec["usd"] - np.mean(tot["plan_usd"])) / weeks), 1),
        "recorded_lb_per_week": round(tot_rec["lb"] / weeks, 1),
        "stockout_min_per_night": {
            "recorded": round(rec_stockout / len(rows), 1),
            "baseline_plan": _pct(tot["base_stockout"] / len(rows)),
            "plan": _pct(tot["plan_stockout"] / len(rows)),
        },
    }
    return {
        "kind": "simulated_vs_synthetic_recorded",
        "_note": "SYNTHETIC history; simulated savings, not measured. Pre-consumer waste only (expired + left at close).",
        "plan": plan, "seeds_per_night": seeds, "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "headline": headline, "nights": rows, "adopted_nights": adopted_rows,
        "_dishes_note": "Per dish over the back-tested nights: recorded pre-consumer lb per night served vs the plan "
                        "(mean of seeds). $ uses the dish cost table, like the nightly cost. The rows add up to "
                        "headline.usd_per_week_mean (the average simulated week); headline.usd_per_week is the "
                        "range over seeds, whose median can differ.",
        "dishes": dish_rows,
    }


def backtest_path() -> Path:
    return out_dir() / "backtest.json"


def write_backtest(svc: Service, plan: str = RECOMMENDED_PLAN) -> Path:
    p = backtest_path()
    p.write_text(json.dumps(run_backtest(svc, plan), indent=1))
    return p


def waste_history() -> dict:
    """What the Waste Record page shows: recorded (synthetic) nights, cause split, top dishes."""
    from .synthetic import load_waste_log

    nights = load_nights()
    log = load_waste_log()
    top: dict[str, dict] = {}
    for w in log:
        t = top.setdefault(w["dish_id"], {"dish_id": w["dish_id"], "dish": w["dish"], "station": w["station"],
                                          "expired_lb": 0.0, "close_lb": 0.0, "plate_lb": 0.0, "portions": 0})
        t[f"{w['reason']}_lb"] += float(w["weight_lb"])
        if w["portions"]:
            t["portions"] += int(w["portions"])
    for t in top.values():
        t["pre_consumer_lb"] = round(t["expired_lb"] + t["close_lb"], 1)
        for k in ("expired_lb", "close_lb", "plate_lb"):
            t[k] = round(t[k], 1)
    return {
        "kind": "recorded_synthetic",
        "_note": "SYNTHETIC history shaped like MetaFoodX/LeanPath exports. Not UMass data.",
        "nights": nights,
        "top_dishes": sorted(top.values(), key=lambda t: -t["pre_consumer_lb"])[:15],
    }
