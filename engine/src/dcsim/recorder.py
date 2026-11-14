"""Append-only recorder. Cheap tuples during the run; reduced to metrics after.

Pan levels and queue lengths are logged as *changes*, so they form exact step
functions (minutes out of stock is computed exactly, not sampled). Per-student
events are kept only when trace=True (the 3D playback night).
"""

from __future__ import annotations

from enum import IntEnum
from typing import NamedTuple


class Ev(IntEnum):
    ENTER = 0
    QUEUE_JOIN = 1
    BALK = 2  # saw a line too long from where they stood and chose another station (no walk)
    SERVED = 3
    EMPTY_PAN = 4
    SIT = 5
    SECONDS = 6
    DISH_RETURN = 7
    EXIT = 8
    RETRY = 9  # going back for the favorite whose line they skipped
    CLOSED = 10  # still in line when the dish's service period ended (9 pm dinner, 12 am late night)


class PanEv(IntEnum):
    OPEN_STOCK = 0
    TAKE = 1
    BATCH_START = 2
    BATCH_READY = 3
    EXPIRE = 4
    CLOSE = 5


VESSELS = ("plate", "bowl", "cup")


class Outcome(NamedTuple):
    student: int
    arrive: float
    plate_done: float
    first_choice: int  # dish index, -1 if nothing eligible
    missed_first: bool  # never got their first choice during the whole visit (incl. return trips)
    got_hot: bool
    wanted_hot: bool  # first choice is a hot dish
    items: int
    exit: float = 0.0  # left the building


class Recorder:
    __slots__ = ("trace", "events", "pan_events", "pan_levels", "queue_lens", "outcomes", "balks", "empty_seen",
                 "batch_wait_min", "cook_busy_min", "equipment_peak", "empty_wait_min", "seat_wait_min", "peak_seated",
                 "served_by_hour", "swipe_wait_min", "swipe_queue_max")

    def __init__(self, n_dishes: int, n_stations: int, trace: bool = False):
        self.trace = trace
        self.events: list[tuple] = []  # (t, student, Ev, station, dish, vessel, line)  [trace only]
        self.pan_events: list[tuple] = []  # (t, dish, level, PanEv)                [trace only]
        self.pan_levels: list[list[tuple[float, int]]] = [[] for _ in range(n_dishes)]
        self.queue_lens: list[list[tuple[float, int]]] = [[] for _ in range(n_stations)]
        self.outcomes: list[Outcome] = []
        self.balks = 0
        self.empty_seen = 0
        self.batch_wait_min = 0.0  # minutes batches waited for a cook or equipment
        self.cook_busy_min: dict[str, float] = {}  # service period -> hands-on prep minutes
        self.equipment_peak: dict[str, int] = {}  # equipment type -> most units in use at once
        self.empty_wait_min = 0.0  # minutes students waited at an empty pan for a batch
        self.seat_wait_min = 0.0  # minutes students stood waiting for a seat
        self.peak_seated = 0
        self.swipe_wait_min = 0.0  # minutes students waited at the card readers
        self.swipe_queue_max = 0
        self.served_by_hour: dict[tuple[int, int], int] = {}  # (station, hour of day) -> items served

    def ev(self, t: float, student: int, code: Ev, station: int = -1, dish: int = -1, vessel: int = -1,
           line: int = -1) -> None:
        """`line`: students already waiting when this one joined or skipped it (queue_join / balk)."""
        if self.trace:
            self.events.append((t, student, code, station, dish, vessel, line))

    def pan(self, t: float, dish: int, level: int, code: PanEv) -> None:
        if code not in (PanEv.BATCH_START,):
            self.pan_levels[dish].append((t, level))
        if self.trace:
            self.pan_events.append((t, dish, level, code))

    def queue(self, t: float, station: int, length: int) -> None:
        self.queue_lens[station].append((t, length))
