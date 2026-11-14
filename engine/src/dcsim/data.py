"""Load a ScenarioConfig for one Worcester dinner from data files.

Default data dir is <repo>/data/worcester (override with DCSIM_DATA). It holds:
    dc.json, stations.json, personas.json, kitchen_defaults.json, overrides.json
    menus/<date>_dinner.json      written by `dcsim prepare` from the real menu snapshot
    menus/<date>_late_night.json  same, late night 9 pm-12 am (dishes get ids "late.<station>.<name>")
    kitchen/<date>_dinner.json    baseline production plan, written by `dcsim prepare`

`data_version` fingerprints every file the config depends on, so editing any of
them changes every config hash (cached results can never go stale).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from .config import ScenarioConfig

MEAL = "dinner"
LATE = "late_night"
LATE_PREFIX = "late."
_BASE_FILES = ("dc.json", "stations.json", "personas.json", "overrides.json", "kitchen_defaults.json")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_data_dir() -> Path:
    env = os.environ.get("DCSIM_DATA")
    return Path(env) if env else repo_root() / "data" / "worcester"


def raw_menu_files() -> list[Path]:
    return sorted((repo_root() / "data" / "raw" / "menu").glob("worcester_menus_*.json"))


def available_dates(data_dir: Path | None = None) -> list[str]:
    d = data_dir or default_data_dir()
    return sorted(p.name.split("_")[0] for p in (d / "menus").glob(f"*_{MEAL}.json"))


def _read(path: Path) -> dict:
    return json.loads(path.read_text())


def data_version(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def unlimited_kitchen(dish_ids: list[str]) -> dict:
    """Never runs out, never expires: used by the planner to measure raw demand."""
    return {
        i: {"batch_size": 100_000, "starting_batches": 1, "reorder_point": 0, "cook_min": 1, "hold_min": 10_000, "max_batches": 0}
        for i in dish_ids
    }


def load_baseline(
    date: str | None = None,
    data_dir: str | Path | None = None,
    *,
    neutral: bool = False,
    kitchen: dict | str | None = None,
) -> ScenarioConfig:
    """Baseline config for `date` (default: first available).

    neutral=True ignores staff popularity overrides (what the baseline plan assumes).
    kitchen=None loads the prepared plan; "unlimited" or a dict replaces it.
    """
    d = Path(data_dir) if data_dir else default_data_dir()
    dates = available_dates(d)
    if not dates:
        raise FileNotFoundError(f"no menus in {d / 'menus'}; run `dcsim prepare` first")
    date = date or dates[0]
    menu_path = d / "menus" / f"{date}_{MEAL}.json"
    if not menu_path.exists():
        raise FileNotFoundError(f"no {MEAL} menu for {date}; available: {', '.join(dates)}")

    dc = _read(d / "dc.json")
    stations = _read(d / "stations.json")["stations"]
    personas = _read(d / "personas.json")
    defaults = _read(d / "kitchen_defaults.json")
    overrides = _read(d / "overrides.json")
    menu = _read(menu_path)
    late_cfg = dc.get("late_night")
    late_path = d / "menus" / f"{date}_{LATE}.json"
    rows = [x | {"period": "dinner"} for x in menu["dishes"]]
    if late_cfg and late_path.exists():
        rows += [x | {"id": LATE_PREFIX + x["id"], "period": LATE} for x in _read(late_path)["dishes"]]
    else:
        late_cfg = None

    prior = defaults["popularity_prior"]
    staff = {} if neutral else overrides.get("popularity", {})
    # Station-level favorites are known to the kitchen, so they apply to the baseline plan too.
    station_pop = overrides.get("station_popularity", {})
    # Nest-style correction for the logit model: similar items at one station share demand.
    dilution = float(defaults.get("station_dilution", 0.0))
    same = Counter((x["period"], x["station"], x["role"]) for x in rows)
    dishes = [
        {
            "id": x["id"], "name": x["name"], "station": x["station"], "role": x["role"],
            "diet_tags": x["diet_tags"], "vessel": x["vessel"], "hot": x["hot"],
            "serving_size": x.get("serving_size"), "carbon_rating": x.get("carbon_rating"),
            "period": x["period"],
            "popularity": prior[x["role"]]
            * float(staff.get(x["name"], 1.0))
            * float(station_pop.get(x["station"], 1.0))
            / same[(x["period"], x["station"], x["role"])] ** dilution,
        }
        for x in rows
    ]
    used = {x["station"] for x in dishes}
    station_rows = [
        {"name": s.get("display_name", s["names"][0])} | {k: v for k, v in s.items() if k not in ("names", "display_name")}
        for s in stations
        if s["id"] in used
    ]
    to_order = {s["id"] for s in station_rows if s.get("to_order")}
    batch_ids = [x["id"] for x in dishes if x["station"] not in to_order]

    files = [d / f for f in _BASE_FILES] + [menu_path] + ([late_path] if late_cfg else [])
    if kitchen is None:
        kpath = d / "kitchen" / f"{date}_{MEAL}.json"
        if not kpath.exists():
            raise FileNotFoundError(f"no kitchen plan for {date}; run `dcsim prepare`")
        kitchen = _read(kpath)["plans"]
        files.append(kpath)
    elif kitchen == "unlimited":
        kitchen = unlimited_kitchen(batch_ids)

    return ScenarioConfig.model_validate(
        {
            "label": "baseline",
            "dc": dc["dc"],
            "date": date,
            "meal": dc["meal"],
            "open_min": dc["open_min"],
            "close_min": dc["close_min"],
            "late_close_min": late_cfg["close_min"] if late_cfg else None,
            "late_crowd": late_cfg["crowd"] if late_cfg else None,
            "late_plate_size": late_cfg.get("plate_size") if late_cfg else None,
            "layout": dc["layout"],
            "stations": station_rows,
            "crowd": dc["crowd"],
            "arrival_cap_per_min": dc["arrival_cap_per_min"],
            "favorite_patience_mult": dc.get("favorite_patience_mult", 1.0),
            "retry_balked_favorite": dc.get("retry_balked_favorite", False),
            "menu": dishes,
            "kitchen": kitchen,
            "personas": personas["personas"],
            "population_status": personas["status"],
            "data_version": data_version(files),
        }
    )
