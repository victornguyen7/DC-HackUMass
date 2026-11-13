"""Reduce one night's recorder logs to a small NightMetrics summary (and optional trace)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .model import Night
from .recorder import VESSELS, Ev, PanEv


@dataclass(slots=True)
class DishNight:
    to_order: bool
    cooked: int
    served: int
    expired: int
    leftover: int
    waste: int
    stockout_min: float
    runout_min: float | None  # first time the pan hit 0 during service
    first_choice_demand: int


@dataclass(slots=True)
class NightMetrics:
    seed: int
    students: int
    waste_total: int
    stockout_min_total: float
    missed_first_choice_rate: float  # share who never got their first choice all visit
    missed_meal: int  # got nothing, or wanted a hot dish and left with no hot food
    wait_p50: float
    wait_p90: float
    balks: int
    empty_seen: int
    dishes: dict[str, DishNight]
    max_queue: dict[str, int]
    arrivals_per_10min: list[int]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> NightMetrics:
        return cls(**(d | {"dishes": {k: DishNight(**v) for k, v in d["dishes"].items()}}))


def _stockout(changes: list[tuple[float, int]], open_min: float, close_min: float) -> tuple[float, float | None]:
    total, level, last, runout = 0.0, 0, float(open_min), None
    for k, (t, lvl) in enumerate(changes):
        if k + 1 < len(changes) and changes[k + 1][0] == t:
            continue  # several changes at one instant: only the final level counts
        tt = min(max(t, open_min), close_min)
        if level == 0:
            total += tt - last
        last = tt
        if lvl == 0 and level > 0 and runout is None and open_min <= t < close_min:
            runout = t
        level = lvl
    if level == 0:
        total += close_min - last
    return total, runout


def compute_metrics(night: Night) -> NightMetrics:
    cfg, rec = night.cfg, night.rec
    o, c = cfg.open_min, cfg.close_min
    outcomes = rec.outcomes

    first_counts = np.zeros(len(cfg.menu), dtype=int)
    for oc in outcomes:
        if oc.first_choice >= 0:
            first_counts[oc.first_choice] += 1

    dishes = {}
    for i, d in enumerate(cfg.menu):
        pan = night.pans[i]
        so, runout = (0.0, None) if pan.to_order else _stockout(rec.pan_levels[i], o, c)
        dishes[d.id] = DishNight(
            to_order=pan.to_order,
            cooked=pan.cooked,
            served=pan.served,
            expired=pan.expired,
            leftover=pan.leftover,
            waste=pan.expired + pan.leftover,
            stockout_min=round(so, 2),
            runout_min=None if runout is None else round(runout, 2),
            first_choice_demand=int(first_counts[i]),
        )

    waits = np.array([oc.plate_done - oc.arrive for oc in outcomes]) if outcomes else np.zeros(1)
    arrivals = np.array([s.arrive for s in night.students])
    bins = np.arange(o, c + 10, 10)
    hist = np.histogram(arrivals, bins=bins)[0].tolist() if len(arrivals) else [0] * (len(bins) - 1)

    return NightMetrics(
        seed=night.seed,
        students=len(night.students),
        waste_total=sum(x.waste for x in dishes.values()),
        stockout_min_total=round(sum(x.stockout_min for x in dishes.values()), 2),
        missed_first_choice_rate=round(float(np.mean([oc.missed_first for oc in outcomes])) if outcomes else 0.0, 4),
        missed_meal=sum(1 for oc in outcomes if oc.items == 0 or (oc.wanted_hot and not oc.got_hot)),
        wait_p50=round(float(np.percentile(waits, 50)), 2),
        wait_p90=round(float(np.percentile(waits, 90)), 2),
        balks=rec.balks,
        empty_seen=rec.empty_seen,
        dishes=dishes,
        max_queue={s.id: max((n for _, n in rec.queue_lens[i]), default=0) for i, s in enumerate(cfg.stations)},
        arrivals_per_10min=hist,
    )


def build_trace(night: Night) -> dict:
    """Per-student event lists + pan events for 3D playback (SPEC §8.3)."""
    cfg, rec = night.cfg, night.rec
    stations = [s.id for s in cfg.stations]
    dishes = [d.id for d in cfg.menu]
    zones = cfg.layout.zones()
    by_student: dict[int, list[dict]] = {}
    for t, sid, code, st, dish, vessel, line in rec.events:
        e: dict = {"t": round(t, 3), "type": Ev(code).name.lower()}
        if st >= 0:
            e["station"] = stations[st]
        if dish >= 0:
            e["dish"] = dishes[dish]
        if vessel >= 0:
            e["vessel"] = VESSELS[vessel]
        if line >= 0:
            e["line"] = line
        if code == Ev.SIT:
            e["seat"] = zones[cfg.layout.seat_zone(sid)].id  # zone id; per-table seats later
        by_student.setdefault(sid, []).append(e)
    students = [
        {"id": s.id, "persona": cfg.personas[s.persona].id, "diet": s.diet, "events": by_student.get(s.id, [])}
        for s in night.students
    ]
    pans = [
        {"t": round(t, 3), "dish": dishes[d], "portions": lvl, "event": PanEv(code).name.lower()}
        for t, d, lvl, code in rec.pan_events
        if code != PanEv.TAKE
    ]
    return {"night_seed": night.seed, "students": students, "pans": pans}
