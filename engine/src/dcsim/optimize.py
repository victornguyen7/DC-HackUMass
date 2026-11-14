"""Optimize plan (IMPROVEMENTS 2.1): search kitchen plans for less waste, and show what it costs.

Black-box search (Optuna, multi-objective TPE), not learning from data: every trial is a kitchen plan,
simulated on the same seeds as today's plan (common random numbers), and scored on two things at once,
median waste and median stockout minutes (p90 wait is a limit). The answer is a **trade-off frontier**:
the plans no other plan beats on both. It exists because smaller batches waste less but need more
batches, so the cooks (kitchen_resources) fall behind and food runs out (measured on Nov 13: batches x0.7
cut waste 31% but tripled stockout minutes and batch waits went from 438 to 2,419 min).

The **recommended plan** is the frontier plan with the least waste whose stockouts stay within the limit
(default: today's plus the Lab's "too small to matter" margin, runner.min_meaningful). Because the best
of many trials on 10 nights flatters itself, every frontier plan is checked again on seeds the search
never saw (31-60), and the recommended one also in plausible worlds (worlds.py). There may be no
recommended plan: then nothing beats today's within the limit, and the frontier shows what more
stockouts would buy.

Search space (0.1 steps, so configs repeat and hit the results cache):
  - per station with batch-cooked dishes: a batch-size multiplier (0.6-1.2) and a reorder-point
    multiplier (0.8-1.2);
  - per dish, for the 5 dishes that waste the most today: an extra batch-size multiplier (0.6-1.2).
Every plan is a plain `Levers` (kitchen settings per dish), so it runs, compares and adopts like any
other Lab plan.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from .config import ScenarioConfig
from .data import load_baseline
from .levers import KitchenLever, Levers, ScenarioRequest, build_config
from .runner import ScenarioResult, compare, min_meaningful

STEP = 0.1
RANGES = {"batch": (0.6, 1.2), "reorder": (0.8, 1.2), "dish": (0.6, 1.2)}
TOP_DISHES = 5
WAIT_LIMIT_MIN = 8.0  # SPEC default: p90 wait at most 8 min
VALIDATE = (31, 60)  # unseen seeds
MAX_VALIDATE = 4  # frontier plans re-checked on them
# Whole-kitchen starting points (batch, reorder): today's plan first, then a few that map the trade-off.
STARTS = ((1.0, 1.0), (0.9, 1.0), (0.8, 1.0), (1.0, 0.9), (0.9, 0.9))

Progress = Callable[[int, int], None]


def _median(nights, attr: str) -> float:
    return float(np.median([getattr(n, attr) for n in nights]))


def top_waste_dishes(base: ScenarioConfig, nights, k: int = TOP_DISHES) -> list[str]:
    waste = {d: float(np.median([n.dishes[d].waste for n in nights])) for d in base.kitchen}
    return [d for d, w in sorted(waste.items(), key=lambda x: (-x[1], x[0]))[:k] if w > 0]


def batch_stations(base: ScenarioConfig) -> list[str]:
    return sorted({d.station for d in base.menu if d.id in base.kitchen})


def levers_for(base: ScenarioConfig, params: dict[str, float]) -> Levers:
    """Multipliers -> per-dish kitchen settings. Smaller batches get more batches, so the
    total a dish can cook never drops below today's."""
    kitchen: dict[str, KitchenLever] = {}
    for d in base.menu:
        p = base.kitchen.get(d.id)
        if p is None:
            continue
        bm = params.get(f"batch:{d.station}", 1.0) * params.get(f"dish:{d.id}", 1.0)
        rm = params.get(f"reorder:{d.station}", 1.0)
        batch = max(1, round(p.batch_size * bm))
        reorder = max(0, round(p.reorder_point * rm))
        change: dict = {}
        if batch != p.batch_size:
            change["batch_size"] = batch
            need = math.ceil(p.max_batches * p.batch_size / batch)
            if need > p.max_batches:
                change["max_batches"] = need
        if reorder != p.reorder_point:
            change["reorder_point"] = reorder
        if change:
            kitchen[d.id] = KitchenLever(**change)
    return Levers(kitchen=kitchen)


def describe(base: ScenarioConfig, params: dict[str, float]) -> list[str]:
    """Readable changes, e.g. "Grill: batches x0.7, reorder at x1.1"; uniform ones as "All stations"."""
    names = {s.id: s.name for s in base.stations}
    dishes = {d.id: d.name for d in base.menu}
    stations = batch_stations(base)
    out = []
    for key, what in (("batch", "batches"), ("reorder", "reorder at")):
        vals = {s: params.get(f"{key}:{s}", 1.0) for s in stations}
        if len(set(vals.values())) == 1 and abs(next(iter(vals.values())) - 1.0) > 1e-9:
            out.append(f"All stations: {what} x{next(iter(vals.values())):.1f}")
            for s in stations:
                params = params | {f"{key}:{s}": 1.0}
    size = lambda sid: max(abs(params.get(f"{k}:{sid}", 1.0) - 1.0) for k in ("batch", "reorder"))  # noqa: E731
    for sid in sorted(stations, key=lambda sid: (-size(sid), sid)):  # biggest changes first
        parts = [f"{what} x{params[f'{key}:{sid}']:.1f}" for key, what in (("batch", "batches"), ("reorder", "reorder at"))
                 if abs(params.get(f"{key}:{sid}", 1.0) - 1.0) > 1e-9]
        if parts:
            out.append(f"{names.get(sid, sid)}: {', '.join(parts)}")
    for k, v in sorted(params.items()):
        if k.startswith("dish:") and abs(v - 1.0) > 1e-9:
            out.append(f"{dishes.get(k[5:], k[5:])}: batches x{v:.1f} on top")
    return out


def _subset(nights, lo: int, hi: int):
    return [n for n in nights if lo <= n.seed <= hi]


def _frontier(rows: list[dict]) -> list[dict]:
    """Plans within the wait limit that no other such plan beats on both waste and stockouts."""
    ok = [r for r in rows if r["wait_ok"]]
    front = [r for r in ok if not any(
        o["waste"] <= r["waste"] and o["stockout_min"] <= r["stockout_min"]
        and (o["waste"] < r["waste"] or o["stockout_min"] < r["stockout_min"]) for o in ok)]
    seen, out = set(), []
    for r in sorted(front, key=lambda r: (r["waste"], r["stockout_min"], r["n"])):
        if (r["waste"], r["stockout_min"]) not in seen:  # identical configs (cache hits) appear once
            seen.add((r["waste"], r["stockout_min"]))
            out.append(r)
    return out


def optimize(svc, date: str, trials: int = 80, seeds: int = 10, max_stockout_min: float | None = None,
             max_wait_p90: float | None = None, worlds_n: int = 100, sampler_seed: int = 0,
             validate: tuple[int, int] | None = None,
             on_trial: Callable[[dict], None] | None = None, on_progress: Progress | None = None) -> dict:
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    base = load_baseline(date)
    base_n = svc.ensure_nights(base, seeds)
    b_waste, b_stock, b_wait = (_median(base_n, a) for a in ("waste_total", "stockout_min_total", "wait_p90"))
    limits = {
        "stockout_min_total": max_stockout_min if max_stockout_min is not None
        else b_stock + max(min_meaningful("stockout_min_total", b_stock), 0.05 * b_stock),
        "wait_p90": max_wait_p90 if max_wait_p90 is not None
        else max(WAIT_LIMIT_MIN, b_wait + min_meaningful("wait_p90", b_wait)),
    }
    stations, top = batch_stations(base), top_waste_dishes(base, base_n)
    keys = [f"batch:{s}" for s in stations] + [f"reorder:{s}" for s in stations] + [f"dish:{d}" for d in top]
    rows: list[dict] = []

    def objective(trial) -> tuple[float, float]:
        params = {k: round(trial.suggest_float(k, *RANGES[k.split(":")[0]], step=STEP), 1) for k in keys}
        cfg = build_config(ScenarioRequest(date=date, levers=levers_for(base, params)))
        nights = svc.ensure_nights(cfg, seeds)
        waste, stock, wait = (_median(nights, a) for a in ("waste_total", "stockout_min_total", "wait_p90"))
        trial.set_constraint("wait", wait - limits["wait_p90"])
        within = stock <= limits["stockout_min_total"] and wait <= limits["wait_p90"]
        best = min([r["waste"] for r in rows if r["within"]] + ([waste] if within else []), default=None)
        row = {"n": len(rows) + 1, "of": trials, "waste": round(waste, 1), "stockout_min": round(stock, 1),
               "wait_p90": round(wait, 2), "wait_ok": wait <= limits["wait_p90"], "within": within,
               "feasible": within, "best_waste": None if best is None else round(best, 1), "params": params}
        rows.append(row)
        if on_trial:
            on_trial({k: v for k, v in row.items() if k != "params"})
        return waste, stock

    sampler = optuna.samplers.TPESampler(seed=sampler_seed, multivariate=True)
    study = optuna.create_study(directions=["minimize", "minimize"], sampler=sampler)
    for b, r in STARTS[:max(1, min(len(STARTS), trials // 4))]:
        study.enqueue_trial({k: (b if k.startswith("batch:") else r if k.startswith("reorder:") else 1.0) for k in keys})
    study.optimize(objective, n_trials=trials)

    def public(r: dict) -> dict:
        lv = levers_for(base, r["params"])
        return {k: v for k, v in r.items() if k not in ("params", "of", "best_waste", "feasible")} | {
            "changes": describe(base, r["params"]) or ["Today's plan"],
            "levers": lv.model_dump(exclude_defaults=True),
            "waste_pct": round(100 * (r["waste"] - b_waste) / b_waste, 1) if b_waste else 0.0,
        }

    frontier = _frontier(rows)
    out = {
        "date": date, "trials": trials, "seeds": seeds, "nights_searched": trials * seeds,
        "feasible_trials": sum(r["within"] for r in rows),
        "baseline": {"waste": round(b_waste, 1), "stockout_min": round(b_stock, 1), "wait_p90": round(b_wait, 2)},
        "limits": {k: round(v, 2) for k, v in limits.items()},
        "search_space": {"stations": stations, "top_dishes": top, "ranges": RANGES, "step": STEP},
        "history": [{k: v for k, v in r.items() if k not in ("params", "of", "feasible")} for r in rows],
        "frontier": [public(r) for r in frontier],
        "levers": None, "changes": [],
        "method": (f"Searched {trials} plans across {trials * seeds} simulated nights (Optuna, black-box search, "
                   "not learning from data). Each plan was scored on waste and stockouts together; the frontier "
                   "plans were re-checked on 30 nights the search never saw."),
    }

    # Re-check every frontier plan on unseen seeds: run 1..60 (cached; the same seeds for all), compare on 31..60.
    from .worlds import _run_all, sweep

    lo, hi = validate or VALIDATE
    plans = [(f, r, build_config(ScenarioRequest(date=date, levers=levers_for(base, r["params"]), label="optimized plan")))
             for f, r in zip(out["frontier"], frontier) if f["levers"].get("kitchen")][:MAX_VALIDATE]
    nights = _run_all(svc, [(base, hi)] + [(cfg, hi) for *_, cfg in plans], on_progress)
    b_val = _subset(nights[0], lo, hi)
    for (f, _, cfg), p_n in zip(plans, nights[1:]):
        vs = compare(ScenarioResult("base", base.label, b_val), ScenarioResult("plan", cfg.label, _subset(p_n, lo, hi)))
        m = vs["metrics"]
        f["validation"] = {"seeds": [lo, hi], "waste_pct": round(100 * m["waste_total"]["diff"]["p50"] / _median(b_val, "waste_total"), 1),
                           "stockout_diff_min": m["stockout_min_total"]["diff"]["p50"], "vs_baseline": vs}

    pick = min((r for r in rows if r["within"]), key=lambda r: (r["waste"], r["n"]), default=None)
    if pick is None or pick["waste"] >= b_waste - min_meaningful("waste_total", b_waste):
        return out  # nothing meaningfully better within the limits: today's plan is on the frontier
    lv = levers_for(base, pick["params"])
    cfg = build_config(ScenarioRequest(date=date, levers=lv, label="optimized plan"))
    p60 = _run_all(svc, [(cfg, hi)], on_progress)[0]
    out |= {"levers": lv.model_dump(exclude_defaults=True), "params": pick["params"],
            "changes": describe(base, pick["params"]), "best_trial": pick["n"],
            "train": {k: pick[k] for k in ("waste", "stockout_min", "wait_p90")},
            "validation": {"seeds": [lo, hi], "vs_baseline": compare(
                ScenarioResult("base", base.label, b_val), ScenarioResult("plan", cfg.label, _subset(p60, lo, hi)))}}
    if worlds_n:
        w = sweep(svc, base, cfg, n_worlds=worlds_n, tornado_worlds=0, on_progress=on_progress)
        out["worlds"] = {k: w[k] for k in ("n_worlds", "uncertainty", "vs_baseline", "waste")}
    return out
