"""Import the real umassdining.com menu snapshot into per-date engine menus.

Each published item is classified into a role (entree, starch, side, soup,
dessert, bread) or "addon" (sauces, cheese, chutneys, tortillas, toppings).
Add-ons stay on the menu for the dashboard but are not simulated. Wrong calls
are fixed by name in data/worcester/overrides.json -> "roles".
"""

from __future__ import annotations

import json
import re
from fractions import Fraction
from pathlib import Path

ADDON = "addon"

_ADDON_RE = re.compile(
    r"\b(sauce|chutney|dressing|gravy|salsa|pico de gallo|guacamole|sour cream|cilantro|scallions?|"
    r"lime wedges?|jalapenos?|tortillas?|taco shells|chips|parmesan|cheddar|shredded .*cheese|"
    r"mayu|oil|lettuce|pickled|syrup|whipped|butter|aioli|vinaigrette|relish|pesto|marinara|alfredo|"
    r"croutons|seeds$)\b",
    re.I,
)
_NOODLE_MAIN_RE = re.compile(r"\b(ramen|pho broth|pho base|hot pot|tom yum|soup|taiwan pork|bowl|bar)\b", re.I)
_DESSERT_RE = re.compile(r"\b(cake|cookies?|brownies?|pie|crisp|pudding|sago|mochi|tart|cobbler|waffles?|cupcakes?|bars?)\b", re.I)
_STARCH_RE = re.compile(
    r"\b(rice|pasta|penne|linguini|orecchiette|noodles|mei fun|chap che|japchae|potato(es)?|fries|naan|"
    r"bread|baguette|quinoa|couscous|polenta|stuffing)\b",
    re.I,
)
_PROTEIN_RE = re.compile(
    r"\b(chicken|beef|pork|fish|salmon|shrimp|prawns?|mussels|tofu|paneer|turkey|burger|patty|lamb|steak|"
    r"meatballs?|bulgogi|stew|masala|parmesan|eggplant|filling|tikka|birria|carved|vindaloo|korma|saag)\b",
    re.I,
)
_BOWL_RE = re.compile(r"\b(soup|chowder|stew|curry|masala|ramen|pho|hot pot|tom yum|salad|chili|bisque)\b", re.I)
_COLD_RE = re.compile(r"\b(salad|pickled|sushi|roll)\b", re.I)

STATION_ROLE = {"soups": "soup", "desserts": "dessert", "breads": "bread"}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def serving_oz(serving: str | None) -> float | None:
    """'5 2/3 OZL' -> 5.667; '1/4 OZ' -> 0.25; '1 each' -> None."""
    if not serving:
        return None
    m = re.match(r"^\s*([\d\s/\.]+?)\s*ozl?\b", serving, re.I)
    if not m:
        return None
    try:
        return float(sum(Fraction(p) for p in m.group(1).split()))
    except (ValueError, ZeroDivisionError):
        return None


def classify(station_id: str, name: str, serving: str | None) -> str:
    if station_id in STATION_ROLE:
        return STATION_ROLE[station_id]
    if station_id == "sushi":
        return "entree"
    if station_id == "noodle_bowl":
        return "entree" if _NOODLE_MAIN_RE.search(name) else ADDON
    oz = serving_oz(serving)
    if re.search(r"\bfilling\b", name, re.I):
        return "entree"
    if _ADDON_RE.search(name) and not _PROTEIN_RE.search(name.replace("Parmesan Cheese", "")):
        return ADDON
    if oz is not None and oz <= 1.0:
        return ADDON
    if _DESSERT_RE.search(name):
        return "dessert"
    if _STARCH_RE.search(name):
        return "starch"
    if _PROTEIN_RE.search(name):
        return "entree"
    if station_id == "sushi" or oz is None or oz >= 4:
        return "entree"
    return "side"


def diet_tags(labels: list[str]) -> list[str]:
    s = {x.strip().lower() for x in labels}
    tags = set()
    if "plant based" in s:
        tags |= {"vegan", "vegetarian"}
    if "vegetarian" in s:
        tags.add("vegetarian")
    if "halal" in s:
        tags.add("halal")
    return sorted(tags)


def _station_index(stations: list[dict]) -> dict[str, str]:
    return {name: s["id"] for s in stations for name in s["names"]}


def import_menus(raw_path: Path, stations_path: Path, overrides_path: Path, out_dir: Path, meal: str = "dinner") -> list[Path]:
    raw = json.loads(raw_path.read_text())
    stations = json.loads(stations_path.read_text())["stations"]
    by_name = _station_index(stations)
    role_overrides = json.loads(overrides_path.read_text()).get("roles", {}) if overrides_path.exists() else {}
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for day in raw["dates"]:
        m = day["meals"].get(meal) or {}
        if m.get("status") != "published":
            continue
        dishes, addons, seen = [], [], set()
        for st in m.get("stations", []):
            if st["name"] not in by_name:
                raise ValueError(f"{day['date']}: unknown station {st['name']!r}; add it to {stations_path.name}")
            sid = by_name[st["name"]]
            for it in st["items"]:
                dish_id = f"{sid}.{slug(it['name'])}"
                if dish_id in seen:
                    continue
                seen.add(dish_id)
                role = role_overrides.get(it["name"]) or classify(sid, it["name"], it.get("serving_size"))
                row = {
                    "id": dish_id,
                    "name": it["name"],
                    "station": sid,
                    "station_name": st["name"],
                    "serving_size": it.get("serving_size"),
                    "dietary_labels": it.get("dietary_labels", []),
                    "allergens": it.get("allergens", []),
                    "carbon_rating": it.get("carbon_rating"),
                    "healthfulness": it.get("healthfulness"),
                    "recipe_webcode": it.get("recipe_webcode"),
                }
                if role == ADDON:
                    addons.append(row)
                    continue
                row |= {
                    "role": role,
                    "diet_tags": diet_tags(it.get("dietary_labels", [])),
                    "vessel": "bowl" if (role == "soup" or sid == "noodle_bowl" or _BOWL_RE.search(it["name"])) else "plate",
                    "hot": role in ("entree", "starch", "side", "soup") and sid != "sushi" and not _COLD_RE.search(it["name"]),
                }
                dishes.append(row)
        out = out_dir / f"{day['date']}_{meal}.json"
        out.write_text(json.dumps({
            "date": day["date"],
            "meal": meal,
            "dc": "worcester",
            "source": {"file": raw_path.name, "url": day.get("source_url"), "retrieved_at": day.get("retrieved_at")},
            "dishes": dishes,
            "addons": addons,
        }, indent=1))
        written.append(out)
    return written
