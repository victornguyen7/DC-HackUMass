"""Synthetic "recorded" waste history (SPEC §5.3). SYNTHETIC: never real UMass data.

Each past night is simulated by the same engine, with noise a real kitchen has
and the plan doesn't know about: crowd swings, staff over-prep, weather, and
occasional event nights. Plate waste is added as a share of what was served
(it is not affected by production planning). Files mirror the shape of the
tools Worcester uses:

    data/synthetic/worcester/consumption.csv   MetaFoodX-style: per dish per 30-min window,
                                               portions put out / taken / left on the line
    data/synthetic/worcester/waste_log.csv     LeanPath-style: timestamp, dish, weight_lb, reason
    data/synthetic/worcester/nights.csv        one row per night (what a manager report would show)
    data/synthetic/worcester/_ground_truth.json  hidden noise parameters; never served by the API

Past nights reuse the real Nov 13-20 menu with the same weekday, because no
past Worcester menus were captured. A few nights in the last two weeks are
"adopted": the kitchen followed the recommended plan, so the Waste page can
show predicted vs recorded for them.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

import numpy as np

from .config import ScenarioConfig
from .data import available_dates, default_data_dir, load_baseline, repo_root
from .menu_import import serving_oz
from .model import Night
from .presets import PRESETS
from .recorder import PanEv

HISTORY_END = dt.date(2026, 11, 11)  # the day before "today" in the demo
HISTORY_DAYS = 42
WINDOW_MIN = 30
RECOMMENDED_PLAN = "smaller_batches"  # until the optimizer exists
ADOPTED_COUNT = 3
GEN_SEED = 20261112
DC = "worcester"


def out_dir() -> Path:
    return repo_root() / "data" / "synthetic" / DC


def portion_defaults() -> dict:
    return json.loads((default_data_dir() / "portion_defaults.json").read_text())


def dish_oz(cfg: ScenarioConfig, defaults: dict | None = None) -> dict[str, float]:
    d = defaults or portion_defaults()
    out = {}
    for x in cfg.menu:
        oz = serving_oz(x.serving_size)
        out[x.id] = oz if oz else d["oz_by_station"].get(x.station, d["oz_by_role"][x.role])
    return out


def dish_cost(cfg: ScenarioConfig, defaults: dict | None = None) -> dict[str, float]:
    d = defaults or portion_defaults()
    return {x.id: d["cost_per_portion_usd"][x.role] for x in cfg.menu}


def menu_date_for(day: dt.date) -> str:
    """The real menu with the same weekday."""
    by_wd = {dt.date.fromisoformat(d).weekday(): d for d in available_dates()}
    return by_wd[day.weekday()]


def history_days() -> list[dt.date]:
    return [HISTORY_END - dt.timedelta(days=i) for i in range(HISTORY_DAYS - 1, -1, -1)]


def _over_prep(cfg: ScenarioConfig, factor: float) -> ScenarioConfig:
    """Staff cook a bit more than the plan: every batch (opening stock included) is `factor` x bigger.

    Scaling the batch keeps over-prep proportional; rounding batch counts up would turn
    1 opening pan into 2 for every dish even at 1% over-prep.
    """
    kitchen = {}
    for k, p in cfg.kitchen.items():
        kitchen[k] = p.model_dump() | {"batch_size": max(1, round(p.batch_size * factor))}
        if p.taper_batch_size:
            kitchen[k]["taper_batch_size"] = max(1, round(p.taper_batch_size * factor))
    return cfg.updated(kitchen=kitchen)


def ground_truth(day: dt.date, adopted: bool) -> dict:
    rng = np.random.default_rng([GEN_SEED, day.toordinal()])
    weather = rng.choice(["clear", "rain", "cold"], p=[0.6, 0.25, 0.15])
    event = bool(rng.random() < 0.07)
    crowd = float(np.exp(rng.normal(0, 0.08)))
    crowd *= {"clear": 1.0, "rain": 0.94, "cold": 0.97}[weather] * (1.15 if event else 1.0)
    return {
        "date": day.isoformat(),
        "crowd_multiplier": round(crowd, 4),
        "weather": str(weather),
        "event_night": event,
        "over_prep": round(float(rng.uniform(1.0, 1.05) if adopted else rng.uniform(1.0, 1.2)), 4),
        "plate_waste_share": round(float(rng.uniform(0.06, 0.12)), 4),
        "adopted": adopted,
        "seed": 1000 + day.toordinal() % 10_000,
    }


def actual_config(day: dt.date, gt: dict, plan: ScenarioConfig | None = None) -> ScenarioConfig:
    """What the kitchen really ran: `plan` (default: baseline, or the recommended plan on adopted nights)
    with the night's crowd swing and staff over-prep."""
    if plan is not None:
        cfg = plan
    else:
        cfg = load_baseline(menu_date_for(day), events=False)  # weekday effect yes, the snapshot date's events no
        if gt["adopted"]:
            cfg = PRESETS[RECOMMENDED_PLAN](cfg)
    cfg = cfg.updated(**cfg.crowd_scaled(gt["crowd_multiplier"]))
    return _over_prep(cfg, gt["over_prep"])


def _hhmm(minutes: float) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h:02d}:{m:02d}"


NIGHT_ENDS_HOUR = 6  # a service night runs past midnight (late night ends 12 am); rows before 6 am belong to the previous night


def night_of(timestamp: str) -> str:
    """Service night (ISO date) of a log timestamp: late-night waste logged at 12:10 am counts for the night before."""
    t = dt.datetime.fromisoformat(timestamp) - dt.timedelta(hours=NIGHT_ENDS_HOUR)
    return t.date().isoformat()


def _stamp(day: dt.date, minutes: float) -> str:
    return (dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")


def pan_windows(night: Night, day: dt.date) -> tuple[list[dict], list[tuple[float, int, int]]]:
    """MetaFoodX-style rows (per batch-cooked dish per 30-min window) from a night run with trace=True.

    Used for the synthetic "recorded" history and for simulated replays (tsdb.py),
    so both sides of a comparison are bucketed by the same code. Also returns
    (t, dish_index, portions) for every expiry.
    """
    cfg, rec = night.cfg, night.rec
    o, c = cfg.open_min, cfg.service_end
    windows = list(range(o, c, WINDOW_MIN))
    batch_ids = {d.id for d in cfg.menu if d.id in cfg.kitchen}
    consumption = []
    prev = {i: 0 for i in range(len(cfg.menu))}
    per = {i: {w: {"out": 0, "taken": 0, "left": None} for w in windows} for i in range(len(cfg.menu))}
    expired_events = []
    for t, i, level, code in rec.pan_events:
        w = windows[min(int((min(t, c - 1e-9) - o) // WINDOW_MIN), len(windows) - 1)]
        delta = level - prev[i]
        if code in (PanEv.OPEN_STOCK, PanEv.BATCH_READY) and delta > 0:
            per[i][w]["out"] += delta
        elif code == PanEv.TAKE:
            per[i][w]["taken"] += 1
        elif code == PanEv.EXPIRE and delta < 0:
            expired_events.append((t, i, -delta))
        if code != PanEv.BATCH_START:
            prev[i] = level
            per[i][w]["left"] = level

    for i, d in enumerate(cfg.menu):
        if d.id not in batch_ids:
            continue
        level = 0
        for w in windows:
            row = per[i][w]
            level = row["left"] if row["left"] is not None else level
            if row["out"] or row["taken"] or level:
                consumption.append({
                    "date": day.isoformat(), "meal": cfg.meal, "dc": DC, "station": d.station, "dish_id": d.id,
                    "dish": d.name, "window_start": _hhmm(w), "portions_out": row["out"],
                    "portions_eaten": row["taken"], "portions_left": level,
                })
    return consumption, expired_events


def simulate_night(day: dt.date, gt: dict, plan: ScenarioConfig | None = None) -> dict:
    cfg = actual_config(day, gt, plan)
    night = Night(cfg, gt["seed"], trace=True).run()
    oz, cost = dish_oz(cfg), dish_cost(cfg)
    batch_ids = {d.id for d in cfg.menu if d.id in cfg.kitchen}
    consumption, expired_events = pan_windows(night, day)
    waste = []

    for t, i, n in expired_events:
        d = cfg.menu[i]
        waste.append({"timestamp": _stamp(day, t), "dc": DC, "station": d.station, "dish_id": d.id, "dish": d.name,
                      "portions": n, "weight_lb": round(n * oz[d.id] / 16, 2), "reason": "expired"})
    pans = {p.idx: p for p in night.pans}
    rng = np.random.default_rng([GEN_SEED, day.toordinal(), 7])
    for i, d in enumerate(cfg.menu):
        p = pans[i]
        if d.id in batch_ids and p.leftover:
            waste.append({"timestamp": _stamp(day, cfg.window(d.period)[1] + 10), "dc": DC, "station": d.station, "dish_id": d.id, "dish": d.name,
                          "portions": p.leftover, "weight_lb": round(p.leftover * oz[d.id] / 16, 2), "reason": "close"})
        plate = p.served * oz[d.id] / 16 * gt["plate_waste_share"] * float(rng.uniform(0.6, 1.4))
        if plate >= 0.05:
            waste.append({"timestamp": _stamp(day, cfg.window(d.period)[1] + 15), "dc": DC, "station": d.station, "dish_id": d.id, "dish": d.name,
                          "portions": None, "weight_lb": round(plate, 2), "reason": "plate"})

    from .metrics import compute_metrics

    m = compute_metrics(night)
    pre_lb = sum(w["weight_lb"] for w in waste if w["reason"] != "plate")
    pre_portions = sum(w["portions"] for w in waste if w["reason"] != "plate")
    pre_cost = sum(w["portions"] * cost[w["dish_id"]] for w in waste if w["reason"] != "plate")
    summary = {
        "date": day.isoformat(), "weekday": day.strftime("%A"), "menu_source_date": cfg.date,
        "students": m.students, "late_night_students": m.late_night_students, "adopted": gt["adopted"],
        "waste_portions": pre_portions, "waste_lb": round(pre_lb, 1), "waste_cost_usd": round(pre_cost, 2),
        "expired_lb": round(sum(w["weight_lb"] for w in waste if w["reason"] == "expired"), 1),
        "close_lb": round(sum(w["weight_lb"] for w in waste if w["reason"] == "close"), 1),
        "plate_lb": round(sum(w["weight_lb"] for w in waste if w["reason"] == "plate"), 1),
        "stockout_min": round(m.stockout_min_total, 1),
        "cooked_portions": sum(x.cooked for x in m.dishes.values() if not x.to_order),
    }
    return {"summary": summary, "consumption": consumption, "waste": waste}


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def generate(out: Path | None = None) -> Path:
    out = out or out_dir()
    out.mkdir(parents=True, exist_ok=True)
    days = history_days()
    adopted = set(days[-14:][:: 14 // ADOPTED_COUNT][:ADOPTED_COUNT])
    nights, consumption, waste, truth = [], [], [], []
    for day in days:
        gt = ground_truth(day, day in adopted)
        r = simulate_night(day, gt)
        nights.append(r["summary"])
        consumption += r["consumption"]
        waste += r["waste"]
        truth.append(gt)
    _write_csv(out / "nights.csv", nights)
    _write_csv(out / "consumption.csv", consumption)
    _write_csv(out / "waste_log.csv", waste)
    (out / "_ground_truth.json").write_text(json.dumps({
        "_note": "Hidden noise parameters of the synthetic generator. Not served by the API or the demo bundle.",
        "recommended_plan": RECOMMENDED_PLAN, "nights": truth}, indent=1))
    (out / "README.md").write_text(
        "# Synthetic Worcester waste history\n\n"
        "**SYNTHETIC. Not UMass data.** Generated by `uv run dcsim waste-gen` (engine/src/dcsim/synthetic.py) "
        f"for {days[0]} to {days[-1]}.\n\n"
        "- `consumption.csv`: MetaFoodX-style, per dish and 30-min window: portions put out, taken from the line, left.\n"
        "- `waste_log.csv`: LeanPath-style weigh-ins: `expired` (past hold time), `close` (left in pans), `plate` (after eating; not affected by planning).\n"
        "- `nights.csv`: one row per night. `waste_*` columns are pre-consumer (expired + close).\n"
        "- `_ground_truth.json`: the generator's hidden noise; never shown in the UI.\n\n"
        "Past nights reuse the real Nov 13–20 menu with the same weekday. Weights use menu serving sizes, "
        "or `data/worcester/portion_defaults.json` when a size isn't in ounces. Costs are estimates from the same file. "
        f"{ADOPTED_COUNT} nights in the last two weeks followed the recommended plan (`{RECOMMENDED_PLAN}`).\n"
    )
    return out


def load_nights(out: Path | None = None) -> list[dict]:
    p = (out or out_dir()) / "nights.csv"
    if not p.exists():
        return []
    rows = list(csv.DictReader(p.open()))
    for r in rows:
        for k, v in r.items():
            if k in ("date", "weekday", "menu_source_date"):
                continue
            r[k] = v == "True" if k == "adopted" else float(v)
    return rows


def load_waste_log(out: Path | None = None) -> list[dict]:
    p = (out or out_dir()) / "waste_log.csv"
    return list(csv.DictReader(p.open())) if p.exists() else []
