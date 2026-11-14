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
    allergies: tuple[str, ...] = ()  # allergy ids (ScenarioConfig.allergies)
    group: int = -1  # id of the group's first member, -1 if alone
    seat_key: int = -1  # id used to pick the seat zone (the group's first member: they sit together)


def eligible(diet: str, dish: Dish) -> bool:
    tags = set(dish.diet_tags)
    if diet == "vegan":
        return "vegan" in tags
    if diet == "vegetarian":
        return bool(tags & {"vegetarian", "vegan"})
    if diet == "pescetarian":  # vegetarian dishes, plus fish/seafood with no meat (tag set by data.py)
        return bool(tags & {"vegetarian", "vegan", "pescetarian"})
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

    # Late night is a snack: late arrivals draw their plate size from late_plate_size (same uniform draw).
    if cfg.late_plate_size is not None and cfg.late_close_min is not None:
        late_m = times >= cfg.close_min
        if late_m.any():
            plate[late_m] = _categorical(u_plate[late_m], cfg.late_plate_size)

    # Taste: one Gumbel draw per (student, dish id), independent of the rest of the menu.
    dish_keys = np.array([stable_key(d.id) for d in menu], dtype=np.uint64)
    gumbel = -np.log(-np.log(uniform(seed, Stream.TASTE, ids[:, None], dish_keys[None, :])))
    log_w = np.array([[math.log(d.popularity * p.affinity(d)) for d in menu] for p in personas])
    elig = np.array([[eligible(p.diet, d) for d in menu] for p in personas])
    # A student ranks only the dishes of the service period they arrive in (dinner or late night).
    late = np.array([d.period == "late_night" for d in menu])
    arrive_late = (times >= cfg.close_min) if cfg.late_close_min is not None else np.zeros(n, dtype=bool)
    in_period = late[None, :] == arrive_late[:, None]
    ok = elig[pidx] & in_period
    # Allergies (ISSUES N8): each student independently has each allergy at its survey share and
    # never picks a dish labeled with it. Keyed by (student, allergy id), so menus don't reshuffle it.
    has = np.zeros((n, len(cfg.allergies)), dtype=bool)
    if cfg.allergies:
        akeys = np.array([stable_key(a.id) for a in cfg.allergies], dtype=np.uint64)
        shares = np.array([a.share for a in cfg.allergies])
        has = uniform(seed, Stream.ALLERGY, ids[:, None], akeys[None, :]) < shares[None, :]
        avoid = np.array([[bool(set(a.labels) & set(d.allergens)) for d in menu] for a in cfg.allergies])
        ok &= ~(has.astype(int) @ avoid.astype(int)).astype(bool)
    util = np.where(ok, log_w[pidx] + gumbel, -np.inf)
    order = np.argsort(-util, axis=1, kind="stable")
    n_elig = ok.sum(axis=1)

    svc_z = std_normal(seed, Stream.SERVICE, ids[:, None], np.arange(MAX_SERVICES)[None, :])

    students = [
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
            allergies=tuple(cfg.allergies[k].id for k in np.nonzero(has[i])[0]),
        )
        for i in range(n)
    ]
    for st in students:
        st.seat_key = st.id
    if cfg.groups is not None:
        _form_groups(cfg, seed, students, ids)
    return students


def _form_groups(cfg: ScenarioConfig, seed: int, students: list[Student], ids: np.ndarray) -> None:
    """Friends arrive together (the first member's time) and sit in the same zone. Uses its own
    random stream keyed by student id, so groups don't move other draws."""
    g = cfg.groups
    pid = next((k for k, p in enumerate(cfg.personas) if p.id == g.persona), None)
    if pid is None:
        return
    u_join = uniform(seed, Stream.GROUP, ids)
    u_size = uniform(seed, Stream.GROUP, ids, np.full(len(ids), 1, dtype=np.uint64))
    pool = sorted((st for k, st in enumerate(students) if st.persona == pid and u_join[k] < g.share), key=lambda st: st.arrive)
    index = {st.id: k for k, st in enumerate(students)}
    i = 0
    while i < len(pool):
        lead = pool[i]
        size = int(_categorical(np.array([u_size[index[lead.id]]]), g.sizes)[0])
        members = [lead]
        j = i + 1
        while j < len(pool) and len(members) < size and pool[j].arrive - lead.arrive <= g.window_min \
                and (pool[j].arrive >= cfg.close_min) == (lead.arrive >= cfg.close_min):  # same service period
            members.append(pool[j])
            j += 1
        if len(members) > 1:
            for m in members:
                m.arrive, m.group, m.seat_key = lead.arrive, lead.id, lead.id
        i = j
    students.sort(key=lambda st: (st.arrive, st.id))
