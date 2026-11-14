"""Scenario configuration: the single, immutable input to the engine.

A ScenarioConfig is fully resolved before simulating (no defaults filled in
later). Two configs that mean the same thing produce the same `config_hash`,
which is the cache key for results.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ENGINE_VERSION = "0.4.1"  # bump when model logic changes; invalidates cached results

Diet = Literal["none", "vegetarian", "vegan", "halal"]
Period = Literal["dinner", "late_night"]
Role = Literal["entree", "starch", "side", "soup", "dessert", "bread"]
ROLES: tuple[str, ...] = ("entree", "starch", "side", "soup", "dessert", "bread")
Point = tuple[float, float]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SeatZone(_Frozen):
    """A seating area (layout/floor_plan.json -> seating_zones). `via` lists the doors into a
    separate room; a walk into or out of the zone goes through the nearest one."""

    id: str
    pos: Point  # centroid of the zone's tables
    seats: int = Field(ge=1)
    via: tuple[Point, ...] = ()


class Layout(_Frozen):
    """Key points in meters (data/worcester/layout/floor_plan.json). Nav graph later."""

    entrance: Point
    seating: Point  # legacy single centroid; used only when seating_zones is empty
    dish_return: Point
    walk_speed_m_per_min: float = Field(72.0, gt=0)  # ~1.2 m/s
    seating_zones: tuple[SeatZone, ...] = ()

    def zones(self) -> tuple[SeatZone, ...]:
        return self.seating_zones or (SeatZone(id="main", pos=self.seating, seats=1),)

    def seat_zone(self, student_id: int) -> int:
        """Zone index for a student, proportional to seats. Deterministic in the student id
        (golden-ratio sequence), so it draws nothing from the RNG streams."""
        zones = self.zones()
        u = (student_id * 0.6180339887498949) % 1.0
        total = sum(z.seats for z in zones)
        acc = 0.0
        for i, z in enumerate(zones):
            acc += z.seats / total
            if u < acc:
                return i
        return len(zones) - 1


class Station(_Frozen):
    id: str
    name: str
    pos: Point
    servers: int = Field(ge=1)  # staffed: servers; self-serve: serving spots
    self_serve: bool = False
    to_order: bool = False  # cooked per order: no pans, no expiry waste, slower service
    service_median_min: float = Field(gt=0)  # per item
    service_sigma: float = Field(0.35, ge=0)  # lognormal spread


class Dish(_Frozen):
    id: str
    name: str
    station: str
    role: Role
    diet_tags: tuple[str, ...] = ()  # "vegetarian", "vegan", "halal"
    vessel: Literal["plate", "bowl", "cup"] = "plate"
    hot: bool = True
    popularity: float = Field(gt=0)  # survey-adjusted level (Bradley-Terry weight)
    serving_size: str | None = None
    carbon_rating: str | None = None
    estimate: bool = False  # new dish: popularity borrowed from "similar to" dishes + AI persona affinities
    period: Period = "dinner"  # served open..close (dinner) or close..late_close (late night)


class BatchPlan(_Frozen):
    batch_size: int = Field(ge=1)  # portions per batch
    starting_batches: int = Field(ge=0)  # ready at open
    reorder_point: int = Field(ge=0)  # start a batch when ready + cooking <= this
    cook_min: float = Field(gt=0)
    hold_min: float = Field(gt=0)  # max time on the line before it's expired
    max_batches: int = Field(ge=0)  # batches after the starting ones
    last_batch_min: int | None = None  # latest a batch may be ready; default close


class Wave(_Frozen):
    peak_min: float  # minutes since midnight
    sd_min: float = Field(gt=0)
    share: float = Field(ge=0)


class Crowd(_Frozen):
    expected_students: int = Field(ge=0)
    multiplier: float = Field(1.0, gt=0)
    shift_min: float = 0.0  # shifts every wave
    base_share: float = Field(0.1, ge=0)  # flat share spread over the whole meal
    waves: tuple[Wave, ...]


class Persona(_Frozen):
    id: str
    label: str
    description: str = ""  # one or two sentences; used in narration prompts
    share: float = Field(ge=0)
    diet: Diet
    patience: tuple[tuple[int, float], ...]  # (max students waiting they'll join, prob)
    plate_size: tuple[tuple[int, float], ...]  # (items on first trip, prob)
    seconds_prob: float = Field(ge=0, le=1)
    dwell_median_min: float = Field(gt=0)
    dwell_sigma: float = Field(0.4, ge=0)
    category_affinity: dict[str, float] = {}  # keyed by item role
    station_affinity: dict[str, float] = {}  # keyed by station id
    dish_affinity: dict[str, float] = {}  # keyed by dish id; replaces role x station

    def affinity(self, dish: Dish) -> float:
        if dish.id in self.dish_affinity:
            return self.dish_affinity[dish.id]
        return self.category_affinity.get(dish.role, 1.0) * self.station_affinity.get(dish.station, 1.0)


class ScenarioConfig(_Frozen):
    label: str = "baseline"  # cosmetic; excluded from the hash
    dc: str
    date: str  # ISO date of the menu
    meal: Literal["dinner"] = "dinner"
    open_min: int
    close_min: int  # end of dinner
    late_close_min: int | None = None  # end of late night (serving stops; seated students may stay)
    layout: Layout
    stations: tuple[Station, ...]
    menu: tuple[Dish, ...]
    kitchen: dict[str, BatchPlan]
    crowd: Crowd  # dinner arrivals, open..close
    late_crowd: Crowd | None = None  # late-night arrivals, close..late_close
    late_plate_size: tuple[tuple[int, float], ...] | None = None  # (items on first trip, prob) for late arrivals; None = persona's
    personas: tuple[Persona, ...]
    population_status: Literal["uncalibrated", "calibrated"] = "uncalibrated"
    arrival_cap_per_min: float = Field(gt=0)  # thinning bound; fixed so seeds stay comparable
    favorite_patience_mult: float = Field(1.0, ge=1)  # patience x this when the line serves the student's first choice
    retry_balked_favorite: bool = False  # after leaving their favorite's line, students sit, then try it once more
    engine_version: str = ENGINE_VERSION
    data_version: str = "dev"

    @model_validator(mode="after")
    def _check(self) -> ScenarioConfig:
        if self.close_min <= self.open_min:
            raise ValueError("close_min must be after open_min")
        if self.late_close_min is not None and self.late_close_min <= self.close_min:
            raise ValueError("late_close_min must be after close_min")
        if self.late_close_min is None and any(d.period == "late_night" for d in self.menu):
            raise ValueError("late-night dishes need late_close_min")
        station_ids = [s.id for s in self.stations]
        dish_ids = [d.id for d in self.menu]
        for name, ids in (("station", station_ids), ("dish", dish_ids)):
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {name} ids")
        for d in self.menu:
            if d.station not in station_ids:
                raise ValueError(f"dish {d.id!r} uses unknown station {d.station!r}")
        batch = {d.id for d in self.menu if not self.is_to_order(d)}
        if set(self.kitchen) != batch:
            missing, extra = sorted(batch - set(self.kitchen)), sorted(set(self.kitchen) - batch)
            raise ValueError(f"kitchen needs one BatchPlan per batch-cooked dish; missing={missing} extra={extra}")
        if self.late_plate_size is not None and abs(sum(prob for _, prob in self.late_plate_size) - 1.0) > 1e-6:
            raise ValueError("late_plate_size probabilities must sum to 1")
        if not self.personas:
            raise ValueError("at least one persona is required")
        if abs(sum(p.share for p in self.personas) - 1.0) > 1e-6:
            raise ValueError("persona shares must sum to 1")
        for p in self.personas:
            for field in ("patience", "plate_size"):
                if abs(sum(prob for _, prob in getattr(p, field)) - 1.0) > 1e-6:
                    raise ValueError(f"persona {p.id!r} {field} probabilities must sum to 1")
        return self

    @property
    def service_end(self) -> int:
        """When the last food is served (end of late night if there is one)."""
        return self.late_close_min if self.late_close_min is not None else self.close_min

    def window(self, period: str) -> tuple[int, int]:
        """(start, end) minutes of a service period."""
        if period == "late_night":
            if self.late_close_min is None:
                raise ValueError("no late night in this scenario")
            return self.close_min, self.late_close_min
        return self.open_min, self.close_min

    def period_at(self, t: float) -> str:
        return "late_night" if self.late_close_min is not None and t >= self.close_min else "dinner"

    def crowd_scaled(self, factor: float) -> dict[str, Any]:
        """`updated(**...)` changes that scale both dinner and late-night crowds by `factor`."""
        out: dict[str, Any] = {"crowd": self.crowd.model_dump() | {"multiplier": round(self.crowd.multiplier * factor, 4)}}
        if self.late_crowd is not None:
            out["late_crowd"] = self.late_crowd.model_dump() | {"multiplier": round(self.late_crowd.multiplier * factor, 4)}
        return out

    def station(self, station_id: str) -> Station:
        return next(s for s in self.stations if s.id == station_id)

    def is_to_order(self, dish: Dish) -> bool:
        return self.station(dish.station).to_order

    def updated(self, **changes: Any) -> ScenarioConfig:
        """Return a validated copy with top-level fields replaced."""
        data = self.model_dump()
        data.update(changes)
        return ScenarioConfig.model_validate(data)


# ---------------------------------------------------------------- hashing

_HASH_EXCLUDE = {"label"}


def _canon(x: Any) -> Any:
    if isinstance(x, float):
        return float(f"{x:.6g}")
    if isinstance(x, dict):
        return {k: _canon(v) for k, v in sorted(x.items())}
    if isinstance(x, list):
        items = [_canon(v) for v in x]
        if items and all(isinstance(i, dict) and "id" in i for i in items):
            items.sort(key=lambda i: i["id"])  # order of stations/dishes/personas doesn't matter
        return items
    return x


def canonical_json(cfg: ScenarioConfig) -> str:
    data = cfg.model_dump(mode="json", exclude=_HASH_EXCLUDE)
    return json.dumps(_canon(data), sort_keys=True, separators=(",", ":"))


def config_hash(cfg: ScenarioConfig) -> str:
    return hashlib.sha256(canonical_json(cfg).encode()).hexdigest()[:16]
