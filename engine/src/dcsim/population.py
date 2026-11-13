"""Pre-drawn student population for one night (scenario-independent randomness).

Each arriving student gets a persona, personal attributes and a full ranking of
eligible dishes. Ranking by log(weight) + Gumbel noise is exactly the logit
choice model P(i) = w_i / sum_j w_j, and the full order gives substitution for
free: when a dish is unavailable, take the next one on the list.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np # pyright: ignore[reportMissingImports]

from .config import Dish, ScenarioConfig
from .rng import Stream, stable_key, std_normal, uniform

MAX_SERVICES = 8  # pre-drawn service-time slots per student


@dataclass(slots=True)
class Student:
    id: int
    arrive: float
    persona: int
    diet: str
    patience: int
    plate_size: int
    wants_seconds: bool
    dwell: float
    prefs: list[int]  # eligible dish indices, best first
    svc_z: list[float]  # standard-normal draws for service times


def eligible(diet: str, dish: Dish) -> bool:
    tags = set(dish.diet_tags)
    if diet == "vegan":
        return "vegan" in tags
    if diet == "vegetarian":
        return bool(tags & {"vegetarian", "vegan"})
    if diet == "halal":  # UMass labels Halal explicitly; plant-based items are also fine
        return bool(tags & {"halal", "vegan"})
    return True


def _categorical(u: np.ndarray, pairs: tuple[tuple[int, float], ...]) -> np.ndarray:
    values = np.array([v for v, _ in pairs])
    cum = np.cumsum([p for _, p in pairs])
    idx = np.minimum(np.searchsorted(cum, u * cum[-1], side="right"), len(values) - 1)
    return values[idx]


def build_students(cfg: ScenarioConfig, seed: int, ids: np.ndarray, times: np.ndarray) -> list[Student]:
    n = len(ids)
    if n == 0:
        return []
    personas, menu = cfg.personas, cfg.menu

    cum_share = np.cumsum([p.share for p in personas])
    u_persona = uniform(seed, Stream.PERSONA, ids)
    pidx = np.minimum(np.searchsorted(cum_share, u_persona * cum_share[-1], side="right"), len(personas) - 1)

    u_pat = uniform(seed, Stream.PATIENCE, ids)
    u_plate = uniform(seed, Stream.PLATE, ids)
    u_sec = uniform(seed, Stream.SECONDS, ids)
    z_dwell = std_normal(seed, Stream.DWELL, ids)

    patience = np.zeros(n, dtype=int)
    plate = np.ones(n, dtype=int)
    wants = np.zeros(n, dtype=bool)
    dwell = np.zeros(n)
    for k, p in enumerate(personas):
        m = pidx == k
        if not m.any():
            continue
        patience[m] = _categorical(u_pat[m], p.patience)
        plate[m] = _categorical(u_plate[m], p.plate_size)
        wants[m] = u_sec[m] < p.seconds_prob
        dwell[m] = p.dwell_median_min * np.exp(p.dwell_sigma * z_dwell[m])

    # Taste: one Gumbel draw per (student, dish id), independent of the rest of the menu.
    dish_keys = np.array([stable_key(d.id) for d in menu], dtype=np.uint64)
    gumbel = -np.log(-np.log(uniform(seed, Stream.TASTE, ids[:, None], dish_keys[None, :])))
    log_w = np.array([[math.log(d.popularity * p.affinity(d)) for d in menu] for p in personas])
    elig = np.array([[eligible(p.diet, d) for d in menu] for p in personas])
    util = np.where(elig[pidx], log_w[pidx] + gumbel, -np.inf)
    order = np.argsort(-util, axis=1, kind="stable")
    n_elig = elig[pidx].sum(axis=1)

    svc_z = std_normal(seed, Stream.SERVICE, ids[:, None], np.arange(MAX_SERVICES)[None, :])

    return [
        Student(
            id=int(ids[i]),
            arrive=float(times[i]),
            persona=int(pidx[i]),
            diet=personas[int(pidx[i])].diet,
            patience=int(patience[i]),
            plate_size=int(plate[i]),
            wants_seconds=bool(wants[i]),
            dwell=float(dwell[i]),
            prefs=order[i, : n_elig[i]].tolist(),
            svc_z=svc_z[i].tolist(),
        )
        for i in range(n)
    ]
