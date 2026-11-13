"""Preset what-if scenarios (SPEC §6.2), as pure config transforms of a baseline."""

from __future__ import annotations

import math
from collections.abc import Callable

from .config import ScenarioConfig


def _kitchen(cfg: ScenarioConfig, dish_id: str, **changes) -> dict:
    kitchen = {k: v.model_dump() for k, v in cfg.kitchen.items()}
    kitchen[dish_id].update(changes)
    return kitchen


def _by_popularity(cfg: ScenarioConfig) -> list[str]:
    """Batch-cooked dishes (to-order dishes have no batch plan), most popular first."""
    batch = [d for d in cfg.menu if d.id in cfg.kitchen]
    return [d.id for d in sorted(batch, key=lambda d: d.popularity, reverse=True)]


def smaller_batches_of_favorites(cfg: ScenarioConfig, top: int = 2) -> ScenarioConfig:
    """Cook favorites in half-size batches, twice as often, with an earlier reorder."""
    for dish_id in _by_popularity(cfg)[:top]:
        p = cfg.kitchen[dish_id]
        half = math.ceil(p.batch_size / 2)
        cfg = cfg.updated(
            kitchen=_kitchen(
                cfg, dish_id,
                batch_size=half,
                starting_batches=p.starting_batches * 2,
                max_batches=p.max_batches * 2 + 2,
                reorder_point=max(p.reorder_point, half),
            )
        )
    return cfg.updated(label="smaller batches of favorites")


def trim_slow_sellers(cfg: ScenarioConfig, bottom: int = 3, factor: float = 0.7) -> ScenarioConfig:
    """Cook the least popular dishes in smaller batches (same number of batches allowed)."""
    for dish_id in _by_popularity(cfg)[-bottom:]:
        p = cfg.kitchen[dish_id]
        cfg = cfg.updated(kitchen=_kitchen(cfg, dish_id, batch_size=max(1, round(p.batch_size * factor))))
    return cfg.updated(label="trim slow sellers")


def extra_server_busiest(cfg: ScenarioConfig) -> ScenarioConfig:
    """+1 server at the staffed station with the most work per server (popularity x service time)."""
    staffed = [s for s in cfg.stations if not s.self_serve]
    load = {
        s.id: sum(d.popularity for d in cfg.menu if d.station == s.id) * s.service_median_min / s.servers
        for s in staffed
    }
    busiest = max(load, key=load.get)
    stations = [s.model_dump() | ({"servers": s.servers + 1} if s.id == busiest else {}) for s in cfg.stations]
    return cfg.updated(stations=stations, label=f"extra server at {busiest}")


def theme_night(cfg: ScenarioConfig, multiplier: float = 1.25) -> ScenarioConfig:
    crowd = cfg.crowd.model_dump() | {"multiplier": cfg.crowd.multiplier * multiplier}
    return cfg.updated(crowd=crowd, label="theme night (+25% crowd)")


def combined_plan(cfg: ScenarioConfig) -> ScenarioConfig:
    cfg = extra_server_busiest(trim_slow_sellers(smaller_batches_of_favorites(cfg)))
    return cfg.updated(label="combined plan")


PRESETS: dict[str, Callable[[ScenarioConfig], ScenarioConfig]] = {
    "smaller_batches": smaller_batches_of_favorites,
    "trim_slow": trim_slow_sellers,
    "extra_server": extra_server_busiest,
    "theme_night": theme_night,
    "combined": combined_plan,
}
