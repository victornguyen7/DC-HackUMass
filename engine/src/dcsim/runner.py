"""Run many nights, aggregate into ranges, and compare scenarios on paired seeds."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from .config import ScenarioConfig, config_hash
from .metrics import NightMetrics, build_trace, compute_metrics
from .model import Night

HEADLINE = ("waste_total", "stockout_min_total", "missed_first_choice_rate", "wait_p90", "missed_meal")
WIN_THRESHOLD = 0.6  # a change is "better" only if it wins in >= 60% of nights


def min_meaningful(metric: str, baseline_median: float) -> float:
    """Smallest median paired difference worth a verdict (ISSUES C1); below it the verdict is "same"."""
    return {
        "waste_total": max(5.0, 0.01 * abs(baseline_median)),  # portions
        "stockout_min_total": 1.0,  # minutes
        "missed_first_choice_rate": 0.005,  # half a percentage point
        "wait_p90": 0.5,  # minutes
        "missed_meal": max(2.0, 0.01 * abs(baseline_median)),  # students
    }.get(metric, 0.0)


@dataclass
class NightResult:
    metrics: NightMetrics
    trace: dict | None = None


def run_night(cfg: ScenarioConfig, seed: int, trace: bool = False) -> NightResult:
    night = Night(cfg, seed, trace=trace).run()
    return NightResult(compute_metrics(night), build_trace(night) if trace else None)


def _night_metrics(args: tuple[ScenarioConfig, int]) -> NightMetrics:
    cfg, seed = args
    return run_night(cfg, seed).metrics


def run_nights(cfg: ScenarioConfig, seeds: list[int], workers: int | None = None) -> list[NightMetrics]:
    workers = workers if workers is not None else min(len(seeds), os.cpu_count() or 1)
    if workers <= 1 or len(seeds) <= 1:
        return [_night_metrics((cfg, s)) for s in seeds]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_night_metrics, [(cfg, s) for s in seeds]))


@dataclass
class ScenarioResult:
    config_hash: str
    label: str
    nights: list[NightMetrics]
    summary: dict = field(default_factory=dict)

    @property
    def seeds(self) -> list[int]:
        return [n.seed for n in self.nights]


def run_scenario(cfg: ScenarioConfig, n_nights: int = 30, workers: int | None = None, first_seed: int = 1) -> ScenarioResult:
    seeds = list(range(first_seed, first_seed + n_nights))
    nights = run_nights(cfg, seeds, workers)
    return ScenarioResult(config_hash(cfg), cfg.label, nights, summarize(nights))


# ---------------------------------------------------------------- aggregation


def _pct(values: list[float] | np.ndarray) -> dict[str, float]:
    a = np.asarray(values, dtype=float)
    p10, p50, p90 = np.percentile(a, [10, 50, 90])
    return {"p10": round(float(p10), 3), "p50": round(float(p50), 3), "p90": round(float(p90), 3)}


def summarize(nights: list[NightMetrics]) -> dict:
    n = len(nights)
    headline = {k: _pct([getattr(m, k) for m in nights]) for k in HEADLINE}
    headline["students"] = _pct([m.students for m in nights])
    dishes = {}
    for dish_id in nights[0].dishes:
        rows = [m.dishes[dish_id] for m in nights]
        runouts = [r.runout_min for r in rows if r.runout_min is not None]
        dishes[dish_id] = {
            "cooked": _pct([r.cooked for r in rows]),
            "served": _pct([r.served for r in rows]),
            "waste": _pct([r.waste for r in rows]),
            "expired": _pct([r.expired for r in rows]),
            "leftover": _pct([r.leftover for r in rows]),
            "stockout_min": _pct([r.stockout_min for r in rows]),
            "runout_share": round(len(runouts) / n, 3),
            "runout_min": _pct(runouts) if runouts else None,
            "first_choice_demand": _pct([r.first_choice_demand for r in rows]),
        }
    stations = {sid: _pct([m.max_queue[sid] for m in nights]) for sid in nights[0].max_queue}
    # Kitchen capacity, empty-pan waits and seats (engine 0.5.0); servers needed per station and hour (N3)
    for k in ("batch_wait_min", "cook_busy_share", "empty_wait_min", "seat_wait_min", "peak_seated"):
        if hasattr(nights[0], k):
            headline[k] = _pct([getattr(m, k) for m in nights])
    staffing: dict[str, dict[str, int]] = {}
    for sid in {sid for m in nights for sid in m.servers_needed}:
        hours = {h for m in nights for h in m.servers_needed.get(sid, {})}
        staffing[sid] = {h: int(np.median([m.servers_needed.get(sid, {}).get(h, 0) for m in nights])) for h in sorted(hours, key=int)}
    return {"n_nights": n, "headline": headline, "dishes": dishes, "max_queue": stations, "staffing": staffing}


def _night_series(args: tuple[ScenarioConfig, int]) -> dict:
    from .metrics import night_series

    cfg, seed = args
    return night_series(Night(cfg, seed).run())


def series_summary(cfg: ScenarioConfig, seeds: list[int], workers: int | None = None) -> dict:
    """Per-minute p10/p50/p90 across nights of line length per station, portions on the line per
    dish and students inside (IMPROVEMENTS 1.4). Re-runs the nights (series aren't cached)."""
    workers = workers if workers is not None else min(len(seeds), os.cpu_count() or 1)
    args = [(cfg, s) for s in seeds]
    if workers <= 1 or len(seeds) <= 1:
        per = [_night_series(a) for a in args]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            per = list(pool.map(_night_series, args))

    def band(rows: list[list[int]]) -> dict[str, list[float]]:
        a = np.asarray(rows, dtype=float)
        p10, p50, p90 = np.percentile(a, [10, 50, 90], axis=0)
        return {"p10": np.round(p10, 1).tolist(), "p50": np.round(p50, 1).tolist(), "p90": np.round(p90, 1).tolist()}

    first = per[0]
    return {
        "t0": first["t0"], "step": first["step"], "n_nights": len(per),
        "inside": band([p["inside"] for p in per]),
        "queue": {k: band([p["queue"][k] for p in per]) for k in first["queue"]},
        "pans": {k: band([p["pans"][k] for p in per]) for k in first["pans"]},
    }


def compare(baseline: ScenarioResult, scenario: ScenarioResult) -> dict:
    """Paired comparison: same seeds, so per-night differences come from the change."""
    base = {m.seed: m for m in baseline.nights}
    pairs = [(base[m.seed], m) for m in scenario.nights if m.seed in base]
    if not pairs:
        raise ValueError("no common seeds between baseline and scenario")
    out = {"n_pairs": len(pairs), "metrics": {}}
    for k in HEADLINE:  # lower is better for every headline metric
        diffs = np.array([getattr(s, k) - getattr(b, k) for b, s in pairs], dtype=float)
        win = float(np.mean(diffs < 0))
        lose = float(np.mean(diffs > 0))
        med = float(np.median(diffs))
        floor = min_meaningful(k, float(np.median([getattr(b, k) for b, _ in pairs])))
        if abs(med) < floor:
            verdict = "same"  # a real but too-small difference (e.g. 0.1 min of wait) isn't "worse"
        elif win >= WIN_THRESHOLD and med < 0:
            verdict = "better"
        elif lose >= WIN_THRESHOLD and med > 0:
            verdict = "worse"
        else:
            verdict = "inconclusive"
        out["metrics"][k] = {"diff": _pct(diffs), "win_rate": round(win, 3), "verdict": verdict,
                             "min_meaningful": round(floor, 4)}
    return out


def median_night(nights: list[NightMetrics], key: str = "waste_total") -> int:
    """Seed of the most typical night (closest to the median of `key`), for 3D playback."""
    values = np.array([getattr(m, key) for m in nights], dtype=float)
    return nights[int(np.argmin(np.abs(values - np.median(values))))].seed
