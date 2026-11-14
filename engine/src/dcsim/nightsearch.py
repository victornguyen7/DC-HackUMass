"""Similar-night search on Tiger (pgvector + pgvectorscale).

Each observed night becomes one vector in `dcsim.night_vectors`:

    weekday   7  one-hot
    weather   4  one-hot (clear / rain / cold / snow); all zero when unknown
    event     1  event night flag
    menu     64  dish ids feature-hashed into 64 buckets (the dishes put out that night)

Each block is scaled to unit length and weighted (WEIGHTS), so a menu with 40 dishes
doesn't drown out the weekday. Distance is cosine. This is a hand-built feature vector,
not a learned embedding: "similar" means same kind of night and same menu, nothing more.

`similar()` answers "which past nights look most like this one, and what happened on them".
`backtest()` checks whether that answer is any use: for each held-out night it averages
the k nearest *training* nights and compares the error with two plain baselines
(the training mean, and the mean of the same weekday).
"""

from __future__ import annotations

import datetime as dt
import hashlib

import numpy as np

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEATHER = ("clear", "rain", "cold", "snow")
MENU_DIM = 64
WEIGHTS = {"weekday": 1.0, "weather": 0.6, "event": 0.8, "menu": 1.0}
DIM = len(WEEKDAYS) + len(WEATHER) + 1 + MENU_DIM
OUTCOMES = ("students", "waste_lb", "stockout_min")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS dcsim.night_vectors (
  run_id text NOT NULL, night date NOT NULL,
  weekday text, weather text, event_night boolean, n_dishes int,
  embedding vector({DIM}) NOT NULL,
  PRIMARY KEY (run_id, night)
);
"""


def _bucket(dish_id: str) -> int:
    # md5, not hash(): Python salts str hashes per process, and the vectors must be stable across runs
    return int.from_bytes(hashlib.md5(dish_id.encode()).digest()[:4], "big") % MENU_DIM


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


def embed(weekday: str | None, weather: str | None, event: bool, dishes) -> np.ndarray:
    wd = np.zeros(len(WEEKDAYS))
    if weekday in WEEKDAYS:
        wd[WEEKDAYS.index(weekday)] = 1
    we = np.zeros(len(WEATHER))
    if weather in WEATHER:
        we[WEATHER.index(weather)] = 1
    ev = np.array([1.0 if event else 0.0])
    menu = np.zeros(MENU_DIM)
    for d in set(dishes):
        menu[_bucket(d)] += 1
    blocks = [WEIGHTS["weekday"] * wd, WEIGHTS["weather"] * we, WEIGHTS["event"] * ev,
              WEIGHTS["menu"] * _unit(menu)]
    return np.concatenate(blocks)


def _vec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def init(conn) -> str:
    """Create the vector extension(s) and table. Returns the index kind used."""
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute(SCHEMA)
    kind = "none"
    try:
        with conn.transaction():
            conn.execute("CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE")
            conn.execute("CREATE INDEX IF NOT EXISTS night_vectors_diskann ON dcsim.night_vectors "
                         "USING diskann (embedding vector_cosine_ops)")
        kind = "diskann (pgvectorscale)"
    except Exception:  # noqa: BLE001 - plain pgvector still works, just without the DiskANN index
        with conn.transaction():
            conn.execute("CREATE INDEX IF NOT EXISTS night_vectors_hnsw ON dcsim.night_vectors "
                         "USING hnsw (embedding vector_cosine_ops)")
        kind = "hnsw (pgvector)"
    conn.commit()
    return kind


def build(conn, run_ids=("observed", "observed:live")) -> int:
    """(Re)embed every observed night from dcsim.nights + the dishes in dcsim.pan_windows."""
    rows = conn.execute(
        """SELECT n.run_id, n.night, n.weekday, n.weather, coalesce(n.event_night, false),
                  array_agg(DISTINCT p.dish_id)
           FROM dcsim.nights n
           JOIN dcsim.pan_windows p ON p.run_id = n.run_id AND p.night = n.night
           WHERE n.run_id = ANY(%s)
           GROUP BY 1, 2, 3, 4, 5""", (list(run_ids),)).fetchall()
    conn.execute("DELETE FROM dcsim.night_vectors WHERE run_id = ANY(%s)", (list(run_ids),))
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO dcsim.night_vectors (run_id, night, weekday, weather, event_night, n_dishes, embedding) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::vector)",
            [(r, n, wd, we, ev, len(d), _vec(embed(wd, we, ev, d))) for r, n, wd, we, ev, d in rows])
    conn.commit()
    return len(rows)


def query_vector(date: str, weather: str | None = None, event: bool = False) -> tuple[np.ndarray, dict]:
    """Vector for a night that hasn't happened: its weekday, the planned dinner menu, and any known situation."""
    from .data import load_baseline

    day = dt.date.fromisoformat(date)
    cfg = load_baseline(date)
    dishes = [d.id for d in cfg.menu if getattr(d, "period", "dinner") == "dinner"]
    info = {"night": day, "weekday": WEEKDAYS[day.weekday()], "weather": weather, "event_night": event,
            "n_dishes": len(dishes)}
    return embed(info["weekday"], weather, event, dishes), info


def similar(conn, vec: np.ndarray, k: int = 5, exclude: dt.date | None = None,
            before: dt.date | None = None, run_id: str = "observed") -> list[dict]:
    """k nearest observed nights by cosine distance, with what happened on each."""
    rows = conn.execute(
        """SELECT v.night, v.weekday, v.weather, v.event_night, v.n_dishes,
                  v.embedding <=> %s::vector AS distance,
                  n.students, n.waste_lb, n.stockout_min, n.adopted
           FROM dcsim.night_vectors v
           JOIN dcsim.nights n ON n.run_id = v.run_id AND n.night = v.night
           WHERE v.run_id = %s AND v.night IS DISTINCT FROM %s::date AND (%s::date IS NULL OR v.night < %s::date)
           ORDER BY v.embedding <=> %s::vector
           LIMIT %s""",
        (_vec(vec), run_id, exclude, before, before, _vec(vec), k)).fetchall()
    cols = ("night", "weekday", "weather", "event_night", "n_dishes", "distance",
            "students", "waste_lb", "stockout_min", "adopted")
    return [dict(zip(cols, r)) for r in rows]


def backtest(conn, test_from: dt.date, k: int = 5, run_id: str = "observed") -> dict:
    """Held-out check: does the average of the k most similar training nights beat plain averages?"""
    rows = conn.execute(
        """SELECT v.night, v.weekday, v.embedding::text, n.students, n.waste_lb, n.stockout_min
           FROM dcsim.night_vectors v JOIN dcsim.nights n ON n.run_id = v.run_id AND n.night = v.night
           WHERE v.run_id = %s ORDER BY v.night""", (run_id,)).fetchall()
    train = [r for r in rows if r[0] < test_from]
    test = [r for r in rows if r[0] >= test_from]
    if not train or not test:
        return {"nights": 0}
    out = {"train_nights": len(train), "nights": len(test), "k": k}
    for i, m in enumerate(OUTCOMES):
        y_train = np.array([r[3 + i] for r in train], float)
        err = {"knn": [], "train_mean": [], "weekday_mean": []}
        for night, wd, emb, *ys in test:
            y = float(ys[i])
            nn = similar(conn, np.array(parse_vector(emb)), k=k, before=test_from, run_id=run_id)
            err["knn"].append(abs(np.mean([float(x[m]) for x in nn]) - y))
            err["train_mean"].append(abs(y_train.mean() - y))
            same = [float(r[3 + i]) for r in train if r[1] == wd]
            err["weekday_mean"].append(abs((np.mean(same) if same else y_train.mean()) - y))
        out[m] = {name: round(float(np.mean(e)), 1) for name, e in err.items()}
    return out


def parse_vector(text: str) -> list[float]:
    return [float(x) for x in text.strip("[]").split(",")]
