"""One simulated night: SimPy processes for students, stations, pans and the kitchen.

Time is minutes since midnight. A night has one or two service periods: dinner
(open..close) and, when configured, late night (close..late_close). Each dish
belongs to one period; its pans open with starting stock when the period starts
and close (leftovers counted) when it ends. Serving stops at the end of the last
period; seated students may stay until they are done.

Students decide from where they stand: they look at the lines for the dishes
they want, skip the ones that are too long or visibly empty without walking
over, and walk only to the line they choose, then stay in it. The kitchen is event-driven: taking a portion
or a lot approaching its hold time triggers a reorder check; nothing ticks
every minute. Dishes at to-order stations have no pans: each portion is made
when served (slower service, no expiry or leftover waste).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Generator

import numpy as np
import simpy

from .arrivals import _segments, _intensity, sample_arrivals
from .config import BatchPlan, ScenarioConfig, Station
from .population import MAX_SERVICES, Student, build_students
from .recorder import VESSELS, Ev, Outcome, PanEv, Recorder

DRAIN_MIN = 180  # let students still inside finish after serving stops
LAST_CALL_MAX_MIN = 30  # at a period change (9 pm), lines already formed are served for up to this long


class Lot:
    __slots__ = ("portions", "expires_at")

    def __init__(self, portions: int, expires_at: float):
        self.portions = portions
        self.expires_at = expires_at


class PanStock(simpy.Container):
    """The portions on the line (IMPROVEMENTS 1.1): a SimPy Container, so a student can wait for
    the next batch with `stock.get(1)`. Every get, immediate or after waiting, also takes the
    portions from the oldest lots, so hold times stay exact (lots of one pan expire in order)."""

    def __init__(self, env: simpy.Environment, lots: deque):
        super().__init__(env, capacity=float("inf"), init=0)
        self.lots = lots
        self.skip_lots = False  # set while removing portions whose lot was already zeroed (expiry)

    def _do_get(self, event):  # noqa: ANN001
        if self._level >= event.amount and not self.skip_lots:
            need = event.amount
            while need:
                lot = self.lots[0]
                if lot.portions == 0:  # expired or used up: drop it
                    self.lots.popleft()
                    continue
                k = min(need, lot.portions)
                lot.portions -= k
                need -= k
        return super()._do_get(event)

    def take_now(self, n: int) -> bool:
        """Take n portions at once, or nothing. Students already waiting for this pan are served
        first (in line order); a get that can't be filled right now is withdrawn."""
        self._trigger_get(None)
        if self._level < n:
            return False
        ev = self.get(n)
        if not ev.triggered:  # pragma: no cover - guarded by the level check above
            self.get_queue.remove(ev)
            return False
        return True

    def drop_waiters(self) -> None:
        for ev in list(self.get_queue):
            self.get_queue.remove(ev)


class Pan:
    __slots__ = (
        "idx", "plan", "to_order", "last_batch", "lots", "stock", "cooking", "batches_started",
        "cooked", "served", "expired", "leftover", "closed", "last_call", "taper_armed", "closed_ev",
        "cap", "cabinet", "swapping",
    )

    def __init__(self, idx: int, plan: BatchPlan | None, last_batch: float, not_open_yet: bool,
                 env: simpy.Environment, cap: int | None = None):
        self.idx = idx
        self.plan = plan
        self.to_order = plan is None
        self.last_batch = last_batch
        self.lots: deque[Lot] = deque()
        self.stock = PanStock(env, self.lots)  # level + waiting; the lot list keeps hold times
        self.cooking = 0
        self.batches_started = 0
        self.cooked = 0
        self.served = 0
        self.expired = 0
        self.leftover = 0
        self.closed = not_open_yet  # closed before its period starts and after it ends
        self.last_call = False  # period over: no new students, but the line already formed is served
        self.taper_armed = False  # a re-check is scheduled for when the taper starts
        self.closed_ev = env.event()  # fires when the pan closes: anyone waiting for it gives up
        # Pan size (IMPROVEMENTS A): at most `cap` portions on the line (one hotel pan); the rest
        # of a batch waits in the hot-hold cabinet until staff swap in a full pan.
        self.cap = cap
        self.cabinet = 0
        self.swapping = False

    @property
    def on_line(self) -> int:
        return int(self.stock.level)

    @property
    def level(self) -> int:
        """All portions cooked and still good: on the line plus in the hot-hold cabinet."""
        return int(self.stock.level) + self.cabinet

    @property
    def visibly_empty(self) -> bool:
        return not self.to_order and self.on_line == 0


STUDENT_PRIORITY, OFF_DUTY_PRIORITY = 1, 0  # off-duty tokens go ahead of students


class StationRt:
    __slots__ = ("idx", "cfg", "res", "shifted", "off_duty")

    def __init__(self, idx: int, cfg: Station, env: simpy.Environment):
        self.idx = idx
        self.cfg = cfg
        # Staffing by hour: capacity = most servers on any shift; "off duty" tokens (higher
        # priority than students) hold the servers that aren't working, so a server leaves only
        # after finishing the current student.
        self.shifted = bool(cfg.shifts)
        self.res = (simpy.PriorityResource(env, capacity=cfg.max_servers) if self.shifted
                    else simpy.Resource(env, capacity=cfg.servers))
        self.off_duty: list = []

    def line(self) -> int:
        """Students waiting (off-duty tokens in the queue aren't people)."""
        if not self.shifted:
            return len(self.res.queue)
        return sum(1 for r in self.res.queue if r.priority == STUDENT_PRIORITY)

    def request(self):
        return self.res.request(priority=STUDENT_PRIORITY) if self.shifted else self.res.request()


Proc = Generator[simpy.Event, object, object]


class Night:
    def __init__(self, cfg: ScenarioConfig, seed: int, trace: bool = False):
        self.cfg = cfg
        self.seed = seed
        self.env = simpy.Environment(initial_time=cfg.open_min)
        env = self.env

        station_index = {s.id: i for i, s in enumerate(cfg.stations)}
        self.stations = [StationRt(i, s, env) for i, s in enumerate(cfg.stations)]
        self.dish_station = [station_index[d.station] for d in cfg.menu]
        self.dish_vessel = [VESSELS.index(d.vessel) for d in cfg.menu]
        self.dish_hot = [d.hot for d in cfg.menu]
        self.dish_window = [cfg.window(d.period) for d in cfg.menu]
        self.pans: list[Pan] = []
        for i, d in enumerate(cfg.menu):
            plan = None if cfg.stations[self.dish_station[i]].to_order else cfg.kitchen[d.id]
            start, end = self.dish_window[i]
            last = end
            if plan is not None and plan.last_batch_min is not None:
                last = min(plan.last_batch_min, end)
            cap = cfg.pan_capacity.get(d.role) if plan is not None else None
            self.pans.append(Pan(i, plan, last, not_open_yet=start > cfg.open_min, env=env, cap=cap))

        self.rec = Recorder(len(cfg.menu), len(cfg.stations), trace)
        self._forecast_cdf = self._build_forecast(cfg)
        self.readers = simpy.Resource(env, capacity=cfg.swipe_readers) if cfg.swipe_readers else None
        # Kitchen capacity (IMPROVEMENTS 1.3): cooks per service period and equipment units.
        kr = cfg.kitchen_resources
        self.cooks = {p: simpy.Resource(env, capacity=max(1, n)) for p, n in kr.cooks.items()} if kr else {}
        self.equipment = {k: simpy.Resource(env, capacity=max(1, n)) for k, n in kr.equipment.items()} if kr else {}
        self.dish_equipment = [kr.equipment_for(d) if kr else None for d in cfg.menu]
        self.dish_period = [d.period for d in cfg.menu]
        self.dish_prep = [kr.prep_for(d, cfg.kitchen[d.id].cook_min) if kr and d.id in cfg.kitchen else 0.0 for d in cfg.menu]
        ids, times = sample_arrivals(cfg, seed)
        self.students: list[Student] = build_students(cfg, seed, ids, times)
        self._speed = cfg.layout.walk_speed_m_per_min
        self._zones = cfg.layout.zones()
        # seat positions in a separate room -> its doors (walks in or out go through one)
        self._doors = {z.pos: z.via for z in self._zones if z.via}
        # Seats (IMPROVEMENTS 2.4): with seat_limits each zone holds `seats` students; otherwise the
        # zones are unlimited but still counted, so peak_seated says how many seats the night needs.
        if cfg.layout.seating_zones:
            self.seats = [simpy.Resource(env, capacity=z.seats if cfg.seat_limits else 10**6) for z in self._zones]
        else:
            self.seats = None

    # ------------------------------------------------------------ run

    def run(self) -> Night:
        for window in sorted(set(self.dish_window)):
            self.env.process(self._service_hours(*window))
        for pan in self.pans:
            if pan.plan is not None and pan.plan.forecast_reorder and pan.plan.expected_demand is not None:
                self.env.process(self._forecast_watch(pan))
        for st in self.stations:
            if st.shifted:
                self.env.process(self._staffing(st))
        self.env.process(self._arrivals())
        self.env.run(until=self.cfg.service_end + DRAIN_MIN)
        return self

    def _staffing(self, st: StationRt) -> Proc:
        """Follow the station's shifts by holding or releasing off-duty tokens."""
        env = self.env
        steps = [(self.cfg.open_min, st.cfg.servers), *sorted(st.cfg.shifts)]
        for t, servers in steps:
            if t > env.now:
                yield env.timeout(t - env.now)
            want_off = st.res.capacity - servers
            while len(st.off_duty) < want_off:  # a server goes off duty after the current student
                st.off_duty.append(st.res.request(priority=OFF_DUTY_PRIORITY))
            while len(st.off_duty) > want_off:  # a server comes back
                tok = st.off_duty.pop()
                if tok.triggered:
                    st.res.release(tok)
                else:
                    tok.cancel()

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

    def _service_hours(self, start: int, end: int) -> Proc:
        """Open the pans of one service period at `start`, close them at `end`."""
        env = self.env
        pans = [p for p in self.pans if self.dish_window[p.idx] == (start, end)]
        if start > env.now:
            yield env.timeout(start - env.now)
        for pan in pans:
            pan.closed = False
            if pan.to_order:
                continue
            if pan.plan.starting_batches:
                self._add_lot(pan, pan.plan.starting_batches * pan.plan.batch_size, PanEv.OPEN_STOCK)
            self._kitchen_check(pan)
        yield env.timeout(end - env.now)
        if end < self.cfg.service_end:
            # Dinner ends but the hall stays open for late night: nobody new joins these dishes'
            # lines, and the students already in them are served from what is left.
            for pan in pans:
                pan.last_call = True
            busy = {self.dish_station[p.idx] for p in pans}
            stop = env.now + LAST_CALL_MAX_MIN
            while env.now < stop and any(self.stations[i].res.count - len(self.stations[i].off_duty) > 0 or self.stations[i].line() for i in busy):
                yield env.timeout(0.5)
        for pan in pans:
            pan.closed = True
            if not pan.closed_ev.triggered:
                pan.closed_ev.succeed()  # nobody waits at a closed pan
            if pan.to_order:
                continue
            pan.stock.drop_waiters()  # they give up via closed_ev
            pan.leftover += pan.level
            if pan.on_line:
                pan.stock.take_now(pan.on_line)
            pan.cabinet = 0
            pan.lots.clear()
            self.rec.pan(env.now, pan.idx, 0, PanEv.CLOSE)

    def _add_lot(self, pan: Pan, portions: int, code: PanEv) -> None:
        pan.cooked += portions
        if pan.closed:  # finished after close: straight to leftovers
            pan.leftover += portions
            return
        lot = Lot(portions, self.env.now + pan.plan.hold_min)
        pan.lots.append(lot)
        room = portions if pan.cap is None else max(0, min(portions, pan.cap - pan.on_line))
        if room:
            pan.stock.put(room)  # a student waiting with stock.get(1) is served from it
        pan.cabinet += portions - room
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
        if not pan.closed:
            # Students already waiting for this pan take their portions first (oldest lots, so
            # possibly this one); only what is left of the lot expires. With no waiter this is a no-op.
            pan.stock._trigger_get(None)
        if lot.portions and not pan.closed:
            pan.expired += lot.portions
            p, lot.portions = lot.portions, 0
            from_cab = min(pan.cabinet, p)  # the cabinet first, then the line
            pan.cabinet -= from_cab
            rest = min(p - from_cab, pan.on_line)
            if rest:
                pan.stock.skip_lots = True
                pan.stock.take_now(rest)
                pan.stock.skip_lots = False
            self._maybe_swap(pan)
            self.rec.pan(self.env.now, pan.idx, pan.level, PanEv.EXPIRE)
            self._kitchen_check(pan)

    def _maybe_swap(self, pan: Pan) -> None:
        """The line pan is empty but the cabinet has food: staff swap in a full pan (pan_swap_min)."""
        if pan.cap is None or pan.on_line or not pan.cabinet or pan.swapping or pan.closed:
            return
        pan.swapping = True

        def swap() -> Proc:
            yield self.env.timeout(self.cfg.pan_swap_min)
            pan.swapping = False
            if pan.closed or not pan.cabinet:
                return
            k = min(pan.cap - pan.on_line, pan.cabinet)
            if k <= 0:  # a fresh batch already filled the line pan during the swap
                return
            pan.cabinet -= k
            pan.stock.put(k)  # waiting students are served from the new pan
            self.rec.pan(self.env.now, pan.idx, pan.level, PanEv.BATCH_READY)

        self.env.process(swap())

    def _usable(self, pan: Pan) -> int:
        """Portions that will still be within hold time when a batch started now is ready."""
        ready_at = self.env.now + pan.plan.cook_min
        return sum(lot.portions for lot in pan.lots if lot.expires_at > ready_at)

    @staticmethod
    def _build_forecast(cfg: ScenarioConfig) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Per service period: minute grid and the share of that period's arrivals expected by
        each minute (the kitchen's forecast of when demand comes)."""
        out = {}
        for crowd, start, end, sub, _ in _segments(cfg):
            grid = np.arange(start, end + 1, 1.0)
            lam = _intensity(crowd, start, end, grid)
            cum = np.concatenate([[0.0], np.cumsum((lam[1:] + lam[:-1]) / 2)])
            out["late_night" if sub else "dinner"] = (grid, cum / cum[-1] if cum[-1] > 0 else cum)
        return out

    def _forecast_need(self, pan: Pan) -> int:
        """Portions expected over the next cook_min + forecast_extra_min, x forecast_factor."""
        plan, now = pan.plan, self.env.now
        grid, cdf = self._forecast_cdf[self.cfg.menu[pan.idx].period]
        ahead = now + plan.cook_min + plan.forecast_extra_min
        share = np.interp(ahead, grid, cdf) - np.interp(now, grid, cdf)
        mean = plan.forecast_factor * (plan.expected_demand or 0.0) * share
        return math.ceil(mean + plan.forecast_safety_z * math.sqrt(mean))

    def _reorder_threshold(self, pan: Pan) -> int:
        plan, now = pan.plan, self.env.now
        if plan.forecast_reorder and plan.expected_demand is not None and not (
            plan.taper_from_min is not None and now >= plan.taper_from_min
        ):
            return self._forecast_need(pan)
        return plan.reorder_at(now)

    def _forecast_watch(self, pan: Pan) -> Proc:
        """Forecast pans are re-checked every 5 minutes, to start cooking ahead of a rush."""
        while not pan.closed or self.env.now < self.dish_window[pan.idx][0]:
            yield self.env.timeout(5.0)
            if not pan.closed:
                self._kitchen_check(pan)
            if self.env.now >= self.dish_window[pan.idx][1]:
                return

    def _kitchen_check(self, pan: Pan) -> None:
        plan, now = pan.plan, self.env.now
        while (
            not pan.closed
            and self._usable(pan) + pan.cooking <= self._reorder_threshold(pan)
            and pan.batches_started < plan.max_batches
            and now + plan.cook_min <= pan.last_batch
        ):
            size = plan.batch_at(now)
            pan.batches_started += 1
            pan.cooking += size
            self.rec.pan(now, pan.idx, pan.level, PanEv.BATCH_START)
            self.env.process(self._cook(pan, size))
        if plan.taper_from_min is not None and now < plan.taper_from_min and not pan.closed:
            self._taper_wakeup(pan)

    def _taper_wakeup(self, pan: Pan) -> None:
        """Re-check the pan once when the taper starts (the reorder point changes then)."""
        if pan.taper_armed:
            return
        pan.taper_armed = True

        def wake() -> Proc:
            yield self.env.timeout(pan.plan.taper_from_min - self.env.now)
            self._kitchen_check(pan)

        self.env.process(wake())

    def _cook(self, pan: Pan, size: int) -> Proc:
        env, rec, i = self.env, self.rec, pan.idx
        cooks = self.cooks.get(self.dish_period[i])
        if cooks is None:  # unlimited kitchen
            yield env.timeout(pan.plan.cook_min)
        else:
            prep = self.dish_prep[i]
            t0 = env.now
            with cooks.request() as c:  # hands-on prep needs a cook
                yield c
                rec.batch_wait_min += env.now - t0
                key = f"cooks:{self.dish_period[i]}"
                rec.equipment_peak[key] = max(rec.equipment_peak.get(key, 0), cooks.count)
                yield env.timeout(prep)
            rec.cook_busy_min[self.dish_period[i]] = rec.cook_busy_min.get(self.dish_period[i], 0.0) + prep
            rest = pan.plan.cook_min - prep
            name = self.dish_equipment[i]
            if name is not None and rest > 0:
                eq = self.equipment[name]
                t1 = env.now
                with eq.request() as e:  # then the oven / kettle / fryer for the rest of the cook
                    yield e
                    rec.batch_wait_min += env.now - t1
                    rec.equipment_peak[name] = max(rec.equipment_peak.get(name, 0), eq.count)
                    yield env.timeout(rest)
            elif rest > 0:
                yield env.timeout(rest)
        pan.cooking -= size
        self._add_lot(pan, size, PanEv.BATCH_READY)

    def _take(self, pan: Pan) -> bool:
        if pan.closed:
            return False
        if pan.to_order:
            pan.cooked += 1
            pan.served += 1
            return True
        if not pan.stock.take_now(1):  # takes from the oldest lot
            return False
        self._maybe_swap(pan)
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

    def _serving(self, d: int) -> bool:
        """Can a student choose this dish now (its period is on and not at last call)?"""
        pan = self.pans[d]
        return not pan.closed and not pan.last_call

    def _choose(
        self, s: Student, cands: list[int], limit: int, skipped: set[int], balked: set[int] | None,
        must: bool,
    ) -> tuple[int, list[int], list[int]] | None:
        """Pick a station from where the student stands, without walking over.

        `cands`: dishes being served, best first. The student looks at each candidate station's
        line and pans: visibly empty pans are noted and dropped; a line longer than their patience
        (x favorite_patience_mult for their first choice) is skipped. The first acceptable station
        in preference order wins. If none is acceptable and `must`, they pick the shortest line;
        otherwise they choose not to go (None).
        Only the student's top `limit` dishes (what they came for) are logged as empty pans or
        skipped lines; fallbacks further down their list are passed over quietly.
        Returns (station, dishes to take there (at most `limit`), dishes seen empty).
        """
        env, rec = self.env, self.rec
        top = set(cands[:limit])
        order: dict[int, list[int]] = {}
        for d in cands:
            order.setdefault(self.dish_station[d], []).append(d)
        empty: list[int] = []
        options: list[tuple[int, int, list[int]]] = []  # (line, station, wanted)
        for station, ds in order.items():
            wanted = []
            for d in ds:
                if self.pans[d].visibly_empty:  # empty pan in plain sight
                    if d in top:
                        rec.empty_seen += 1
                        rec.ev(env.now, s.id, Ev.EMPTY_PAN, station, d)
                    empty.append(d)
                else:
                    wanted.append(d)
            if not wanted:
                continue
            line = self.stations[station].line()
            fav = bool(s.prefs) and s.prefs[0] in wanted
            patience = s.patience * self.cfg.favorite_patience_mult if fav else s.patience
            if line <= patience:
                return station, self._take_here(wanted, top, limit), empty
            options.append((line, station, wanted))
            if station not in skipped and top.intersection(wanted):  # one skip per station per trip
                skipped.add(station)
                rec.balks += 1
                rec.ev(env.now, s.id, Ev.BALK, station, wanted[0], line=line)
            if balked is not None:
                balked.update(wanted)
        if options:
            if not must:
                return None
            line, station, wanted = min(options, key=lambda o: o[0])  # stable: ties keep preference order
            return station, self._take_here(wanted, top, limit), empty
        return -1, [], empty  # every candidate was visibly empty: nothing to walk to

    @staticmethod
    def _take_here(wanted: list[int], top: set[int], limit: int) -> list[int]:
        """At the chosen station take the plate items (top choices) it has; a fallback station
        gives just its best dish."""
        here = [d for d in wanted if d in top]
        return (here or wanted[:1])[:limit]

    def _visit(self, s: Student, station: int, dishes: list[int], pos: tuple[float, float], n_served: int) -> Proc:
        """Walk to the chosen station and stay in its line. Returns (pos, dishes served)."""
        env, rec = self.env, self.rec
        st = self.stations[station]
        yield env.timeout(self.walk(pos, st.cfg.pos))
        pos = st.cfg.pos
        wanted = []
        for d in dishes:
            if self.pans[d].closed:
                rec.ev(env.now, s.id, Ev.CLOSED, station, d)
            elif self.pans[d].visibly_empty:  # ran out while they walked over
                rec.empty_seen += 1
                rec.ev(env.now, s.id, Ev.EMPTY_PAN, station, d)
            else:
                wanted.append(d)
        if not wanted:
            return pos, []
        rec.ev(env.now, s.id, Ev.QUEUE_JOIN, station, wanted[0], line=st.line())
        req = st.request()
        rec.queue(env.now, station, st.line())
        yield req
        rec.queue(env.now, station, st.line())
        got = []
        for d in wanted:
            pan = self.pans[d]
            ok = self._take(pan)
            if not ok and not pan.closed and (pan.cooking > 0 or pan.swapping or pan.cabinet) and self.cfg.empty_pan_wait_min > 0:
                # Empty, but a batch is already cooking: wait at the counter a little (1.2).
                fav = bool(s.prefs) and s.prefs[0] == d
                limit = self.cfg.empty_pan_wait_min * (self.cfg.favorite_patience_mult if fav else 1.0)
                portion = pan.stock.get(1)  # SimPy queues the student for the next portion
                t0 = env.now
                yield portion | env.timeout(limit) | pan.closed_ev
                rec.empty_wait_min += env.now - t0
                if portion.triggered:  # got a portion from the new batch or the swapped-in pan
                    pan.served += 1
                    self._maybe_swap(pan)
                    self.rec.pan(env.now, pan.idx, pan.level, PanEv.TAKE)
                    self._kitchen_check(pan)
                    ok = True
                elif portion in pan.stock.get_queue:
                    pan.stock.get_queue.remove(portion)
            if ok:
                z = s.svc_z[min(n_served + len(got), MAX_SERVICES - 1)]
                yield env.timeout(st.cfg.service_median_min * math.exp(st.cfg.service_sigma * z))
                rec.ev(env.now, s.id, Ev.SERVED, station, d, self.dish_vessel[d])
                hk = (station, int(env.now // 60))
                rec.served_by_hour[hk] = rec.served_by_hour.get(hk, 0) + 1
                got.append(d)
            elif self.pans[d].closed:  # service period ended while they waited
                rec.ev(env.now, s.id, Ev.CLOSED, station, d)
            else:  # emptied while waiting
                rec.empty_seen += 1
                rec.ev(env.now, s.id, Ev.EMPTY_PAN, station, d)
        st.res.release(req)
        return pos, got

    def _go_back(self, s: Student, target: int, pos: tuple[float, float], n_served: int, seat) -> Proc:
        """From the seat, look at the target's line; walk over only if it's acceptable."""
        if not self._serving(target):
            return pos, []
        pick = self._choose(s, [target], 1, set(), None, must=False)
        if pick is None or pick[0] < 0:
            return pos, []
        pos, got = yield from self._visit(s, pick[0], pick[1], pos, n_served)
        yield self.env.timeout(self.walk(pos, seat))
        return seat, got

    def _student(self, s: Student) -> Proc:
        env, rec, layout = self.env, self.rec, self.cfg.layout
        if self.readers is not None:  # swipe in at the card readers (IMPROVEMENTS A)
            t0 = env.now
            rec.swipe_queue_max = max(rec.swipe_queue_max, len(self.readers.queue) + 1)
            with self.readers.request() as r:
                yield r
                rec.swipe_wait_min += env.now - t0
                yield env.timeout(self.cfg.swipe_min)
        rec.ev(env.now, s.id, Ev.ENTER)
        pos: tuple[float, float] = layout.entrance
        served: list[int] = []
        tried: set[int] = set()
        balked: set[int] = set()  # dishes whose line the student skipped as too long
        skipped: set[int] = set()  # stations already skipped on this trip (one event each)

        # First trip: the plate is the student's top choices; items at the same station are
        # taken in one pass through its line. The student picks each station from where they
        # stand, and walks only to the line they decide to join.
        # Every dish they'd eat is a candidate (best first). With an empty plate they join the
        # shortest line if all are too long; once they have something, they go sit instead.
        while len(served) < s.plate_size:
            need = s.plate_size - len(served)
            cands = [d for d in s.prefs if d not in tried and self._serving(d)]
            if not cands:
                break
            pick = self._choose(s, cands, need, skipped, balked, must=not served)
            if pick is None:
                break
            station, here, empty = pick
            tried.update(empty)
            if station < 0:
                continue
            tried.update(here)
            pos, got = yield from self._visit(s, station, here, pos, len(served))
            served.extend(got)
        plate_done = env.now
        first = s.prefs[0] if s.prefs else -1
        zone = layout.seat_zone(s.seat_key)  # a group sits in the first member's zone
        seat_req = None
        if self.seats is not None:
            # Usual zone if it has a free seat, else the zone with the most free seats; if every
            # zone is full, wait standing in the usual zone for the first free seat there.
            free = [self.seats[k].capacity - self.seats[k].count for k in range(len(self.seats))]
            if free[zone] <= 0 and max(free) > 0:
                zone = max(range(len(free)), key=lambda k: free[k])
        seat = self._zones[zone].pos

        yield env.timeout(self.walk(pos, seat))
        pos = seat
        if self.seats is not None:
            seat_req = self.seats[zone].request()
            t0 = env.now
            yield seat_req
            rec.seat_wait_min += env.now - t0
            rec.peak_seated = max(rec.peak_seated, sum(r.count for r in self.seats))
        rec.ev(env.now, s.id, Ev.SIT, line=zone)  # `line` carries the seat zone index for SIT

        if s.wants_seconds and s.prefs:
            yield env.timeout(s.dwell / 2)
            # Favorite again if they got it, otherwise the best dish they didn't get.
            target = first if first in served else next((d for d in s.prefs if d not in served), first)
            rec.ev(env.now, s.id, Ev.SECONDS, dish=target)
            pos, got = yield from self._go_back(s, target, pos, len(served), seat)
            served.extend(got)
            yield env.timeout(s.dwell / 2)
        elif self.cfg.retry_balked_favorite and first in balked and first not in served:
            # Ate something else first; go back for the favorite once the line may be shorter.
            yield env.timeout(s.dwell / 2)
            rec.ev(env.now, s.id, Ev.RETRY, dish=first)
            pos, got = yield from self._go_back(s, first, pos, len(served), seat)
            served.extend(got)
            yield env.timeout(s.dwell / 2)
        else:
            yield env.timeout(s.dwell)

        if seat_req is not None:
            self.seats[zone].release(seat_req)  # seat kept through seconds trips, freed on the way out
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
                exit=env.now,
            )
        )
