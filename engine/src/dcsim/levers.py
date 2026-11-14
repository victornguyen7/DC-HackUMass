"""What-If Lab levers (SPEC §6.1) as a validated request -> ScenarioConfig.

A request names a date, an optional preset and optional levers. Lever values
are snapped to steps (ENGINE.md §6.1) so the space of configs stays finite and
nearby slider positions hit the cache.
"""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import Role, ScenarioConfig
from .data import load_baseline
from .presets import PRESETS

CROWD_STEP = 0.05
POPULARITY_STEP = 0.1


def _snap(x: float, step: float) -> float:
    return round(round(x / step) * step, 6)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KitchenLever(_Strict):
    """Per-dish batch plan changes. Omitted fields keep the baseline value."""

    batch_size: int | None = Field(None, ge=1)
    starting_batches: int | None = Field(None, ge=0)
    reorder_point: int | None = Field(None, ge=0)
    max_batches: int | None = Field(None, ge=0)
    last_batch_min: int | None = None  # minutes since midnight


class KitchenStaffLever(_Strict):
    """Kitchen capacity (IMPROVEMENTS 1.3): cooks per service period and equipment units."""

    cooks: dict[Literal["dinner", "late_night"], int] = {}  # period -> cooks (1..60)
    equipment: dict[str, int] = {}  # equipment type -> units (1..60)


class StationLever(_Strict):
    servers: int | None = Field(None, ge=1)
    service_median_min: float | None = Field(None, gt=0)
    # Staffing by hour (N3): [(from minute since midnight, servers)], e.g. [(1110, 3), (1200, 2)]
    shifts: list[tuple[int, int]] | None = None


class NewDish(_Strict):
    """A dish that isn't on the menu (SPEC §6.1 new-dish wizard).

    Popularity is borrowed from the "similar to" dishes (their geometric mean x
    `popularity_multiplier`). `affinity` gives each persona's taste for it, from
    POST /api/ai/dish-prior ("AI estimate"); a persona left out gets the mean of
    its affinities for the similar dishes. The batch plan defaults to a similar
    dish's plan at the same kind of station.
    """

    name: str = Field(min_length=1, max_length=80)
    station: str
    role: Role
    diet_tags: list[Literal["vegetarian", "vegan", "halal"]] = []
    vessel: Literal["plate", "bowl", "cup"] = "plate"
    hot: bool = True
    similar_to: list[str] = Field(min_length=1, max_length=3)
    popularity_multiplier: float = Field(1.0, gt=0, le=3)  # snapped to 0.1
    affinity: dict[str, float] = {}  # persona id -> 0.1..10, snapped to 0.1
    batch: KitchenLever | None = None

    @property
    def id(self) -> str:
        return "new_" + re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")


class Levers(_Strict):
    crowd_multiplier: float | None = Field(None, gt=0, le=3)  # x baseline crowd, snapped to 5%
    crowd_shift_min: float | None = Field(None, ge=-120, le=120)  # moves every arrival wave
    stations: dict[str, StationLever] = {}
    kitchen: dict[str, KitchenLever] = {}  # keyed by dish id
    kitchen_staff: KitchenStaffLever | None = None
    popularity: dict[str, float] = {}  # dish id -> multiplier, snapped to 0.1
    remove_dishes: list[str] = []
    add_dishes: list[NewDish] = []


class ScenarioRequest(_Strict):
    date: str | None = None  # default: first prepared date
    preset: str | None = None  # applied before levers
    levers: Levers | None = None
    label: str | None = None
    n_nights: int = Field(30, ge=1, le=200)


def apply_levers(cfg: ScenarioConfig, lv: Levers) -> ScenarioConfig:
    changes: dict = {}
    dish_ids = {d.id for d in cfg.menu}
    station_ids = {s.id for s in cfg.stations}
    unknown = (set(lv.kitchen) | set(lv.popularity) | set(lv.remove_dishes)) - dish_ids
    if unknown:
        raise ValueError(f"unknown dish ids: {sorted(unknown)}")
    if set(lv.stations) - station_ids:
        raise ValueError(f"unknown station ids: {sorted(set(lv.stations) - station_ids)}")
    not_batch = set(lv.kitchen) - set(cfg.kitchen)
    if not_batch:
        raise ValueError(f"made-to-order dishes have no batch plan: {sorted(not_batch)}")

    if lv.crowd_multiplier is not None or lv.crowd_shift_min is not None:
        if lv.crowd_multiplier is not None:  # scales dinner and late night alike
            changes |= cfg.crowd_scaled(_snap(lv.crowd_multiplier, CROWD_STEP))
        crowd = changes.get("crowd", cfg.crowd.model_dump())
        if lv.crowd_shift_min is not None:  # dinner class-change waves only
            crowd["shift_min"] = cfg.crowd.shift_min + round(lv.crowd_shift_min / 5) * 5
        changes["crowd"] = crowd

    if lv.kitchen_staff is not None:
        kr = cfg.kitchen_resources
        if kr is None:
            raise ValueError("this scenario has no kitchen_resources to change")
        bad = set(lv.kitchen_staff.equipment) - set(kr.equipment)
        if bad:
            raise ValueError(f"unknown equipment: {sorted(bad)}")
        clamp = lambda v: max(1, min(60, int(v)))  # noqa: E731
        changes["kitchen_resources"] = kr.model_dump() | {
            "cooks": kr.cooks | {k: clamp(v) for k, v in lv.kitchen_staff.cooks.items()},
            "equipment": kr.equipment | {k: clamp(v) for k, v in lv.kitchen_staff.equipment.items()},
        }

    if lv.stations:
        changes["stations"] = [
            s.model_dump() | lv.stations[s.id].model_dump(exclude_none=True) if s.id in lv.stations else s.model_dump()
            for s in cfg.stations
        ]

    removed = set(lv.remove_dishes)
    if lv.popularity or removed:
        changes["menu"] = [
            d.model_dump() | ({"popularity": d.popularity * _snap(lv.popularity[d.id], POPULARITY_STEP)} if d.id in lv.popularity else {})
            for d in cfg.menu
            if d.id not in removed
        ]

    if lv.kitchen or removed:
        changes["kitchen"] = {
            k: v.model_dump() | (lv.kitchen[k].model_dump(exclude_none=True) if k in lv.kitchen else {})
            for k, v in cfg.kitchen.items()
            if k not in removed
        }

    cfg = cfg.updated(**changes) if changes else cfg
    return add_dishes(cfg, lv.add_dishes) if lv.add_dishes else cfg


def add_dishes(cfg: ScenarioConfig, new: list[NewDish]) -> ScenarioConfig:
    menu = {d.id: d for d in cfg.menu}
    stations = {s.id: s for s in cfg.stations}
    persona_ids = {p.id for p in cfg.personas}
    dishes, kitchen = [d.model_dump() for d in cfg.menu], {k: v.model_dump() for k, v in cfg.kitchen.items()}
    affinity: dict[str, dict[str, float]] = {p.id: dict(p.dish_affinity) for p in cfg.personas}
    for nd in new:
        if nd.id in menu or any(d["id"] == nd.id for d in dishes):
            raise ValueError(f"dish {nd.name!r} is already on the menu")
        if nd.station not in stations:
            raise ValueError(f"unknown station {nd.station!r}")
        missing = [x for x in nd.similar_to if x not in menu]
        if missing:
            raise ValueError(f"unknown 'similar to' dishes: {missing}")
        if set(nd.affinity) - persona_ids:
            raise ValueError(f"unknown personas: {sorted(set(nd.affinity) - persona_ids)}")
        similar = [menu[x] for x in nd.similar_to]
        pop = math.exp(sum(math.log(d.popularity) for d in similar) / len(similar)) * _snap(nd.popularity_multiplier, POPULARITY_STEP)
        dishes.append({"id": nd.id, "name": nd.name, "station": nd.station, "role": nd.role,
                       "diet_tags": tuple(nd.diet_tags), "vessel": nd.vessel, "hot": nd.hot,
                       "popularity": round(pop, 6), "estimate": True})
        for p in cfg.personas:
            a = nd.affinity.get(p.id)
            if a is None:
                a = sum(p.affinity(d) for d in similar) / len(similar)
            affinity[p.id][nd.id] = max(0.1, min(10.0, _snap(a, 0.1)))
        if not stations[nd.station].to_order:
            src = next((d for d in similar if d.id in cfg.kitchen and d.station == nd.station), None) \
                or next((d for d in similar if d.id in cfg.kitchen), None)
            if src is None:
                raise ValueError(f"{nd.name!r} is batch-cooked but no 'similar to' dish has a batch plan; set `batch` fully")
            kitchen[nd.id] = cfg.kitchen[src.id].model_dump() | (nd.batch.model_dump(exclude_none=True) if nd.batch else {})
    personas = [p.model_dump() | {"dish_affinity": affinity[p.id]} for p in cfg.personas]
    return cfg.updated(menu=dishes, kitchen=kitchen, personas=personas)


def lever_label(before: ScenarioConfig, cfg: ScenarioConfig, has_preset: bool, max_items: int = 3) -> str:
    """Readable label for a run with levers, e.g. "smaller batches of favorites + Crowd: x1.10 (...)".

    The label is not part of the config hash, so results and caches are unchanged."""
    from .ai.describe import config_changes  # lazy: keeps levers importable without the LLM layer

    changes = config_changes(before, cfg)
    if not changes:
        return before.label if has_preset else "custom"
    text = "; ".join(changes[:max_items]) + (f"; +{len(changes) - max_items} more" if len(changes) > max_items else "")
    return f"{before.label} + {text}" if has_preset else text


def build_config(req: ScenarioRequest) -> ScenarioConfig:
    cfg = load_baseline(req.date)
    if req.preset:
        if req.preset not in PRESETS:
            raise ValueError(f"unknown preset {req.preset!r}; choose from {sorted(PRESETS)}")
        cfg = PRESETS[req.preset](cfg)
    if req.levers:
        before = cfg
        cfg = apply_levers(cfg, req.levers)
        cfg = cfg.updated(label=lever_label(before, cfg, req.preset is not None))
    if req.label:
        cfg = cfg.updated(label=req.label)
    return cfg
