"""Arrival model: a time-varying Poisson process made of class-change waves.

Each service period (dinner, then late night if the scenario has one) has its
own crowd, normalized over its own window and sampled from its own random
stream, so changing the late-night crowd never moves a dinner arrival.

Sampling uses thinning from a fixed candidate pool. Candidates depend only on
the seed and `arrival_cap_per_min`, so a +10% crowd keeps every student of the
baseline and adds new ones, instead of reshuffling the whole night.
"""

from __future__ import annotations

import numpy as np

from .config import Crowd, ScenarioConfig
from .rng import Stream

# Late-night candidate ids start here, so they never collide with dinner ids.
LATE_ID_OFFSET = 1 << 40


def _segments(cfg: ScenarioConfig) -> list[tuple[Crowd, int, int, int, int]]:
    """(crowd, start, end, stream sub-key, id offset) per service period."""
    segs = [(cfg.crowd, cfg.open_min, cfg.close_min, 0, 0)]
    if cfg.late_crowd is not None and cfg.late_close_min is not None:
        segs.append((cfg.late_crowd, cfg.close_min, cfg.late_close_min, 1, LATE_ID_OFFSET))
    return segs


def _shape(t: np.ndarray, crowd: Crowd, start: float, end: float) -> np.ndarray:
    s = np.full(np.shape(t), crowd.base_share / (end - start), dtype=float)
    for w in crowd.waves:
        mu = w.peak_min + crowd.shift_min
        s += w.share * np.exp(-0.5 * ((t - mu) / w.sd_min) ** 2) / (w.sd_min * np.sqrt(2 * np.pi))
    return s


def _intensity(crowd: Crowd, start: float, end: float, t: np.ndarray) -> np.ndarray:
    t = np.asarray(t, dtype=float)
    grid = np.linspace(start, end, 2001)
    z = np.trapezoid(_shape(grid, crowd, start, end), grid)
    if z <= 0:
        return np.zeros(np.shape(t))
    lam = crowd.expected_students * crowd.multiplier * _shape(t, crowd, start, end) / z
    return np.where((t >= start) & (t < end), lam, 0.0)


def intensity(cfg: ScenarioConfig, t: np.ndarray) -> np.ndarray:
    """Expected arrivals per minute at times t (minutes since midnight), all periods."""
    t = np.asarray(t, dtype=float)
    out = np.zeros(np.shape(t))
    for crowd, start, end, _, _ in _segments(cfg):
        out = out + _intensity(crowd, start, end, t)
    return out


def expected_arrivals(cfg: ScenarioConfig, window_min: int = 10) -> list[tuple[int, float]]:
    """(window_start, expected students) per window, for the dashboard chart."""
    out = []
    end = cfg.service_end
    for start in range(cfg.open_min, end, window_min):
        stop = min(start + window_min, end)
        grid = np.linspace(start, stop - 1e-9, 51)
        out.append((start, float(np.trapezoid(intensity(cfg, grid), grid))))
    return out


def sample_arrivals(cfg: ScenarioConfig, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (candidate ids, arrival times) of the students who arrive on this night."""
    cap = cfg.arrival_cap_per_min
    ids, times = [], []
    for crowd, start, end, sub, offset in _segments(cfg):
        # Dinner keeps the original stream key, so its arrivals are unchanged by late night.
        key = [seed, int(Stream.ARRIVAL)] + ([sub] if sub else [])
        rng = np.random.default_rng(key)
        n = rng.poisson(cap * (end - start))
        t = np.sort(rng.uniform(start, end, n))
        u = rng.uniform(0.0, 1.0, n)
        lam = _intensity(crowd, start, end, t)
        if n and lam.max() > cap:
            raise ValueError(
                f"arrival intensity {lam.max():.1f}/min exceeds arrival_cap_per_min={cap}; raise the cap"
            )
        keep = u * cap < lam
        ids.append(np.nonzero(keep)[0].astype(np.uint64) + np.uint64(offset))
        times.append(t[keep])
    return np.concatenate(ids), np.concatenate(times)
