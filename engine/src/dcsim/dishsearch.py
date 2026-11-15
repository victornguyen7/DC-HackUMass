"""Dish search and the dish-popularity check on Tiger (pgvector + pgvectorscale, docs/TIGER.md section 9).

Two questions about dishes, answered from what Tiger holds:

1. **Is each dish's popularity right?** For every recorded night, a dish's observed portions eaten
   (`dish_nightly`, scenario `observed`) is compared with the replay of that same night
   (scenario `replay`: the model with that night's crowd and production). The log ratio per
   dish-night mixes noise with any real per-dish error. `popularity_check` separates the two
   (method-of-moments empirical Bayes): tau is the spread of real per-dish error, and each dish's
   factor is shrunk toward 1 by how many nights back it up.

   The recorded history is SYNTHETIC and its generator has no per-dish popularity error, so on it
   the check finds none (tau ~ 0). That is the honest answer, not a bug: with R04 consumption data
   the same check would measure the real spread. `tests/test_dishsearch.py` shows it recovers a
   known spread from a fixture.

2. **What is a new dish like?** Every dish on every menu date becomes one vector in
   `dcsim.dish_vectors`:

       station    16  md5-hashed one-hot
       role        6  one-hot
       period      2  one-hot (dinner / late night)
       diet        3  multi-hot (vegetarian / vegan / halal)
       allergens  16  md5-hashed multi-hot (menu labels)
       form        4  hot + vessel one-hot (plate / bowl / cup)
       name       64  name words md5-hashed into 64 buckets

   Blocks are unit length and weighted (WEIGHTS); distance is cosine. It is a hand-built feature
   vector, not a learned embedding. The k nearest dishes and what they really sold (portions per
   student) give a new dish its popularity spread in plausible worlds (`new_dish_sd`): dishes that
   look alike but sell differently mean a wider spread.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict

import numpy as np

from .ai.similar import _words

ROLES = ("entree", "starch", "side", "soup", "dessert", "bread")
PERIODS = ("dinner", "late_night")
DIETS = ("vegetarian", "vegan", "halal")
VESSELS = ("plate", "bowl", "cup")
STATION_DIM, ALLERGEN_DIM, NAME_DIM = 16, 16, 64
WEIGHTS = {"station": 1.0, "role": 1.0, "period": 0.5, "diet": 0.6, "allergens": 0.5, "form": 0.4, "name": 1.2}
DIM = STATION_DIM + len(ROLES) + len(PERIODS) + len(DIETS) + ALLERGEN_DIM + 1 + len(VESSELS) + NAME_DIM
MIN_REPLAY_EATEN = 10  # dish-nights with fewer replayed portions are mostly noise
DETECT_TAU = 0.05      # a per-dish popularity error smaller than 5% isn't worth modelling
MIN_DISHES = 20
NEIGHBORS = 5

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS dcsim.dish_vectors (
  dish_id text PRIMARY KEY, name text NOT NULL, station text, role text, period text,
  menu_dates date[], embedding vector({DIM}) NOT NULL
);
"""

# One row per observed dish-night: what was eaten, what the replay of that night ate (mean over seeds),
# and the night's students. dish_nightly buckets by local day, so the date joins straight to nights.night.
EVIDENCE_SQL = """
WITH obs AS (
  SELECT dish_id, (day AT TIME ZONE 'America/New_York')::date AS night, sum(eaten) AS eaten
  FROM dcsim.dish_nightly WHERE scenario = 'observed' AND run_id = 'observed'
  GROUP BY 1, 2),
rep AS (
  SELECT dish_id, (day AT TIME ZONE 'America/New_York')::date AS night, avg(eaten) AS eaten
  FROM dcsim.dish_nightly WHERE scenario = 'replay'
  GROUP BY 1, 2),
st AS (
  SELECT night, max(students) AS students FROM dcsim.nights
  WHERE run_id = 'observed' AND scenario = 'observed' GROUP BY 1)
SELECT o.dish_id, o.night, o.eaten::float8, r.eaten::float8 AS replay_eaten, st.students::float8
FROM obs o LEFT JOIN rep r USING (dish_id, night) LEFT JOIN st USING (night)
WHERE %(ids)s::text[] IS NULL OR o.dish_id = ANY(%(ids)s::text[])
ORDER BY o.dish_id, o.night
"""


# ---------------------------------------------------------------- vectors

def _bucket(key: str, n: int) -> int:
    # md5, not hash(): Python salts str hashes per process, and the vectors must be stable across runs
    return int.from_bytes(hashlib.md5(key.encode()).digest()[:4], "big") % n


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


def _vec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def embed(name: str, station: str | None = None, role: str | None = None, period: str | None = "dinner",
          diet_tags=(), allergens=(), hot: bool | None = None, vessel: str | None = None) -> np.ndarray:
    """A dish as a vector. Anything unknown (None / empty) is an all-zero block, so it adds nothing."""
    st = np.zeros(STATION_DIM)
    if station:
        st[_bucket(station, STATION_DIM)] = 1
    ro = np.zeros(len(ROLES))
    if role in ROLES:
        ro[ROLES.index(role)] = 1
    pe = np.zeros(len(PERIODS))
    if period in PERIODS:
        pe[PERIODS.index(period)] = 1
    di = np.array([1.0 if t in diet_tags else 0.0 for t in DIETS])
    al = np.zeros(ALLERGEN_DIM)
    for a in set(allergens or ()):
        al[_bucket(a.lower(), ALLERGEN_DIM)] = 1
    fo = np.zeros(1 + len(VESSELS))
    if hot is not None:
        fo[0] = 1.0 if hot else 0.0
    if vessel in VESSELS:
        fo[1 + VESSELS.index(vessel)] = 1
    nm = np.zeros(NAME_DIM)
    for w in _words(name):
        nm[_bucket(w, NAME_DIM)] += 1
    blocks = {"station": st, "role": ro, "period": pe, "diet": di, "allergens": al, "form": fo, "name": nm}
    return np.concatenate([WEIGHTS[k] * _unit(v) for k, v in blocks.items()])


def init(conn) -> str:
    """Create the table and its index (DiskANN, else HNSW). Returns the index kind."""
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    old = conn.execute(
        """SELECT a.atttypmod FROM pg_attribute a
           WHERE a.attrelid = to_regclass('dcsim.dish_vectors') AND a.attname = 'embedding'""").fetchone()
    if old and old[0] != DIM:  # derived table: a vector size from an older version is rebuilt, not migrated
        conn.execute("DROP TABLE dcsim.dish_vectors")
    conn.execute(SCHEMA)
    kind = "none"
    try:
        with conn.transaction():
            conn.execute("CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE")
            conn.execute("CREATE INDEX IF NOT EXISTS dish_vectors_diskann ON dcsim.dish_vectors "
                         "USING diskann (embedding vector_cosine_ops)")
        kind = "diskann (pgvectorscale)"
    except Exception:  # noqa: BLE001 - plain pgvector still works
        with conn.transaction():
            conn.execute("CREATE INDEX IF NOT EXISTS dish_vectors_hnsw ON dcsim.dish_vectors "
                         "USING hnsw (embedding vector_cosine_ops)")
        kind = "hnsw (pgvector)"
    conn.execute("COMMENT ON TABLE dcsim.dish_vectors IS 'One hand-built feature vector per dish id (station, role, "
                 "period, diet, allergens, hot/vessel, hashed name words) for the new-dish search (dishsearch.py).'")
    conn.commit()
    return kind


def build(conn) -> int:
    """(Re)embed every dish id in dcsim.dishes, with the attributes of its latest menu date."""
    rows = conn.execute(
        """SELECT DISTINCT ON (dish_id) dish_id, name, station, role, period, vessel, hot,
                  coalesce(diet_tags, '{}'), coalesce(allergens, '{}'),
                  array(SELECT x.menu_date FROM dcsim.dishes x WHERE x.dish_id = d.dish_id ORDER BY 1)
           FROM dcsim.dishes d ORDER BY dish_id, menu_date DESC""").fetchall()
    conn.execute("DELETE FROM dcsim.dish_vectors")
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO dcsim.dish_vectors (dish_id, name, station, role, period, menu_dates, embedding) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::vector)",
            [(i, n, s, r, p, dates, _vec(embed(n, s, r, p, tuple(dt_), tuple(al), h, v)))
             for i, n, s, r, p, v, h, dt_, al, dates in rows])
    conn.commit()
    return len(rows)


def ensure(conn) -> str:
    """The vectors, current: nothing to do when the table has this DIM and one row per dish id in dcsim.dishes
    (so a search runs no DDL); else init + build (the first search after a load or a menu change)."""
    r = conn.execute(
        """SELECT (SELECT a.atttypmod FROM pg_attribute a
                   WHERE a.attrelid = to_regclass('dcsim.dish_vectors') AND a.attname = 'embedding'),
                  to_regclass('dcsim.dish_vectors_diskann') IS NOT NULL""").fetchone()
    if r[0] == DIM:
        have, want = conn.execute("SELECT (SELECT count(*) FROM dcsim.dish_vectors),"
                                  " (SELECT count(DISTINCT dish_id) FROM dcsim.dishes)").fetchone()
        if have and have == want:
            return "diskann (pgvectorscale)" if r[1] else "hnsw (pgvector)"
    kind = init(conn)
    build(conn)
    return kind


def neighbors(conn, vec: np.ndarray, k: int = NEIGHBORS, exclude=()) -> list[dict]:
    """k nearest dishes by cosine distance (the DiskANN index serves the inner ORDER BY)."""
    rows = conn.execute(
        """WITH near AS (
             SELECT dish_id, name, station, role, period, menu_dates, embedding <=> %(q)s::vector AS distance
             FROM dcsim.dish_vectors WHERE NOT (dish_id = ANY(%(ex)s::text[]))
             ORDER BY embedding <=> %(q)s::vector LIMIT %(k)s)
           SELECT * FROM near ORDER BY round(distance::numeric, 4), name""",
        {"q": _vec(vec), "ex": list(exclude), "k": k}).fetchall()
    cols = ("dish_id", "name", "station", "role", "period", "menu_dates", "distance")
    return [dict(zip(cols, r)) | {"menu_dates": [str(d) for d in r[5]], "distance": round(float(r[6]), 4)}
            for r in rows]


# ---------------------------------------------------------------- evidence and the popularity check

def evidence_rows(conn, dish_ids=None) -> list[tuple]:
    return conn.execute(EVIDENCE_SQL, {"ids": None if dish_ids is None else list(dish_ids)}).fetchall()


def summarize(rows) -> dict[str, dict]:
    """Per dish: nights observed, portions eaten per night and per student, and the observed/replay log ratios."""
    by: dict[str, dict] = defaultdict(lambda: {"nights": 0, "eaten": [], "per_student": [], "log_ratio": []})
    for dish_id, _night, eaten, replay, students in rows:
        d = by[dish_id]
        d["nights"] += 1
        d["eaten"].append(float(eaten))
        if students:
            d["per_student"].append(float(eaten) / float(students))
        if replay is not None and replay >= MIN_REPLAY_EATEN:
            d["log_ratio"].append(math.log(max(float(eaten), 1.0) / float(replay)))
    return dict(by)


def popularity_check(log_ratios: dict[str, list[float]]) -> dict:
    """How far off each dish's popularity is, from per-night observed/model log ratios.

    Each dish-night's log ratio = night-wide offset + the dish's real error + noise. After removing
    the overall mean, the spread of dish means is tau^2 + sigma^2/n, so
    tau^2 = max(0, var(dish means) - mean(sigma^2 / n)), with sigma^2 the pooled within-dish variance.
    Each dish's factor is its mean shrunk toward 0 by w = tau^2 / (tau^2 + sigma^2/n)."""
    used = {d: np.asarray(v, float) for d, v in log_ratios.items() if len(v) >= 2}
    if len(used) < 2:
        return {"dishes": len(used), "tau": None, "sigma": None, "detected": False, "factors": {},
                "note": "Not enough dish history in Tiger to check popularity."}
    ss = sum(float(((v - v.mean()) ** 2).sum()) for v in used.values())
    dof = sum(len(v) - 1 for v in used.values())
    sigma2 = ss / dof if dof else 0.0
    means = {d: float(v.mean()) for d, v in used.items()}
    mu = float(np.mean(list(means.values())))
    var_means = float(np.var(list(means.values()), ddof=1))
    tau2 = max(0.0, var_means - float(np.mean([sigma2 / len(v) for v in used.values()])))
    factors = {}
    for d, v in used.items():
        n = len(v)
        w = tau2 / (tau2 + sigma2 / n) if tau2 > 0 else 0.0
        factors[d] = {"nights": n, "factor": round(math.exp(w * (means[d] - mu)), 3),
                      "sd": round(math.sqrt(w * sigma2 / n), 3), "raw": round(math.exp(means[d] - mu), 3)}
    tau = math.sqrt(tau2)
    detected = tau > DETECT_TAU and len(used) >= MIN_DISHES
    return {
        "dishes": len(used), "nights": sum(len(v) for v in used.values()),
        "tau": round(tau, 3), "sigma": round(math.sqrt(sigma2), 3), "detected": detected, "factors": factors,
        "note": (f"Dish popularity is off by about ±{tau:.0%} per dish beyond night-to-night noise."
                 if detected else
                 f"No dish-level popularity error found (±{tau:.0%}, below {DETECT_TAU:.0%}) across {len(used)} dishes. "
                 "The recorded history is synthetic and has none built in; with real consumption data (R04) "
                 "this check would measure it."),
    }


def new_dish_sd(per_student: list[float], default_sd: float) -> tuple[float, float | None]:
    """A new dish's popularity spread: the default spread plus how differently its look-alikes sell
    (sd of log portions per student across the neighbours). Returns (sd, neighbour spread or None)."""
    xs = [math.log(x) for x in per_student if x > 0]
    if len(xs) < 3:
        return default_sd, None
    s = float(np.std(xs, ddof=1))
    return round(math.sqrt(default_sd ** 2 + s ** 2), 3), round(s, 3)


def _dish_brief(s: dict | None) -> dict:
    if not s or not s["nights"]:
        return {"nights": 0, "eaten_per_night": None, "per_student": None}
    return {"nights": s["nights"], "eaten_per_night": round(float(np.mean(s["eaten"])), 1),
            "per_student": round(float(np.mean(s["per_student"])), 4) if s["per_student"] else None}


def evidence(conn, name: str, station: str | None = None, role: str | None = None, period: str | None = "dinner",
             diet_tags=(), hot: bool | None = None, vessel: str | None = None, similar_to=(),
             default_sd: float = 0.3, k: int = NEIGHBORS) -> dict:
    """Everything the new-dish wizard shows: look-alike dishes across all menus with what they sold,
    the chosen similar dishes' history, the new dish's spread, and the popularity check."""
    kind = ensure(conn)
    near = neighbors(conn, embed(name, station, role, period, diet_tags, (), hot, vessel), k=k)
    summ = summarize(evidence_rows(conn))
    check = popularity_check({d: s["log_ratio"] for d, s in summ.items()})
    for n in near:
        n |= _dish_brief(summ.get(n["dish_id"]))
    rates = [n["per_student"] for n in near if n["per_student"]]
    sd, spread = new_dish_sd(rates, default_sd)
    return {
        "query": {"name": name, "station": station, "role": role},
        "neighbors": near,
        "similar_to": [{"dish_id": d} | _dish_brief(summ.get(d)) for d in similar_to],
        "per_student_range": [round(min(rates), 4), round(max(rates), 4)] if rates else None,
        "neighbor_nights": sum(n["nights"] for n in near),
        "new_dish_sd": sd, "neighbor_spread": spread, "default_sd": default_sd,
        "popularity_check": {k: v for k, v in check.items() if k != "factors"},
        "source": "tiger", "index": kind,
        "note": ("Look-alike dishes come from a hand-built feature vector (station, role, diet, allergens, name words), "
                 "not a learned embedding. The new dish's spread is the default ±"
                 f"{default_sd:.0%} plus how differently its look-alikes sold."),
    }


__all__ = ["DIM", "embed", "init", "build", "ensure", "neighbors", "evidence_rows", "summarize",
           "popularity_check", "new_dish_sd", "evidence"]
