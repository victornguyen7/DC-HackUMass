"""Similar Nights: past dining nights whose conditions look most like a target night.

`GET /api/similar-nights` (docs/SIMILAR_NIGHTS.md). A manager gives a date, expected students, weather,
whether it's an event night, and optionally a prepared menu; the search returns the recorded nights
before that date with the most similar conditions, plus what happened on them (waste, stockouts).

The vectors and the ranking are `nightsearch.py`'s, the same ones `dcsim tsdb similar` uses (ISSUES T5):
77 dims (weekday, weather, event night, the menu's dishes hashed into 64 buckets), each block unit length
and weighted, cosine distance. Weather "other" and no menu leave those blocks at zero, so they can't favour
any past night. Expected students is not in the vector (the crowd is an outcome, and the CLI's held-out
check predicts it); it orders nights that are equally similar, closest crowd first. The similarity shown is
cosine over the blocks the query knows (`nightsearch.known_similarity`), so an exact match reads 1.0.
Displayed menu overlap uses exact dish ids (Jaccard). Outcomes never enter the vector.

Storage: with Tiger configured, `nightsearch.build` re-embeds the observed nights into `dcsim.night_vectors`
and the nearest ones come from a pgvectorscale DiskANN index (HNSW without it). If Tiger isn't configured
or can't be reached, the same vectors are ranked locally on data/synthetic/worcester. A failure *after*
connecting raises SearchUnavailable (HTTP 503). The history is SYNTHETIC either way.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import date as Date
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import nightsearch as ns
from .data import available_dates, default_data_dir, repo_root

WEEKDAYS = ns.WEEKDAYS
WEATHERS = (*ns.WEATHER, "other")  # "other" = not sure: the weather block is left out of the match
MAX_STUDENTS = 100_000
MAX_LIMIT = 20
UNAVAILABLE = "Historical search is temporarily unavailable."
METHOD = (f"Cosine similarity of {ns.DIM}-dim condition vectors (the same ones as `dcsim tsdb similar`): "
          "weekday, weather, event night, and the menu's dishes hashed into 64 buckets. Weather 'other' and "
          "no menu are left out of the match. Equally similar nights are ordered by how close their crowd "
          "was to the expected students.")
NOTE = ("Recorded nights are SYNTHETIC until UMass Dining shares real history. Waste and stockouts are what "
        "happened on each matched night, not a forecast for the target date.")


class SearchUnavailable(RuntimeError):
    """Tiger answered but the search failed there (missing schema, no vector support, permissions)."""


@dataclass(frozen=True)
class Night:
    date: str
    students: int
    weather: str  # as recorded (clear, rain, cold or snow)
    event_night: bool
    waste_lb: float | None
    stockout_min: float | None
    dishes: frozenset[str]
    menu_date: str | None = None  # the menu snapshot the (synthetic) night served

    @property
    def weekday(self) -> str:
        return WEEKDAYS[Date.fromisoformat(self.date).weekday()]


# ---------------------------------------------------------------- features

def weather_known(weather: str | None) -> str | None:
    """The weather as the vector sees it: one of nightsearch.WEATHER, or None (not known, ignored)."""
    w = (weather or "").strip().lower()
    return w if w in ns.WEATHER else None


def vector(weekday: str, weather: str | None, event_night: bool, dishes) -> np.ndarray:
    return ns.embed(weekday, weather_known(weather), bool(event_night), dishes or ())


def menu_overlap(query: frozenset[str] | None, night: frozenset[str]) -> float | None:
    if query is None:
        return None
    union = query | night
    return round(len(query & night) / len(union), 3) if union else 0.0


# ---------------------------------------------------------------- inputs

@dataclass(frozen=True)
class Query:
    date: str
    students: int
    weather: str
    event_night: bool
    menu_date: str | None
    limit: int

    @property
    def weekday(self) -> str:
        return WEEKDAYS[Date.fromisoformat(self.date).weekday()]


def _iso(value: str, field: str) -> str:
    try:
        return Date.fromisoformat(value.strip()).isoformat()
    except (ValueError, AttributeError):
        raise ValueError(f"{field} must be a date like 2026-11-14") from None


def validate(date: str, students: int, weather: str = "clear", event_night: bool = False,
             menu_date: str | None = None, limit: int = 5) -> Query:
    """Raises ValueError (HTTP 422) with a message a manager can act on."""
    day = _iso(date, "date")
    if not 1 <= students <= MAX_STUDENTS:
        raise ValueError(f"students must be between 1 and {MAX_STUDENTS:,}")
    w = (weather or "").strip().lower()
    if w not in WEATHERS:
        raise ValueError(f"weather must be one of: {', '.join(WEATHERS)}")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    if menu_date is not None:
        md = _iso(menu_date, "menu_date")
        if md not in available_dates():
            raise ValueError(f"no prepared menu for {md}; leave the menu out to search without one")
        menu_date = md
    return Query(day, int(students), w, bool(event_night), menu_date, int(limit))


def menu_dishes(menu_date: str, data_dir: Path | None = None) -> frozenset[str]:
    """Dish ids on the prepared dinner menu for `menu_date` (add-ons excluded)."""
    path = (data_dir or default_data_dir()) / "menus" / f"{menu_date}_dinner.json"
    return frozenset(d["id"] for d in json.loads(path.read_text())["dishes"])


# ---------------------------------------------------------------- history

def local_history_dir() -> Path:
    return Path(os.environ.get("DCSIM_SIMILAR_HISTORY_DIR") or repo_root() / "data" / "synthetic" / "worcester")


def _num(x: str | None) -> float | None:
    return round(float(x), 1) if x not in (None, "") else None


@lru_cache(maxsize=4)
def _local_history(directory: str, stamp: tuple) -> tuple[Night, ...]:
    d = Path(directory)
    situation = {n["date"]: n for n in json.loads((d / "_ground_truth.json").read_text())["nights"]}
    dishes: dict[str, set[str]] = {}
    with (d / "consumption.csv").open() as f:
        for r in csv.DictReader(f):
            dishes.setdefault(r["date"], set()).add(r["dish_id"])
    out = []
    with (d / "nights.csv").open() as f:
        for r in csv.DictReader(f):
            s = situation.get(r["date"], {})
            out.append(Night(r["date"], int(r["students"]), s.get("weather") or "other", bool(s.get("event_night")),
                             _num(r.get("waste_lb")), _num(r.get("stockout_min")),
                             frozenset(dishes.get(r["date"], ())), r.get("menu_source_date") or None))
    return tuple(sorted(out, key=lambda n: n.date))


def load_local_history(directory: Path | None = None) -> tuple[Night, ...]:
    d = directory or local_history_dir()
    files = [d / "nights.csv", d / "consumption.csv", d / "_ground_truth.json"]
    if not all(p.exists() for p in files):
        return ()
    return _local_history(str(d), tuple(p.stat().st_mtime_ns for p in files))


# ---------------------------------------------------------------- ranking

def _rank_local(q: Query, history: tuple[Night, ...], qdishes: frozenset[str] | None) -> list[tuple[Night, float]]:
    qv = vector(q.weekday, q.weather, q.event_night, qdishes)
    scored = []
    for n in history:
        if n.date >= q.date:
            continue
        nv = vector(n.weekday, n.weather, n.event_night, n.dishes)
        dist = 1 - float(qv @ nv) / (float(np.linalg.norm(qv)) * float(np.linalg.norm(nv)))
        scored.append((n, round(ns.known_similarity(qv, dist), 4)))
    scored.sort(key=lambda t: (-t[1], abs(t[0].students - q.students), t[0].date))
    return scored[: q.limit]


def _match(q: Query, n: Night, sim: float, qdishes: frozenset[str] | None) -> dict:
    return {
        "date": n.date, "similarity": sim, "students": n.students, "weather": n.weather,
        "event_night": n.event_night, "weekday": n.weekday, "waste_lb": n.waste_lb,
        "stockout_min": n.stockout_min, "menu_overlap": menu_overlap(qdishes, n.dishes),
        "same_weekday": n.weekday == q.weekday, "same_weather": n.weather == q.weather,
    }


def _response(q: Query, ranked: list[tuple[Night, float]], qdishes, source: str, history_count: int) -> dict:
    return {
        "query": {"date": q.date, "weekday": q.weekday, "students": q.students, "weather": q.weather,
                  "event_night": q.event_night, "menu_date": q.menu_date},
        "matches": [_match(q, n, s, qdishes) for n, s in ranked],
        "source": source, "synthetic": True, "history_count": history_count,
        "method": METHOD, "note": NOTE,
    }


def search_local(q: Query, history: tuple[Night, ...] | None = None) -> dict:
    history = load_local_history() if history is None else history
    qdishes = menu_dishes(q.menu_date) if q.menu_date else None
    return _response(q, _rank_local(q, history, qdishes), qdishes, "local-synthetic", len(history))


# ---------------------------------------------------------------- Tiger (pgvector)

RUNS = ("observed", "observed:live")


def search_tiger(conn, q: Query) -> dict:
    """Re-embed the observed nights into dcsim.night_vectors, then take the nearest ones from its index."""
    ns.init(conn)
    count = ns.build(conn, RUNS)
    qdishes = menu_dishes(q.menu_date) if q.menu_date else None
    qv = vector(q.weekday, q.weather, q.event_night, qdishes)
    rows = ns.similar(conn, qv, k=q.limit, before=Date.fromisoformat(q.date), run_id=RUNS, students=q.students)
    nights = [r["night"] for r in rows]
    dishes = dict(conn.execute(
        """SELECT night, array_agg(DISTINCT dish_id) FROM dcsim.pan_windows
           WHERE run_id = ANY(%s) AND night = ANY(%s::date[]) GROUP BY night""",
        (list(RUNS), nights)).fetchall()) if nights else {}
    ranked, seen = [], set()
    for r in rows:
        if r["night"] in seen:  # the same night under both observed run ids
            continue
        seen.add(r["night"])
        n = Night(r["night"].isoformat(), int(r["students"] or 0), r["weather"] or "other", bool(r["event_night"]),
                  None if r["waste_lb"] is None else round(float(r["waste_lb"]), 1),
                  None if r["stockout_min"] is None else round(float(r["stockout_min"]), 1),
                  frozenset(dishes.get(r["night"]) or ()))
        ranked.append((n, round(ns.known_similarity(qv, r["distance"]), 4)))
    return _response(q, ranked, qdishes, "tiger-pgvector", count)


def _connect():
    """A Tiger connection, or None when Tiger isn't configured or can't be reached."""
    if os.environ.get("DCSIM_SIMILAR_LOCAL") == "1":
        return None
    from . import tsdb

    if not tsdb.configured():
        return None
    try:
        import psycopg

        return psycopg.connect(_dsn(), connect_timeout=int(os.environ.get("DCSIM_TSDB_TIMEOUT", "8")))
    except Exception:  # noqa: BLE001 - unreachable or misconfigured Tiger falls back to local files
        return None


@lru_cache(maxsize=1)
def _dsn() -> str:
    from . import tsdb

    return tsdb.dsn()  # may shell out to `tiger db uri`; cache it for the life of the process


def search(date: str, students: int, weather: str = "clear", event_night: bool = False,
           menu_date: str | None = None, limit: int = 5) -> dict:
    """Validate, then search Tiger if it's reachable, else the local synthetic files.

    Raises ValueError for bad input and SearchUnavailable when Tiger fails after connecting."""
    q = validate(date, students, weather, event_night, menu_date, limit)
    conn = _connect()
    if conn is None:
        return search_local(q)
    try:
        with conn:
            return search_tiger(conn, q)
    except Exception as e:  # noqa: BLE001
        raise SearchUnavailable(UNAVAILABLE) from e
