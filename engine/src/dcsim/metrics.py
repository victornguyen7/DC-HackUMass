"""Reduce one night's recorder logs to a small NightMetrics summary (and optional trace)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

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
    balks: int  # lines skipped as too long, seen from where the student stood
    empty_seen: int
    dishes: dict[str, DishNight]
    max_queue: dict[str, int]
    arrivals_per_10min: list[int]  # open .. end of service (late night included)
    late_night_students: int = 0  # arrived after dinner closed
    batch_wait_min: float = 0.0  # minutes batches waited for a cook or equipment (kitchen capacity)
    cook_busy_share: float = 0.0  # cooks' hands-on prep minutes / cook-minutes on shift
    equipment_peak: dict[str, int] = field(default_factory=dict)  # most units of each equipment in use at once
    empty_wait_min: float = 0.0  # minutes students waited at an empty pan for a batch
    seat_wait_min: float = 0.0  # minutes students stood waiting for a seat
    peak_seated: int = 0
    swipe_wait_min: float = 0.0  # minutes waited at the entrance card readers
    swipe_queue_max: int = 0
    group_students: int = 0  # students who came with friends
    allergy_students: int = 0  # students with at least one allergy (survey shares)
    allergy_no_meal: int = 0  # of those, how many left with nothing they could eat
    servers_needed: dict[str, dict[str, int]] = field(default_factory=dict)  # station -> hour ("17") -> servers for 85% busy
    in_hall_before_close: int = 0  # students inside 30 min before serving stops (11:30 pm)

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


TARGET_SERVER_BUSY = 0.85  # size a line so its servers are busy at most 85% of the hour


def _servers_needed(cfg, rec) -> dict[str, dict[str, int]]:
    """Servers per station and hour so they'd be busy <= 85% with the items actually served (N3).
    Uses the mean lognormal service time per item; a rough sizing, not a queueing optimum."""
    out: dict[str, dict[str, int]] = {}
    for (i, hour), items in sorted(rec.served_by_hour.items()):
        st = cfg.stations[i]
        mean_min = st.service_median_min * float(np.exp(st.service_sigma ** 2 / 2))
        out.setdefault(st.id, {})[str(hour)] = max(1, int(np.ceil(items * mean_min / 60 / TARGET_SERVER_BUSY)))
    return out


def _allergy_no_meal(night: Night) -> int:
    allergic = {s.id for s in night.students if s.allergies}
    return sum(1 for oc in night.rec.outcomes if oc.student in allergic and oc.items == 0)


def _cook_share(cfg, rec) -> float:
    kr = cfg.kitchen_resources
    if kr is None:
        return 0.0
    on_shift = sum(n * (cfg.window(p)[1] - cfg.window(p)[0]) for p, n in kr.cooks.items()
                   if p == "dinner" or cfg.late_close_min is not None)
    return sum(rec.cook_busy_min.values()) / on_shift if on_shift else 0.0


def compute_metrics(night: Night) -> NightMetrics:
    cfg, rec = night.cfg, night.rec
    o, c = cfg.open_min, cfg.service_end
    outcomes = rec.outcomes

    first_counts = np.zeros(len(cfg.menu), dtype=int)
    for oc in outcomes:
        if oc.first_choice >= 0:
            first_counts[oc.first_choice] += 1

    dishes = {}
    for i, d in enumerate(cfg.menu):
        pan = night.pans[i]
        so, runout = (0.0, None) if pan.to_order else _stockout(rec.pan_levels[i], *cfg.window(d.period))
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
        late_night_students=sum(1 for s in night.students if cfg.late_close_min is not None and s.arrive >= cfg.close_min),
        batch_wait_min=round(rec.batch_wait_min, 2),
        cook_busy_share=round(_cook_share(cfg, rec), 4),
        equipment_peak=dict(rec.equipment_peak),
        empty_wait_min=round(rec.empty_wait_min, 2),
        seat_wait_min=round(rec.seat_wait_min, 2),
        peak_seated=rec.peak_seated,
        swipe_wait_min=round(rec.swipe_wait_min, 2),
        swipe_queue_max=rec.swipe_queue_max,
        group_students=sum(1 for s in night.students if s.group >= 0),
        allergy_students=sum(1 for s in night.students if s.allergies),
        allergy_no_meal=_allergy_no_meal(night),
        servers_needed=_servers_needed(cfg, rec),
        in_hall_before_close=sum(1 for oc in outcomes if oc.arrive <= c - 30 < oc.exit),
    )


SERIES_STEP_MIN = 1


def _step_values(changes: list[tuple[float, int]], grid: np.ndarray) -> np.ndarray:
    """Value of a step function (logged as changes) at each grid time: the last change at or before it."""
    if not changes:
        return np.zeros(len(grid), dtype=int)
    t = np.array([c[0] for c in changes])
    v = np.array([c[1] for c in changes])
    idx = np.searchsorted(t, grid, side="right") - 1
    return np.where(idx >= 0, v[np.maximum(idx, 0)], 0)


def night_series(night: Night, step: int = SERIES_STEP_MIN) -> dict:
    """Per-minute series for one night (IMPROVEMENTS 1.4), sampled from the recorder's exact step
    functions: line length per station, portions on the line per batch-cooked dish, students inside.
    Not stored with cached nights (too big); `runner.series_summary` builds them on request."""
    cfg, rec = night.cfg, night.rec
    grid = np.arange(cfg.open_min, cfg.service_end + 30 + step, step, dtype=float)
    arrive = np.array([o.arrive for o in rec.outcomes])
    leave = np.array([o.exit for o in rec.outcomes])
    inside = ((arrive[None, :] <= grid[:, None]) & (grid[:, None] < leave[None, :])).sum(axis=1) if len(arrive) else np.zeros(len(grid), dtype=int)
    return {
        "t0": int(grid[0]), "step": step,
        "inside": inside.astype(int).tolist(),
        "queue": {s.id: _step_values(rec.queue_lens[i], grid).tolist() for i, s in enumerate(cfg.stations)},
        "pans": {d.id: _step_values(rec.pan_levels[i], grid).tolist() for i, d in enumerate(cfg.menu) if d.id in cfg.kitchen},
    }


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
        if line >= 0 and code != Ev.SIT:
            e["line"] = line
        if code == Ev.SIT:  # SIT carries the zone actually used (seat limits may move a student)
            e["seat"] = zones[line if line >= 0 else cfg.layout.seat_zone(sid)].id
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
