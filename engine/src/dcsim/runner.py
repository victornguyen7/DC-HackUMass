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
    return {"n_nights": n, "headline": headline, "dishes": dishes, "max_queue": stations}


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
        if win >= WIN_THRESHOLD and med < 0:
            verdict = "better"
        elif lose >= WIN_THRESHOLD and med > 0:
            verdict = "worse"
        else:
            verdict = "inconclusive"
        out["metrics"][k] = {"diff": _pct(diffs), "win_rate": round(win, 3), "verdict": verdict}
    return out


def median_night(nights: list[NightMetrics], key: str = "waste_total") -> int:
    """Seed of the most typical night (closest to the median of `key`), for 3D playback."""
    values = np.array([getattr(m, key) for m in nights], dtype=float)
    return nights[int(np.argmin(np.abs(values - np.median(values))))].seed
