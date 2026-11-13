"""Arrival model: a time-varying Poisson process made of class-change waves.

Sampling uses thinning from a fixed candidate pool. Candidates depend only on
the seed and `arrival_cap_per_min`, so a +10% crowd keeps every student of the
baseline and adds new ones, instead of reshuffling the whole night.
"""

from __future__ import annotations

import numpy as np

from .config import ScenarioConfig
from .rng import Stream


def _shape(t: np.ndarray, cfg: ScenarioConfig) -> np.ndarray:
    crowd = cfg.crowd
    duration = cfg.close_min - cfg.open_min
    s = np.full(np.shape(t), crowd.base_share / duration, dtype=float)
    for w in crowd.waves:
        mu = w.peak_min + crowd.shift_min
        s += w.share * np.exp(-0.5 * ((t - mu) / w.sd_min) ** 2) / (w.sd_min * np.sqrt(2 * np.pi))
    return s


def intensity(cfg: ScenarioConfig, t: np.ndarray) -> np.ndarray:
    """Expected arrivals per minute at times t (minutes since midnight)."""
    grid = np.linspace(cfg.open_min, cfg.close_min, 2001)
    z = np.trapezoid(_shape(grid, cfg), grid)
    if z <= 0:
        return np.zeros(np.shape(t))
    total = cfg.crowd.expected_students * cfg.crowd.multiplier
    return total * _shape(np.asarray(t, dtype=float), cfg) / z


def expected_arrivals(cfg: ScenarioConfig, window_min: int = 10) -> list[tuple[int, float]]:
    """(window_start, expected students) per window, for the dashboard chart."""
    out = []
    for start in range(cfg.open_min, cfg.close_min, window_min):
        end = min(start + window_min, cfg.close_min)
        grid = np.linspace(start, end, 51)
        out.append((start, float(np.trapezoid(intensity(cfg, grid), grid))))
    return out


def sample_arrivals(cfg: ScenarioConfig, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (candidate ids, arrival times) of the students who arrive on this night."""
    rng = np.random.default_rng([seed, int(Stream.ARRIVAL)])
    cap = cfg.arrival_cap_per_min
    n = rng.poisson(cap * (cfg.close_min - cfg.open_min))
    t = np.sort(rng.uniform(cfg.open_min, cfg.close_min, n))
    u = rng.uniform(0.0, 1.0, n)
    lam = intensity(cfg, t)
    if n and lam.max() > cap:
        raise ValueError(
            f"arrival intensity {lam.max():.1f}/min exceeds arrival_cap_per_min={cap}; raise the cap"
        )
    keep = u * cap < lam
    return np.nonzero(keep)[0].astype(np.uint64), t[keep]
