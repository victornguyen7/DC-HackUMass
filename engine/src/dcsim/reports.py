"""Turn a summary of many nights into what the pages show (SPEC §4.1, §6.1).

- cook_sheet: per dish, how much to prepare, the batch plan and when it runs out
- flags: plain-English alerts (run-outs, long lines, diet coverage gaps)
- arrivals: expected students per 10 minutes
"""

from __future__ import annotations

from .arrivals import expected_arrivals
from .config import ScenarioConfig
from .population import eligible

LONG_LINE = 15  # people in line at peak
RUNOUT_SHARE = 0.5  # flag a dish that runs out in at least half the nights
LATE_GRACE_MIN = 15  # running out in the last minutes of service isn't worth an alert
DIETS = ("vegetarian", "vegan", "halal")


def clock(minutes: float | None) -> str | None:
    if minutes is None:
        return None
    h, m = divmod(int(round(minutes)), 60)
    return f"{(h - 1) % 12 + 1}:{m:02d} {'am' if h < 12 else 'pm'}"


def cook_sheet(cfg: ScenarioConfig, summary: dict) -> list[dict]:
    rows = []
    for d in cfg.menu:
        s = summary["dishes"][d.id]
        plan = cfg.kitchen.get(d.id)
        ro = s["runout_min"]
        row = {
            "dish_id": d.id,
            "name": d.name,
            "station": d.station,
            "role": d.role,
            "period": d.period,
            "diet_tags": list(d.diet_tags),
            "hot": d.hot,
            "to_order": plan is None,
            "popularity": round(d.popularity, 4),
            "popularity_source": "ai_estimate" if d.estimate else "menu",
            "portions_to_prepare": s["cooked"],
            "served": s["served"],
            "waste": s["waste"],
            "expired": s["expired"],
            "leftover": s["leftover"],
            "stockout_min": s["stockout_min"],
            "runout_share": s["runout_share"],
            "runout_window": None if ro is None else {"from": clock(ro["p10"]), "to": clock(ro["p90"]), "p50_min": ro["p50"]},
            "first_choice_demand": s["first_choice_demand"],
        }
        if plan is not None:
            row["plan"] = plan.model_dump() | {"last_batch_clock": clock(plan.last_batch_min or cfg.window(d.period)[1])}
        rows.append(row)
    rows.sort(key=lambda r: (cfg.station(r["station"]).name, -r["popularity"]))
    return rows


def flags(cfg: ScenarioConfig, summary: dict) -> list[dict]:
    out: list[dict] = []
    names = {d.id: d.name for d in cfg.menu}
    periods = {d.id: d.period for d in cfg.menu}

    for dish_id, s in summary["dishes"].items():
        ro = s["runout_min"]
        cutoff = cfg.window(periods[dish_id])[1] - LATE_GRACE_MIN  # dinner dishes: 9 pm; late night: 12 am
        if s["runout_share"] >= RUNOUT_SHARE and ro and ro["p50"] < cutoff:
            out.append({
                "kind": "runout", "severity": "warn", "dish_id": dish_id,
                "text": f"{names[dish_id]} runs out in {s['runout_share']:.0%} of nights, "
                        f"usually {clock(ro['p10'])}–{clock(ro['p90'])}",
            })

    for sid, q in summary["max_queue"].items():
        if q["p50"] > LONG_LINE:
            out.append({
                "kind": "long_line", "severity": "info", "station": sid,
                "text": f"{cfg.station(sid).name} line reaches about {q['p50']:.0f} people at peak",
            })

    # Diet coverage: is there still a hot entrée each diet can eat late in the night?
    for diet in DIETS:
        options = [d for d in cfg.menu if d.role == "entree" and d.hot and eligible(diet, d)]
        if not options:
            out.append({"kind": "diet_gap", "severity": "warn", "diet": diet,
                        "text": f"No hot {diet} entrée on the menu"})
            continue
        if any(d.id not in cfg.kitchen for d in options):  # a made-to-order option never runs out
            continue
        last = []
        for d in options:
            s = summary["dishes"][d.id]
            if s["runout_share"] < RUNOUT_SHARE or s["runout_min"] is None:
                break
            last.append(s["runout_min"]["p50"])
        else:
            gone = max(last)
            if gone < cutoff:
                out.append({"kind": "diet_gap", "severity": "warn", "diet": diet,
                            "text": f"No hot {diet} entrée left after about {clock(gone)} in most nights"})

    order = {"warn": 0, "info": 1}
    return sorted(out, key=lambda f: order[f["severity"]])


def arrivals(cfg: ScenarioConfig, window_min: int = 10) -> list[dict]:
    return [{"start": clock(t), "start_min": t, "expected": round(n, 1)} for t, n in expected_arrivals(cfg, window_min)]


def stations(cfg: ScenarioConfig, summary: dict) -> list[dict]:
    return [
        {
            "id": s.id, "name": s.name, "pos": list(s.pos), "servers": s.servers, "self_serve": s.self_serve,
            "to_order": s.to_order, "service_median_min": s.service_median_min,
            "max_queue": summary["max_queue"].get(s.id),
        }
        for s in cfg.stations
    ]
