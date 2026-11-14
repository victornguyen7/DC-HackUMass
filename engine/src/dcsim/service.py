"""Cache-aware orchestration shared by the API and the CLI.

    cfg = build_config(ScenarioRequest(date=..., preset=..., levers=...))
    nights = svc.ensure_nights(cfg, 30)          # cached seeds reused, missing ones run
    result = svc.result(cfg, nights)             # summary + cook sheet + flags (+ vs baseline)

Seeds always run 1..N in order (common random numbers), so a scenario and its
baseline are compared night by night, and 30 -> 60 nights only adds 31..60.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor

from .config import ENGINE_VERSION, ScenarioConfig, config_hash
from .data import load_baseline
from .metrics import NightMetrics
from .reports import arrivals, cook_sheet, flags, stations
from .runner import ScenarioResult, _night_metrics, compare, median_night, run_night, summarize
from .store import Store

BATCH = 8  # nights per progress update (ENGINE.md §6.3)


def seeds_for(n: int) -> list[int]:
    return list(range(1, n + 1))


def baseline_of(cfg: ScenarioConfig) -> ScenarioConfig:
    return load_baseline(cfg.date)


class Service:
    def __init__(self, store: Store, pool: Executor | None = None):
        self.store = store
        self.pool = pool

    # ------------------------------------------------------------ nights

    def batches(self, cfg: ScenarioConfig, n: int) -> list[list[int]]:
        h = self.store.put_config(cfg)
        missing = self.store.missing_seeds(h, seeds_for(n))
        return [missing[i : i + BATCH] for i in range(0, len(missing), BATCH)]

    def run_batch(self, cfg: ScenarioConfig, seeds: list[int]) -> list[NightMetrics]:
        """Run and store one batch (blocking). Uses the process pool when there is one."""
        args = [(cfg, s) for s in seeds]
        nights = list(self.pool.map(_night_metrics, args)) if self.pool else [_night_metrics(a) for a in args]
        self.store.put_nights(config_hash(cfg), nights)
        return nights

    def ensure_nights(
        self, cfg: ScenarioConfig, n: int, on_batch: Callable[[int, int], None] | None = None
    ) -> list[NightMetrics]:
        todo = self.batches(cfg, n)
        total, done = sum(len(b) for b in todo), 0
        for b in todo:
            self.run_batch(cfg, b)
            done += len(b)
            if on_batch:
                on_batch(done, total)
        return self.cached_nights(cfg, n)

    def cached_nights(self, cfg: ScenarioConfig | str, n: int) -> list[NightMetrics]:
        h = cfg if isinstance(cfg, str) else config_hash(cfg)
        return self.store.get_nights(h, seeds_for(n))

    def is_cached(self, cfg: ScenarioConfig, n: int) -> bool:
        return not self.store.missing_seeds(config_hash(cfg), seeds_for(n))

    # ------------------------------------------------------------ results

    def result(
        self, cfg: ScenarioConfig, nights: list[NightMetrics], base_nights: list[NightMetrics] | None = None,
        *, provisional: bool = False,
    ) -> dict:
        h = config_hash(cfg)
        summary = summarize(nights)
        out = {
            "config_hash": h,
            "label": cfg.label,
            "date": cfg.date,
            "meal": cfg.meal,
            "n_nights": len(nights),
            "provisional": provisional,
            "engine_version": ENGINE_VERSION,
            "data_version": cfg.data_version,
            "population_status": cfg.population_status,
            "open": cfg.open_min,
            "close": cfg.service_end,  # serving stops (end of late night when there is one)
            "dinner_close": cfg.close_min,
            "late_close": cfg.late_close_min,
            "headline": summary["headline"],
            "cook_sheet": cook_sheet(cfg, summary),
            "flags": flags(cfg, summary),
            "stations": stations(cfg, summary),
            "arrivals": arrivals(cfg),
            "median_seed": median_night(nights),
        }
        base = baseline_of(cfg)
        bh = config_hash(base)
        out["baseline_hash"] = bh
        if base_nights and bh != h:
            out["vs_baseline"] = compare(
                ScenarioResult(bh, base.label, base_nights), ScenarioResult(h, cfg.label, nights)
            )
        return out

    def compare_hashes(self, baseline_hash: str, scenario_hash: str, n: int) -> dict:
        b, s = self.cached_nights(baseline_hash, n), self.cached_nights(scenario_hash, n)
        if not b or not s:
            raise KeyError("run both configs first (POST /api/simulate)")
        return compare(ScenarioResult(baseline_hash, "baseline", b), ScenarioResult(scenario_hash, "scenario", s))

    # ------------------------------------------------------------ traces

    def trace_bytes(self, h: str, seed: int) -> bytes | None:
        """Gzipped JSON trace of one night; computed once (one extra night) and kept on disk."""
        data = self.store.get_trace_bytes(h, seed)
        if data is None:
            cfg = self.store.get_config(h)
            if cfg is None:
                return None
            self.store.put_trace(h, seed, run_night(cfg, seed, trace=True).trace)
            data = self.store.get_trace_bytes(h, seed)
        return data
