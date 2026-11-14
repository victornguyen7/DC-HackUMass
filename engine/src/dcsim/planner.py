"""Baseline production plan: what a planner would cook for typical demand.

Runs pilot nights with neutral popularity (no staff overrides) and unlimited
food, measures each dish's demand, opening-rush demand and peak 10-minute rate,
then sizes batches with the role defaults in kitchen_defaults.json:

    batch          = clamp(role batch, min_batch, demand / 4)
    starting       = opening_factor x demand in the first opening_window_min / batch
    reorder point  = reorder_factor x peak demand per minute x cook time
    max batches    = capacity_factor x demand / batch - starting
    last batch     = close - last_batch_before_close_min, or earlier with tail_fill: the last
                     batch must be ready while at least tail_fill x batch portions are still to be
                     eaten (late night's demand collapses after ~10:30 pm)

    taper          = with taper_fill: once fewer than taper_fill x batch portions are still to be
                     eaten, batches shrink to taper_batch_factor x batch and the reorder point
                     follows the (much lower) remaining pace

`planner_by_period` in kitchen_defaults.json overrides these settings per service period.

Because the plan only knows typical demand, real favorites (overrides.json ->
popularity) run out and slow sellers are left over, as in the demo story.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from .data import MEAL, default_data_dir, load_baseline
from .model import Night


def _take_times(changes: list[tuple[float, int]], end_min: float) -> list[float]:
    out, prev = [], None
    for t, lvl in changes:
        if prev is not None and prev - lvl == 1 and t < end_min:
            out.append(t)
        prev = lvl
    return out


def _tail_cutoff(takes: list[np.ndarray], open_min: float, close_min: float, keep: float, cook_min: float) -> int:
    """Latest time a batch may be ready: while at least `keep` portions are still to be eaten
    afterwards (median over pilot nights). Never before the first batch could be ready."""
    last = open_min + cook_min
    for t in range(int(open_min), int(close_min), 5):
        remaining = float(np.median([np.sum(x > t) for x in takes])) if takes else 0.0
        if remaining >= keep:
            last = max(last, t)
    return int(last)


def plan_kitchen(date: str, data_dir: Path | None = None) -> Path:
    d = data_dir or default_data_dir()
    defaults = json.loads((d / "kitchen_defaults.json").read_text())
    role_cfg, p_all = defaults["roles"], defaults["planner"]
    by_period = defaults.get("planner_by_period", {})
    p = p_all
    params = lambda period: p_all | by_period.get(period, {})  # noqa: E731
    # Typical demand for this weekday; one-off events (Friendsgiving) are not in the plan, and the
    # synthetic history reuses this plan for other nights with the same weekday.
    cfg = load_baseline(date, d, neutral=True, kitchen="unlimited", events=False)
    windows = {d.id: cfg.window(d.period) for d in cfg.menu}  # each dish is planned over its own period

    stats: dict[str, dict[str, list[float]]] = {k: {"demand": [], "opening": [], "peak10": []} for k in cfg.kitchen}
    all_takes: dict[str, list[np.ndarray]] = {k: [] for k in cfg.kitchen}
    for seed in range(1, p["pilot_nights"] + 1):
        night = Night(cfg, seed).run()
        for i, dish in enumerate(cfg.menu):
            if dish.id not in cfg.kitchen:
                continue
            o, c = windows[dish.id]
            takes = np.array(_take_times(night.rec.pan_levels[i], c))
            s = stats[dish.id]
            s["demand"].append(len(takes))
            s["opening"].append(int(np.sum(takes < o + params(dish.period)["opening_window_min"])) if len(takes) else 0)
            bins = np.arange(o, c + 10, 10)
            s["peak10"].append(int(np.histogram(takes, bins=bins)[0].max()) if len(takes) else 0)
            all_takes[dish.id].append(takes)

    plans, basis = {}, {}
    for dish in cfg.menu:
        if dish.id not in cfg.kitchen:
            continue
        o, c = windows[dish.id]
        p = params(dish.period)
        service_min = c - o
        last_batch = c - p["last_batch_before_close_min"]
        r = role_cfg[dish.role]
        s = {k: float(np.median(v)) for k, v in stats[dish.id].items()}
        demand = s["demand"]
        # A batch should sell within its hold time at the average pace, or it expires.
        per_hold = demand * r["hold_min"] / service_min * p["hold_fill"]
        cap = max(p["min_batch"], round(r["batch_size"] * p.get("batch_cap_factor", 1.0)))
        batch = max(p["min_batch"], min(cap, math.ceil(per_hold)))
        start = max(1, math.ceil(p["opening_factor"] * s["opening"] / batch))
        reorder = math.ceil(p["reorder_factor"] * s["peak10"] / 10 * r["cook_min"])
        max_batches = max(0, math.ceil(p["capacity_factor"] * demand / batch) - start + p["spare_batches"])
        taper = {}
        if p.get("taper_fill") is not None:
            # Taper: once fewer than taper_fill x batch portions are still to be eaten, cook small
            # top-up batches (taper_batch_factor x batch) with a matching reorder point.
            t0 = _tail_cutoff(all_takes[dish.id], o, c, p["taper_fill"] * batch, r["cook_min"])
            small = max(p["min_batch"], math.ceil(batch * p["taper_batch_factor"]))
            rest = float(np.median([np.sum(x > t0) for x in all_takes[dish.id]]))
            tail_rate = rest / max(c - t0, 1)  # portions per minute after the taper starts
            taper = {"taper_from_min": t0, "taper_batch_size": small,
                     "taper_reorder_point": math.ceil(p["reorder_factor"] * tail_rate * r["cook_min"])}
            max_batches += math.ceil(rest / small) + 1
        if p.get("tail_fill") is not None:
            last_batch = min(last_batch, _tail_cutoff(all_takes[dish.id], o, c, p["tail_fill"] * batch, r["cook_min"]))
        plans[dish.id] = {
            "batch_size": batch, "starting_batches": start, "reorder_point": reorder,
            "cook_min": r["cook_min"], "hold_min": r["hold_min"], "max_batches": max_batches,
            "last_batch_min": last_batch,
        } | taper
        basis[dish.id] = {"name": dish.name, "expected_demand": demand, "opening_demand": s["opening"], "peak_per_10min": s["peak10"]}

    out = d / "kitchen" / f"{date}_{MEAL}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "_note": "Generated by `dcsim prepare` from pilot runs at neutral popularity. PLACEHOLDER until staff estimates; edit freely.",
        "date": date, "pilot_nights": p_all["pilot_nights"], "plans": plans, "basis": basis,
    }, indent=1))
    return out
