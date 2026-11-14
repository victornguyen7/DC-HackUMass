"""Similar Nights: past dining nights whose conditions look most like a target night.

`GET /api/similar-nights` (docs/SIMILAR_NIGHTS.md). A manager gives a date, expected students, weather,
whether it's an event night, and optionally a prepared menu; the search returns the recorded nights
before that date with the most similar conditions, plus what happened on them (waste, stockouts).

Each night is one 48-dim feature vector (hand-built, not a learned embedding):

    dims   feature                                  weight
    0-6    weekday one-hot (Mon..Sun)               1.7
    7-13   neighbouring weekday slots (day +/- 1)   0.25 each
    14-19  weather one-hot (WEATHERS)               1.8
    20-21  event false / true one-hot               0.9
    22-23  attendance: high / low                   1.7 x clamp(...)
    24-47  dish ids, SHA-256 hashed into 24 buckets 1/sqrt(menu size) per dish

The vector is scaled to unit length; similarity is cosine. With no query menu, both sides are compared
on dims 0-23 only, so a candidate's menu size can't move the score. Displayed menu overlap uses exact dish
ids (Jaccard). Outcomes are attached after ranking; they never enter the vector.

Storage: with Tiger configured, the observed history is read from `dcsim.nights` / `dcsim.pan_windows`,
embeddings are upserted into `dcsim.night_embeddings` (pgvector) and ranked there by exact cosine distance.
If Tiger isn't configured or can't be reached, the same ranking runs locally on data/synthetic/worcester.
A failure *after* connecting raises SearchUnavailable (HTTP 503). The history is SYNTHETIC either way.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import date as Date
from functools import lru_cache
from pathlib import Path

import numpy as np

from .data import available_dates, default_data_dir, repo_root

DIM = 48
CONTEXT_DIMS = 24  # weekday + weather + event + attendance; dims 24.. are the menu
MENU_BUCKETS = DIM - CONTEXT_DIMS
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEATHERS = ("clear", "rain", "snow", "cloudy", "windy", "other")
W_WEEKDAY, W_NEIGHBOUR, W_WEATHER, W_EVENT, W_ATTEND = 1.7, 0.25, 1.8, 0.9, 1.7
MAX_STUDENTS = 100_000
MAX_LIMIT = 20
UNAVAILABLE = "Historical search is temporarily unavailable."
METHOD = ("Cosine similarity of 48-dim condition vectors: weekday (with neighbouring days), weather, "
          "event night, attendance, and the menu's dishes hashed into 24 buckets. Without a menu, only "
          "the first 24 dims are compared.")
NOTE = ("Recorded nights are SYNTHETIC until UMass Dining shares real history. Waste and stockouts are what "
        "happened on each matched night, not a forecast for the target date.")


class SearchUnavailable(RuntimeError):
    """Tiger answered but the search failed there (missing schema, no vector support, permissions)."""


@dataclass(frozen=True)
class Night:
    date: str
    students: int
    weather: str  # as recorded (e.g. "cold"); scored through weather_bucket()
    event_night: bool
    waste_lb: float | None
    stockout_min: float | None
    dishes: frozenset[str]
    menu_date: str | None = None  # the menu snapshot the (synthetic) night served

    @property
    def weekday(self) -> str:
        return WEEKDAYS[Date.fromisoformat(self.date).weekday()]


# ---------------------------------------------------------------- features

def weather_bucket(weather: str | None) -> str:
    w = (weather or "").strip().lower()
    return w if w in WEATHERS else "other"


def _dish_bucket(dish_id: str) -> int:
    # sha256, not hash(): Python salts str hashes per process, and stored vectors must be stable
    return int.from_bytes(hashlib.sha256(dish_id.encode()).digest()[:8], "big") % MENU_BUCKETS


def _clamp(x: float, lo: float = 0.0, hi: float = 2.5) -> float:
    return max(lo, min(hi, x))


def feature_vector(day: str, students: int, weather: str, event_night: bool,
                   dishes: frozenset[str] | set[str] | None) -> np.ndarray:
    """Unit-length 48-dim vector. `dishes=None` or empty leaves the menu dims at zero."""
    v = np.zeros(DIM)
    wd = Date.fromisoformat(day).weekday()
    v[wd] = W_WEEKDAY
    v[7 + (wd - 1) % 7] = W_NEIGHBOUR
    v[7 + (wd + 1) % 7] = W_NEIGHBOUR
    v[14 + WEATHERS.index(weather_bucket(weather))] = W_WEATHER
    v[20 + int(bool(event_night))] = W_EVENT
    v[22] = W_ATTEND * _clamp(students / 1500)
    v[23] = W_ATTEND * _clamp((2600 - students) / 1500)
    if dishes:
        share = 1 / math.sqrt(len(dishes))
        for d in dishes:
            v[CONTEXT_DIMS + _dish_bucket(d)] += share
    return v / np.linalg.norm(v)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(a @ b) / (na * nb) if na and nb else 0.0


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
    qv = feature_vector(q.date, q.students, q.weather, q.event_night, qdishes)
    scored = []
    for n in history:
        if n.date >= q.date:
            continue
        nv = feature_vector(n.date, n.students, n.weather, n.event_night, n.dishes)
        sim = cosine(qv, nv) if qdishes is not None else cosine(qv[:CONTEXT_DIMS], nv[:CONTEXT_DIMS])
        scored.append((n, round(sim, 4)))
    scored.sort(key=lambda t: (-t[1], t[0].date))
    return scored[: q.limit]


def _match(q: Query, n: Night, sim: float, qdishes: frozenset[str] | None) -> dict:
    return {
        "date": n.date, "similarity": sim, "students": n.students, "weather": n.weather,
        "event_night": n.event_night, "weekday": n.weekday, "waste_lb": n.waste_lb,
        "stockout_min": n.stockout_min, "menu_overlap": menu_overlap(qdishes, n.dishes),
        "same_weekday": n.weekday == q.weekday, "same_weather": weather_bucket(n.weather) == q.weather,
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

EMBEDDINGS = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS dcsim;
CREATE TABLE IF NOT EXISTS dcsim.night_embeddings (
  night date PRIMARY KEY,
  embedding vector({DIM}) NOT NULL,
  menu_date date,
  updated_at timestamptz NOT NULL DEFAULT now()
);
"""


def _vec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def tiger_history(conn) -> tuple[Night, ...]:
    rows = conn.execute(
        """SELECT DISTINCT ON (night) night, students, weather, event_night, waste_lb, stockout_min, menu_date
           FROM dcsim.nights WHERE source = 'observed' AND scenario = 'observed'
           ORDER BY night, ts DESC""").fetchall()
    dishes = dict(conn.execute(
        """SELECT night, array_agg(DISTINCT dish_id) FROM dcsim.pan_windows
           WHERE source = 'observed' AND scenario = 'observed' GROUP BY night""").fetchall())
    return tuple(
        Night(d.isoformat(), int(students or 0), weather or "other", bool(event),
              None if waste is None else round(float(waste), 1),
              None if stock is None else round(float(stock), 1), frozenset(dishes.get(d) or ()),
              menu.isoformat() if menu else None)
        for d, students, weather, event, waste, stock, menu in rows)


def search_tiger(conn, q: Query) -> dict:
    """Refresh every observed night's embedding, then rank by exact cosine distance in Postgres."""
    history = tiger_history(conn)
    conn.execute(EMBEDDINGS)
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO dcsim.night_embeddings (night, embedding, menu_date, updated_at)
               VALUES (%s, %s::vector, %s, now())
               ON CONFLICT (night) DO UPDATE SET embedding = EXCLUDED.embedding,
                 menu_date = EXCLUDED.menu_date, updated_at = now()""",
            [(n.date, _vec(feature_vector(n.date, n.students, n.weather, n.event_night, n.dishes)), n.menu_date)
             for n in history])
    conn.commit()
    qdishes = menu_dishes(q.menu_date) if q.menu_date else None
    qv = _vec(feature_vector(q.date, q.students, q.weather, q.event_night, qdishes))
    # Unknown menu: compare the condition dims only (pgvector subvector is 1-based).
    dist = ("e.embedding <=> %(q)s::vector" if qdishes is not None
            else f"subvector(e.embedding, 1, {CONTEXT_DIMS}) <=> subvector(%(q)s::vector, 1, {CONTEXT_DIMS})")
    observed = [n.date for n in history]
    rows = conn.execute(
        f"""SELECT e.night, 1 - ({dist}) AS sim FROM dcsim.night_embeddings e
            WHERE e.night = ANY(%(observed)s::date[]) AND e.night < %(before)s::date
            ORDER BY {dist}, e.night LIMIT %(k)s""",
        {"q": qv, "observed": observed, "before": q.date, "k": q.limit}).fetchall()
    by_date = {n.date: n for n in history}
    ranked = [(by_date[d.isoformat()], round(float(s), 4)) for d, s in rows]
    ranked.sort(key=lambda t: (-t[1], t[0].date))  # equal rounded scores: earlier date first
    return _response(q, ranked, qdishes, "tiger-pgvector", len(history))


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
