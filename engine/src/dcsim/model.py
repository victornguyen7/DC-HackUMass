"""One simulated dinner: SimPy processes for students, stations, pans and the kitchen.

Time is minutes since midnight. The kitchen is event-driven: taking a portion
or a lot approaching its hold time triggers a reorder check; nothing ticks
every minute. Dishes at to-order stations have no pans: each portion is made
when served (slower service, no expiry or leftover waste).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Generator

import simpy

from .arrivals import sample_arrivals
from .config import BatchPlan, ScenarioConfig, Station
from .population import MAX_SERVICES, Student, build_students
from .recorder import VESSELS, Ev, Outcome, PanEv, Recorder

DRAIN_MIN = 180  # let students still inside finish after close


class Lot:
    __slots__ = ("portions", "expires_at")

    def __init__(self, portions: int, expires_at: float):
        self.portions = portions
        self.expires_at = expires_at


class Pan:
    __slots__ = (
        "idx", "plan", "to_order", "last_batch", "lots", "level", "cooking", "batches_started",
        "cooked", "served", "expired", "leftover", "closed",
    )

    def __init__(self, idx: int, plan: BatchPlan | None, last_batch: float):
        self.idx = idx
        self.plan = plan
        self.to_order = plan is None
        self.last_batch = last_batch
        self.lots: deque[Lot] = deque()
        self.level = 0
        self.cooking = 0
        self.batches_started = 0
        self.cooked = 0
        self.served = 0
        self.expired = 0
        self.leftover = 0
        self.closed = False

    @property
    def visibly_empty(self) -> bool:
        return not self.to_order and self.level == 0


class StationRt:
    __slots__ = ("idx", "cfg", "res")

    def __init__(self, idx: int, cfg: Station, res: simpy.Resource):
        self.idx = idx
        self.cfg = cfg
        self.res = res


Proc = Generator[simpy.Event, object, object]


class Night:
    def __init__(self, cfg: ScenarioConfig, seed: int, trace: bool = False):
        self.cfg = cfg
        self.seed = seed
        self.env = simpy.Environment(initial_time=cfg.open_min)
        env = self.env

        station_index = {s.id: i for i, s in enumerate(cfg.stations)}
        self.stations = [StationRt(i, s, simpy.Resource(env, capacity=s.servers)) for i, s in enumerate(cfg.stations)]
        self.dish_station = [station_index[d.station] for d in cfg.menu]
        self.dish_vessel = [VESSELS.index(d.vessel) for d in cfg.menu]
        self.dish_hot = [d.hot for d in cfg.menu]
        self.pans: list[Pan] = []
        for i, d in enumerate(cfg.menu):
            plan = None if cfg.stations[self.dish_station[i]].to_order else cfg.kitchen[d.id]
            last = cfg.close_min
            if plan is not None and plan.last_batch_min is not None:
                last = min(plan.last_batch_min, cfg.close_min)
            self.pans.append(Pan(i, plan, last))

        self.rec = Recorder(len(cfg.menu), len(cfg.stations), trace)
        ids, times = sample_arrivals(cfg, seed)
        self.students: list[Student] = build_students(cfg, seed, ids, times)
        self._speed = cfg.layout.walk_speed_m_per_min
        self._zones = cfg.layout.zones()
        # seat positions in a separate room -> its doors (walks in or out go through one)
        self._doors = {z.pos: z.via for z in self._zones if z.via}

    # ------------------------------------------------------------ run

    def run(self) -> Night:
        self.env.process(self._service_hours())
        self.env.process(self._arrivals())
        self.env.run(until=self.cfg.close_min + DRAIN_MIN)
        return self

    def walk(self, a: tuple[float, float], b: tuple[float, float]) -> float:
        da, db = self._doors.get(a), self._doors.get(b)
        if (da or db) and da != db:
            doors, out = (da, b) if da else (db, a)
            src = a if da else b
            m = min(math.dist(src, v) + math.dist(v, out) for v in doors)
        else:
            m = math.dist(a, b)
        return m / self._speed

    # ------------------------------------------------------------ kitchen & pans

    def _service_hours(self) -> Proc:
        env = self.env
        for pan in self.pans:
            if pan.to_order:
                continue
            if pan.plan.starting_batches:
                self._add_lot(pan, pan.plan.starting_batches * pan.plan.batch_size, PanEv.OPEN_STOCK)
            self._kitchen_check(pan)
        yield env.timeout(self.cfg.close_min - self.cfg.open_min)
        for pan in self.pans:
            pan.closed = True
            if pan.to_order:
                continue
            pan.leftover += pan.level
            for lot in pan.lots:
                lot.portions = 0
            pan.lots.clear()
            pan.level = 0
            self.rec.pan(env.now, pan.idx, 0, PanEv.CLOSE)

    def _add_lot(self, pan: Pan, portions: int, code: PanEv) -> None:
        pan.cooked += portions
        if pan.closed:  # finished after close: straight to leftovers
            pan.leftover += portions
            return
        lot = Lot(portions, self.env.now + pan.plan.hold_min)
        pan.lots.append(lot)
        pan.level += portions
        self.rec.pan(self.env.now, pan.idx, pan.level, code)
        self.env.process(self._expire(pan, lot))

    def _expire(self, pan: Pan, lot: Lot) -> Proc:
        lead = pan.plan.cook_min
        if pan.plan.hold_min > lead:
            # Re-check the reorder rule when this lot stops counting as usable.
            yield self.env.timeout(pan.plan.hold_min - lead)
            self._kitchen_check(pan)
            yield self.env.timeout(lead)
        else:
            yield self.env.timeout(pan.plan.hold_min)
        if lot.portions and not pan.closed:
            pan.expired += lot.portions
            pan.level -= lot.portions
            lot.portions = 0
            self.rec.pan(self.env.now, pan.idx, pan.level, PanEv.EXPIRE)
            self._kitchen_check(pan)

    def _usable(self, pan: Pan) -> int:
        """Portions that will still be within hold time when a batch started now is ready."""
        ready_at = self.env.now + pan.plan.cook_min
        return sum(lot.portions for lot in pan.lots if lot.expires_at > ready_at)

    def _kitchen_check(self, pan: Pan) -> None:
        plan, now = pan.plan, self.env.now
        while (
            not pan.closed
            and self._usable(pan) + pan.cooking <= plan.reorder_point
            and pan.batches_started < plan.max_batches
            and now + plan.cook_min <= pan.last_batch
        ):
            pan.batches_started += 1
            pan.cooking += plan.batch_size
            self.rec.pan(now, pan.idx, pan.level, PanEv.BATCH_START)
            self.env.process(self._cook(pan))

    def _cook(self, pan: Pan) -> Proc:
        yield self.env.timeout(pan.plan.cook_min)
        pan.cooking -= pan.plan.batch_size
        self._add_lot(pan, pan.plan.batch_size, PanEv.BATCH_READY)

    def _take(self, pan: Pan) -> bool:
        if pan.closed:
            return False
        if pan.to_order:
            pan.cooked += 1
            pan.served += 1
            return True
        if pan.level == 0:
            return False
        lots = pan.lots
        while lots[0].portions == 0:  # drop expired/emptied lots (oldest first)
            lots.popleft()
        lots[0].portions -= 1
        pan.level -= 1
        pan.served += 1
        self.rec.pan(self.env.now, pan.idx, pan.level, PanEv.TAKE)
        self._kitchen_check(pan)
        return True

    # ------------------------------------------------------------ students

    def _arrivals(self) -> Proc:
        env = self.env
        for s in self.students:
            dt = s.arrive - env.now
            if dt > 0:
                yield env.timeout(dt)
            env.process(self._student(s))

    def _visit(
        self, s: Student, station: int, dishes: list[int], pos: tuple[float, float], n_served: int,
        balked: set[int] | None = None,
    ) -> Proc:
        """One trip through a station's line for one or more dishes. Returns (pos, dishes served)."""
        env, rec = self.env, self.rec
        st = self.stations[station]
        yield env.timeout(self.walk(pos, st.cfg.pos))
        pos = st.cfg.pos
        wanted = []
        for d in dishes:
            if self.pans[d].visibly_empty:  # empty pan in plain sight: don't queue for it
                rec.empty_seen += 1
                rec.ev(env.now, s.id, Ev.EMPTY_PAN, station, d)
            else:
                wanted.append(d)
        if not wanted:
            return pos, []
        queue = st.res.queue
        # Students will wait longer in a line for the dish they want most.
        patience = s.patience * self.cfg.favorite_patience_mult if s.prefs and s.prefs[0] in wanted else s.patience
        if len(queue) > patience:
            rec.balks += 1
            rec.ev(env.now, s.id, Ev.BALK, station, wanted[0], line=len(queue))
            if balked is not None:
                balked.update(wanted)
            return pos, []
        rec.ev(env.now, s.id, Ev.QUEUE_JOIN, station, wanted[0], line=len(queue))
        req = st.res.request()
        rec.queue(env.now, station, len(queue))
        yield req
        rec.queue(env.now, station, len(queue))
        got = []
        for d in wanted:
            if self._take(self.pans[d]):
                z = s.svc_z[min(n_served + len(got), MAX_SERVICES - 1)]
                yield env.timeout(st.cfg.service_median_min * math.exp(st.cfg.service_sigma * z))
                rec.ev(env.now, s.id, Ev.SERVED, station, d, self.dish_vessel[d])
                got.append(d)
            else:  # emptied while waiting
                rec.empty_seen += 1
                rec.ev(env.now, s.id, Ev.EMPTY_PAN, station, d)
        st.res.release(req)
        return pos, got

    def _student(self, s: Student) -> Proc:
        env, rec, layout = self.env, self.rec, self.cfg.layout
        rec.ev(env.now, s.id, Ev.ENTER)
        pos: tuple[float, float] = layout.entrance
        served: list[int] = []
        tried: set[int] = set()
        balked: set[int] = set()  # dishes whose line the student walked away from

        # First trip: the plate is the student's top choices; items at the same station are
        # taken in one pass through its line. Unavailable items are replaced by the next choice.
        while len(served) < s.plate_size:
            window = [d for d in s.prefs if d not in tried][: s.plate_size - len(served)]
            if not window:
                break
            station = self.dish_station[window[0]]
            here = [d for d in window if self.dish_station[d] == station]
            tried.update(here)
            pos, got = yield from self._visit(s, station, here, pos, len(served), balked)
            served.extend(got)
        plate_done = env.now
        first = s.prefs[0] if s.prefs else -1
        seat = self._zones[layout.seat_zone(s.id)].pos

        yield env.timeout(self.walk(pos, seat))
        pos = seat
        rec.ev(env.now, s.id, Ev.SIT)

        if s.wants_seconds and s.prefs:
            yield env.timeout(s.dwell / 2)
            # Favorite again if they got it, otherwise the best dish they didn't get.
            target = first if first in served else next((d for d in s.prefs if d not in served), first)
            rec.ev(env.now, s.id, Ev.SECONDS, dish=target)
            pos, got = yield from self._visit(s, self.dish_station[target], [target], pos, len(served))
            served.extend(got)
            yield env.timeout(self.walk(pos, seat))
            pos = seat
            yield env.timeout(s.dwell / 2)
        elif self.cfg.retry_balked_favorite and first in balked:
            # Ate something else first; go back for the favorite once the line may be shorter.
            yield env.timeout(s.dwell / 2)
            rec.ev(env.now, s.id, Ev.RETRY, dish=first)
            pos, got = yield from self._visit(s, self.dish_station[first], [first], pos, len(served))
            served.extend(got)
            yield env.timeout(self.walk(pos, seat))
            pos = seat
            yield env.timeout(s.dwell / 2)
        else:
            yield env.timeout(s.dwell)

        yield env.timeout(self.walk(pos, layout.dish_return))
        rec.ev(env.now, s.id, Ev.DISH_RETURN)
        yield env.timeout(self.walk(layout.dish_return, layout.entrance))
        rec.ev(env.now, s.id, Ev.EXIT)
        rec.outcomes.append(
            Outcome(
                student=s.id,
                arrive=s.arrive,
                plate_done=plate_done,
                first_choice=first,
                missed_first=first == -1 or first not in served,  # never got it, all night
                got_hot=any(self.dish_hot[d] for d in served),
                wanted_hot=first >= 0 and self.dish_hot[first],
                items=len(served),
            )
        )
