"""Observed nights and simulated runs side by side in Tiger Data (TimescaleDB).

Everything lives in schema `dcsim`. Both sides share the same tables, told
apart by `source` ('observed' | 'simulated') and `scenario`:

    observed             the recorded history (SYNTHETIC in the hackathon; MetaFoodX/LeanPath shape)
    forecast_naive       before the night: plan as written, expected crowd (what the app shows today)
    forecast_calibrated  before the night: crowd and production drawn from what the training weeks showed
    replay               after the night: plan as executed, that night's observed crowd and production ratio

Tables (hypertables when TimescaleDB is present, plain tables otherwise):

    nights        one row per night (observed) or per simulated night (seed x draw), with situation
                  columns copied onto every row (weekday, weather, event_night, adopted, split)
    pan_windows   per dish per 30-min window: portions put out / eaten / left (consumption.csv shape)
    waste_log     observed weigh-ins (expired / close / plate)
    planned_batches  the production plan each observed night was meant to follow (batch size per dish)
    calibrations  one row per `dcsim calibrate`: fitted parameters, held-out scores, recovery check
    forecasts     append-only ledger of forecasts issued before a night (trigger blocks UPDATE/DELETE/TRUNCATE);
                  view forecast_scorecard scores each one once the night is observed

Continuous aggregate `dish_nightly` rolls pan_windows up to one row per dish per night per run.

Connection: DCSIM_TSDB_URL (env or engine/.env), else `tiger db uri --with-password`
for TIGER_SERVICE_ID (or the CLI's default service).
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import shutil
import subprocess
from collections.abc import Iterable
from zoneinfo import ZoneInfo

from .data import load_baseline

TZ = ZoneInfo("America/New_York")
TEST_FROM = dt.date(2026, 10, 29)  # last 2 of the 6 recorded weeks are held out

NIGHT_COLS = (
    "ts", "night", "source", "scenario", "run_id", "draw", "seed", "menu_date", "plan", "weekday", "weather",
    "event_night", "adopted", "split", "crowd_multiplier", "over_prep", "students", "cooked_portions",
    "waste_portions", "waste_lb", "expired_lb", "close_lb", "plate_lb", "stockout_min",
)
PAN_COLS = ("ts", "night", "source", "scenario", "run_id", "seed", "station", "dish_id", "dish",
            "portions_out", "portions_eaten", "portions_left")
WASTE_COLS = ("ts", "night", "station", "dish_id", "dish", "portions", "weight_lb", "reason")
PLAN_COLS = ("menu_date", "plan", "dish_id", "station", "batch_size", "starting_batches", "reorder_point", "max_batches")

TABLES = """
CREATE SCHEMA IF NOT EXISTS dcsim;
CREATE TABLE IF NOT EXISTS dcsim.nights (
  ts timestamptz NOT NULL, night date NOT NULL,
  source text NOT NULL CHECK (source IN ('observed', 'simulated')),
  scenario text NOT NULL, run_id text NOT NULL, draw int NOT NULL DEFAULT 0, seed int NOT NULL DEFAULT 0,
  menu_date date, plan text, weekday text, weather text, event_night boolean, adopted boolean, split text,
  crowd_multiplier double precision, over_prep double precision,
  students int, cooked_portions int, waste_portions int, waste_lb double precision,
  expired_lb double precision, close_lb double precision, plate_lb double precision, stockout_min double precision,
  PRIMARY KEY (run_id, draw, seed, ts)
);
CREATE INDEX IF NOT EXISTS nights_scenario_idx ON dcsim.nights (scenario, night);
CREATE TABLE IF NOT EXISTS dcsim.pan_windows (
  ts timestamptz NOT NULL, night date NOT NULL, source text NOT NULL, scenario text NOT NULL,
  run_id text NOT NULL, seed int NOT NULL DEFAULT 0,
  station text, dish_id text NOT NULL, dish text,
  portions_out int, portions_eaten int, portions_left int
);
CREATE INDEX IF NOT EXISTS pan_windows_dish_idx ON dcsim.pan_windows (dish_id, ts);
CREATE INDEX IF NOT EXISTS pan_windows_scenario_idx ON dcsim.pan_windows (scenario, ts);
CREATE TABLE IF NOT EXISTS dcsim.waste_log (
  ts timestamptz NOT NULL, night date NOT NULL, station text, dish_id text, dish text,
  portions int, weight_lb double precision, reason text
);
CREATE TABLE IF NOT EXISTS dcsim.planned_batches (
  menu_date date NOT NULL, plan text NOT NULL, dish_id text NOT NULL, station text,
  batch_size int, starting_batches int, reorder_point int, max_batches int,
  PRIMARY KEY (menu_date, plan, dish_id)
);
CREATE TABLE IF NOT EXISTS dcsim.forecasts (
  forecast_id text PRIMARY KEY,
  issued_at timestamptz NOT NULL DEFAULT now(),   -- database clock, never the client's
  for_night date NOT NULL, method text NOT NULL CHECK (method IN ('naive', 'calibrated')),
  label text, config_hash text, scenario_id text, calibration_id text, engine_version text, n_nights int,
  students_p10 double precision, students_p50 double precision, students_p90 double precision,
  waste_p10 double precision, waste_p50 double precision, waste_p90 double precision,
  stockout_p10 double precision, stockout_p50 double precision, stockout_p90 double precision,
  cooked_p10 double precision, cooked_p50 double precision, cooked_p90 double precision,
  samples jsonb
);
CREATE INDEX IF NOT EXISTS forecasts_night_idx ON dcsim.forecasts (for_night, issued_at);
CREATE OR REPLACE FUNCTION dcsim.forecasts_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'dcsim.forecasts is append-only: % is not allowed (issue a new forecast instead)', TG_OP;
END $$;
DROP TRIGGER IF EXISTS forecasts_no_change ON dcsim.forecasts;
CREATE TRIGGER forecasts_no_change BEFORE UPDATE OR DELETE ON dcsim.forecasts
  FOR EACH ROW EXECUTE FUNCTION dcsim.forecasts_append_only();
DROP TRIGGER IF EXISTS forecasts_no_truncate ON dcsim.forecasts;
CREATE TRIGGER forecasts_no_truncate BEFORE TRUNCATE ON dcsim.forecasts
  FOR EACH STATEMENT EXECUTE FUNCTION dcsim.forecasts_append_only();
CREATE TABLE IF NOT EXISTS dcsim.calibrations (
  calibration_id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
  train_from date, train_to date, test_from date, test_to date,
  engine_version text, params jsonb, scores jsonb, recovery jsonb
);
"""

HYPERTABLES = """
SELECT create_hypertable('dcsim.nights', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
SELECT create_hypertable('dcsim.pan_windows', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
SELECT create_hypertable('dcsim.waste_log', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
"""

# Columnstore (compression): segment by what queries and calibrate's delete-and-reload filter on,
# so a re-run drops whole compressed segments. Chunks older than a day convert automatically.
COLUMNSTORE = {
    "nights": ("scenario", "ts, run_id, draw, seed"),
    "pan_windows": ("scenario, dish_id", "ts, seed"),
    "waste_log": ("dish_id", "ts"),
}
COLUMNSTORE_AFTER = "1 day"


# One row per dish per night per run (and seed). Dinner runs 4:30-9 pm Eastern, which
# crosses midnight UTC, so the bucket is taken in local time.
DISH_NIGHTLY_SELECT = """
SELECT {bucket} AS day, source, scenario, run_id, seed, station, dish_id,
       sum(portions_out) AS cooked, sum(portions_eaten) AS eaten,
       {last_left} AS left_at_close,
       count(*) FILTER (WHERE portions_left = 0) AS windows_empty
FROM dcsim.pan_windows
GROUP BY 1, source, scenario, run_id, seed, station, dish_id
"""
CAGG = ("CREATE MATERIALIZED VIEW IF NOT EXISTS dcsim.dish_nightly WITH (timescaledb.continuous) AS"
        + DISH_NIGHTLY_SELECT.format(bucket="time_bucket('1 day', ts, 'America/New_York')",
                                     last_left="last(portions_left, ts)")
        + " WITH NO DATA")
PLAIN_VIEW = ("CREATE OR REPLACE VIEW dcsim.dish_nightly AS"
              + DISH_NIGHTLY_SELECT.format(bucket="date_trunc('day', ts AT TIME ZONE 'America/New_York')",
                                           last_left="(array_agg(portions_left ORDER BY ts DESC))[1]"))

# Per observed night: where the observation fell inside each simulated scenario's spread.
FORECAST_CHECK = """
CREATE OR REPLACE VIEW dcsim.forecast_check AS
WITH sim AS (
  SELECT night, scenario, count(*) AS n,
         percentile_cont(0.1) WITHIN GROUP (ORDER BY waste_lb) AS waste_p10,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY waste_lb) AS waste_p50,
         percentile_cont(0.9) WITHIN GROUP (ORDER BY waste_lb) AS waste_p90,
         percentile_cont(0.1) WITHIN GROUP (ORDER BY stockout_min) AS stockout_p10,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY stockout_min) AS stockout_p50,
         percentile_cont(0.9) WITHIN GROUP (ORDER BY stockout_min) AS stockout_p90,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY students) AS students_p50
  FROM dcsim.nights WHERE source = 'simulated' GROUP BY night, scenario
)
SELECT o.night, o.weekday, o.weather, o.event_night, o.adopted, o.split, s.scenario, s.n,
       o.students, s.students_p50,
       o.waste_lb, s.waste_p10, s.waste_p50, s.waste_p90,
       o.waste_lb BETWEEN s.waste_p10 AND s.waste_p90 AS waste_inside,
       o.stockout_min, s.stockout_p10, s.stockout_p50, s.stockout_p90,
       o.stockout_min BETWEEN s.stockout_p10 AND s.stockout_p90 AS stockout_inside
FROM dcsim.nights o JOIN sim s USING (night)
WHERE o.source = 'observed';
"""


# Every logged forecast next to what then happened ('awaiting' until the night is observed).
SCORECARD = """
CREATE OR REPLACE VIEW dcsim.forecast_scorecard AS
SELECT f.forecast_id, f.for_night, f.method, f.label, f.issued_at, o.ts AS doors_open,
       CASE WHEN o.ts IS NULL THEN NULL ELSE f.issued_at < o.ts END AS issued_before_open,
       f.waste_p10, f.waste_p50, f.waste_p90, o.waste_lb AS observed_waste_lb,
       CASE WHEN o.night IS NULL THEN 'awaiting'
            WHEN o.waste_lb BETWEEN f.waste_p10 AND f.waste_p90 THEN 'inside' ELSE 'outside' END AS waste_result,
       f.stockout_p10, f.stockout_p50, f.stockout_p90, o.stockout_min AS observed_stockout_min,
       CASE WHEN o.night IS NULL THEN 'awaiting'
            WHEN o.stockout_min BETWEEN f.stockout_p10 AND f.stockout_p90 THEN 'inside' ELSE 'outside' END AS stockout_result,
       f.students_p10, f.students_p50, f.students_p90, o.students AS observed_students,
       f.config_hash, f.scenario_id, f.calibration_id
FROM dcsim.forecasts f
LEFT JOIN dcsim.nights o ON o.night = f.for_night AND o.scenario = 'observed';
"""

HISTORY_RUN = "observed"  # run_id of the recorded history; nights observed after a logged forecast use LIVE_RUN
LIVE_RUN = "observed:live"


# ------------------------------------------------------------------ connection

def dsn() -> str:
    from .ai.llm import load_env_file

    load_env_file()
    url = os.environ.get("DCSIM_TSDB_URL")
    if url:
        return url
    if not shutil.which("tiger"):
        raise RuntimeError("set DCSIM_TSDB_URL in engine/.env, or install the Tiger CLI")
    cmd = ["tiger", "db", "uri", "--with-password"]
    if os.environ.get("TIGER_SERVICE_ID"):
        cmd.append(os.environ["TIGER_SERVICE_ID"])
    r = subprocess.run(cmd, capture_output=True, text=True)
    url = r.stdout.strip()
    if r.returncode or not url.startswith("postgres"):
        raise RuntimeError(f"`tiger db uri` failed: {(r.stderr or r.stdout).strip()[:300]}")
    if ":" not in url.split("@")[0].removeprefix("postgresql://").removeprefix("postgres://"):
        raise RuntimeError("Tiger has no saved password for this service: run `tiger db save-password <service-id>` "
                           "(or put DCSIM_TSDB_URL in engine/.env)")
    return url


def connect(url: str | None = None, autocommit: bool = False):
    import psycopg

    return psycopg.connect(url or dsn(), autocommit=autocommit)


def has_timescale(conn) -> bool:
    return conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'").fetchone() is not None


def init_schema(conn) -> bool:
    """Create tables (idempotent). Returns True when TimescaleDB features were used."""
    try:
        conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    except Exception:
        conn.rollback()
    ts = has_timescale(conn)
    conn.execute(TABLES)
    if ts:
        conn.execute(HYPERTABLES)
        conn.execute(CAGG)
        enabled = {r[0] for r in conn.execute(
            "SELECT hypertable_name FROM timescaledb_information.hypertables"
            " WHERE hypertable_schema = 'dcsim' AND compression_enabled")}
        for t, (seg, order) in COLUMNSTORE.items():
            if t not in enabled:
                conn.execute(f"ALTER TABLE dcsim.{t} SET (timescaledb.enable_columnstore,"
                             f" timescaledb.segmentby = '{seg}', timescaledb.orderby = '{order}')")
            conn.execute(f"CALL add_columnstore_policy('dcsim.{t}', after => INTERVAL '{COLUMNSTORE_AFTER}',"
                         " if_not_exists => true)")
    else:
        conn.execute(PLAIN_VIEW)
    conn.execute(FORECAST_CHECK)
    conn.execute(SCORECARD)
    conn.commit()
    return ts


def configured() -> bool:
    """True when a Tiger connection is set up (the API only logs forecasts then)."""
    from .ai.llm import load_env_file

    load_env_file()
    return bool(os.environ.get("DCSIM_TSDB_URL") or os.environ.get("TIGER_SERVICE_ID"))


def refresh(conn) -> None:
    """Bring the continuous aggregate up to date (needs a non-transaction connection)."""
    if not has_timescale(conn):
        return
    if not conn.autocommit:
        conn.commit()
        conn.autocommit = True
        try:
            conn.execute("CALL refresh_continuous_aggregate('dcsim.dish_nightly', NULL, NULL)")
        finally:
            conn.autocommit = False
    else:
        conn.execute("CALL refresh_continuous_aggregate('dcsim.dish_nightly', NULL, NULL)")


def table_sizes(conn) -> dict[str, int]:
    if not has_timescale(conn):
        return {t: conn.execute(f"SELECT pg_total_relation_size('dcsim.{t}')").fetchone()[0] for t in COLUMNSTORE}
    return {t: conn.execute(f"SELECT hypertable_size('dcsim.{t}')").fetchone()[0] for t in COLUMNSTORE}


def compress_now(conn) -> dict:
    """Convert (or recompress) every chunk older than COLUMNSTORE_AFTER to the columnstore now, then
    VACUUM. The policy would get there on its own schedule. Returns sizes before and after, in bytes."""
    before = table_sizes(conn)
    n = 0
    if has_timescale(conn):
        for t in COLUMNSTORE:
            n += len(conn.execute(
                f"SELECT compress_chunk(c, if_not_compressed => true, recompress => true) FROM show_chunks('dcsim.{t}',"
                f" older_than => now() - INTERVAL '{COLUMNSTORE_AFTER}') c").fetchall())
        conn.commit()
        # calibrate's delete-and-reload leaves dead row-store pages behind; reclaim them so sizes are real
        conn.autocommit = True
        try:
            for t in COLUMNSTORE:
                conn.execute(f"VACUUM ANALYZE dcsim.{t}")
        finally:
            conn.autocommit = False
    out = {"chunks": n, "before": before, "after": table_sizes(conn), "rowstore": {}, "columnstore": {}}
    if has_timescale(conn):
        for t in COLUMNSTORE:
            b, a = conn.execute("SELECT coalesce(sum(before_compression_total_bytes), 0),"
                                f" coalesce(sum(after_compression_total_bytes), 0) FROM chunk_columnstore_stats('dcsim.{t}')"
                                ).fetchone()
            out["rowstore"][t], out["columnstore"][t] = int(b), int(a)
    return out


# ------------------------------------------------------------------ writes

def copy_rows(conn, table: str, cols: tuple[str, ...], rows: Iterable[dict]) -> int:
    n = 0
    with conn.cursor() as cur, cur.copy(f"COPY dcsim.{table} ({', '.join(cols)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row([r.get(c, 0 if c in ("draw", "seed") else None) for c in cols])  # COPY skips DEFAULTs
            n += 1
    return n


def delete_scenario(conn, scenario: str) -> None:
    for t in ("nights", "pan_windows"):
        conn.execute(f"DELETE FROM dcsim.{t} WHERE scenario = %s", (scenario,))


def at(night: dt.date | str, minutes: float) -> dt.datetime:
    d = dt.date.fromisoformat(night) if isinstance(night, str) else night
    return dt.datetime.combine(d, dt.time(), TZ) + dt.timedelta(minutes=minutes)


def hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def split_of(night: dt.date | str, test_from: dt.date = TEST_FROM) -> str:
    d = dt.date.fromisoformat(night) if isinstance(night, str) else night
    return "test" if d >= test_from else "train"


def observed_context() -> dict[str, dict]:
    """Weather and event flags per night: things a manager knows on the day (not the hidden noise).

    Only these two fields are read from the generator's ground truth; crowd multiplier,
    over-prep and plate-waste share stay hidden and are never loaded into the database.
    """
    from .synthetic import night_of, out_dir

    p = out_dir() / "_ground_truth.json"
    if not p.exists():
        return {}
    return {n["date"]: {"weather": n["weather"], "event_night": n["event_night"]}
            for n in json.loads(p.read_text())["nights"]}


def load_observed(conn, test_from: dt.date = TEST_FROM) -> dict:
    """data/synthetic/worcester/*.csv -> nights / pan_windows / waste_log (source='observed')."""
    from .synthetic import night_of, out_dir

    d = out_dir()
    if not (d / "nights.csv").exists():
        raise FileNotFoundError("no recorded history; run `dcsim waste-gen` first")
    ctx = observed_context()
    opens: dict[str, int] = {}

    def open_min(menu_date: str) -> int:
        if menu_date not in opens:
            opens[menu_date] = load_baseline(menu_date).open_min
        return opens[menu_date]

    nights = []
    menu_of = {}
    for r in csv.DictReader((d / "nights.csv").open()):
        menu_of[r["date"]] = r["menu_source_date"]
        adopted = r["adopted"] == "True"
        nights.append({
            "ts": at(r["date"], open_min(r["menu_source_date"])), "night": r["date"], "source": "observed",
            "scenario": "observed", "run_id": "observed", "menu_date": r["menu_source_date"],
            "plan": "recommended" if adopted else "baseline", "weekday": r["weekday"],
            **ctx.get(r["date"], {}), "adopted": adopted, "split": split_of(r["date"], test_from),
            "students": int(float(r["students"])), "cooked_portions": int(float(r["cooked_portions"])),
            "waste_portions": int(float(r["waste_portions"])), "waste_lb": float(r["waste_lb"]),
            "expired_lb": float(r["expired_lb"]), "close_lb": float(r["close_lb"]),
            "plate_lb": float(r["plate_lb"]), "stockout_min": float(r["stockout_min"]),
        })
    pans = ({
        "ts": at(r["date"], hhmm_to_min(r["window_start"])), "night": r["date"], "source": "observed",
        "scenario": "observed", "run_id": "observed", "station": r["station"], "dish_id": r["dish_id"],
        "dish": r["dish"], "portions_out": int(r["portions_out"]), "portions_eaten": int(r["portions_eaten"]),
        "portions_left": int(r["portions_left"]),
    } for r in csv.DictReader((d / "consumption.csv").open()))
    waste = ({
        "ts": dt.datetime.fromisoformat(r["timestamp"]).replace(tzinfo=TZ), "night": night_of(r["timestamp"]),
        "station": r["station"], "dish_id": r["dish_id"], "dish": r["dish"],
        "portions": int(r["portions"]) if r["portions"] else None, "weight_lb": float(r["weight_lb"]),
        "reason": r["reason"],
    } for r in csv.DictReader((d / "waste_log.csv").open()))

    from .calibration import executed_plan

    plans = []
    for menu_date, adopted in sorted({(n["menu_date"], n["adopted"]) for n in nights}):
        cfg = executed_plan(menu_date, adopted)
        st = {x.id: x.station for x in cfg.menu}
        plans += [{"menu_date": menu_date, "plan": "recommended" if adopted else "baseline", "dish_id": k,
                   "station": st.get(k), **b.model_dump(include={"batch_size", "starting_batches", "reorder_point", "max_batches"})}
                  for k, b in cfg.kitchen.items()]

    hist = [n["night"] for n in nights]
    for t in ("nights", "pan_windows"):
        conn.execute(f"DELETE FROM dcsim.{t} WHERE run_id = %s", (HISTORY_RUN,))
    conn.execute("DELETE FROM dcsim.waste_log WHERE night BETWEEN %s AND %s", (min(hist), max(hist)))
    conn.execute("DELETE FROM dcsim.planned_batches WHERE plan IN ('baseline', 'recommended') AND menu_date = ANY(%s)",
                 ([n["menu_date"] for n in nights],))
    out = {
        "planned_batches": copy_rows(conn, "planned_batches", PLAN_COLS, plans),
        "nights": copy_rows(conn, "nights", NIGHT_COLS, nights),
        "pan_windows": copy_rows(conn, "pan_windows", PAN_COLS, pans),
        "waste_log": copy_rows(conn, "waste_log", WASTE_COLS, waste),
    }
    conn.commit()
    refresh(conn)
    return out


def observed_nights(conn) -> list[dict]:
    """Observed nights as the calibration reads them. Production comes from the pan windows
    (what a MetaFoodX export records), not from the generator's summary file."""
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute("""
            SELECT n.night::text AS night, n.menu_date::text AS menu_date, n.weekday, n.weather, n.event_night,
                   n.adopted, n.split, n.students, n.waste_lb, n.stockout_min,
                   p.cooked AS cooked_portions
            FROM dcsim.nights n
            JOIN (SELECT night, sum(portions_out)::int AS cooked FROM dcsim.pan_windows
                  WHERE run_id = 'observed' GROUP BY night) p USING (night)
            WHERE n.scenario = 'observed' AND n.run_id = 'observed' ORDER BY n.night
        """).fetchall()


# ------------------------------------------------------------------ forecast ledger

FORECAST_COLS = ("forecast_id", "for_night", "method", "label", "config_hash", "scenario_id", "calibration_id",
                 "engine_version", "n_nights", "students_p10", "students_p50", "students_p90", "waste_p10", "waste_p50",
                 "waste_p90", "stockout_p10", "stockout_p50", "stockout_p90", "cooked_p10", "cooked_p50", "cooked_p90",
                 "samples")


def latest_calibration(conn) -> dict | None:
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute("SELECT calibration_id, params FROM dcsim.calibrations ORDER BY created_at DESC LIMIT 1").fetchone()


def insert_forecast(conn, row: dict) -> dict:
    """Append one forecast; the database stamps issued_at. Returns the stored id and time."""
    from psycopg.types.json import Jsonb

    vals = [Jsonb(row[c]) if c == "samples" else row.get(c) for c in FORECAST_COLS]
    r = conn.execute(f"INSERT INTO dcsim.forecasts ({', '.join(FORECAST_COLS)}) VALUES ({', '.join(['%s'] * len(vals))})"
                     " RETURNING forecast_id, issued_at", vals).fetchone()
    return {"forecast_id": r[0], "issued_at": r[1].isoformat()}


def scorecard(conn, night: str | None = None) -> list[dict]:
    from psycopg.rows import dict_row

    q = "SELECT * FROM dcsim.forecast_scorecard"
    args: tuple = ()
    if night:
        q, args = q + " WHERE for_night = %s", (night,)
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(q + " ORDER BY for_night, issued_at, method", args).fetchall()
    for r in rows:
        for k, v in r.items():
            if isinstance(v, (dt.date, dt.datetime)):
                r[k] = v.isoformat()
    return rows


def latest_forecast_config(conn, night: str) -> str | None:
    r = conn.execute("SELECT config_hash FROM dcsim.forecasts WHERE for_night = %s ORDER BY issued_at DESC LIMIT 1",
                     (night,)).fetchone()
    return r[0] if r else None


def load_live_night(conn, result: dict, cfg, gt: dict, plan: str) -> dict:
    """One newly observed night (after a forecast was logged) -> observed rows under run_id LIVE_RUN."""
    s = result["summary"]
    night = s["date"]
    for t in ("nights", "pan_windows"):
        conn.execute(f"DELETE FROM dcsim.{t} WHERE run_id = %s AND night = %s", (LIVE_RUN, night))
    conn.execute("DELETE FROM dcsim.waste_log WHERE night = %s", (night,))
    conn.execute("DELETE FROM dcsim.planned_batches WHERE menu_date = %s AND plan = %s", (cfg.date, plan))
    st = {x.id: x.station for x in cfg.menu}
    copy_rows(conn, "planned_batches", PLAN_COLS, [
        {"menu_date": cfg.date, "plan": plan, "dish_id": k, "station": st.get(k),
         **b.model_dump(include={"batch_size", "starting_batches", "reorder_point", "max_batches"})}
        for k, b in cfg.kitchen.items()])
    copy_rows(conn, "nights", NIGHT_COLS, [{
        "ts": at(night, cfg.open_min), "night": night, "source": "observed", "scenario": "observed",
        "run_id": LIVE_RUN, "menu_date": cfg.date, "plan": plan, "weekday": s["weekday"], "weather": gt["weather"],
        "event_night": gt["event_night"], "adopted": s["adopted"], "split": "live",
        **{k: s[k] for k in ("students", "cooked_portions", "waste_portions", "waste_lb", "expired_lb", "close_lb",
                             "plate_lb", "stockout_min")}}])
    n_pans = copy_rows(conn, "pan_windows", PAN_COLS, ({
        "ts": at(night, hhmm_to_min(r["window_start"])), "night": night, "source": "observed", "scenario": "observed",
        "run_id": LIVE_RUN, **{k: r[k] for k in ("station", "dish_id", "dish", "portions_out", "portions_eaten",
                                                  "portions_left")}} for r in result["consumption"]))
    copy_rows(conn, "waste_log", WASTE_COLS, ({
        "ts": dt.datetime.fromisoformat(w["timestamp"]).replace(tzinfo=TZ), "night": night,
        **{k: w[k] for k in ("station", "dish_id", "dish", "portions", "weight_lb", "reason")}} for w in result["waste"]))
    conn.commit()
    refresh(conn)
    return {"night": night, "pan_windows": n_pans, **{k: s[k] for k in ("students", "waste_lb", "stockout_min")}}


# The kitchen's production ratio, read off the pans: after the opening stock, the smallest refill a
# dish gets in one 30-min window is one batch. Divided by the planned batch size, the median over
# dishes is that night's ratio. Needs only MetaFoodX-style pan data plus the plan.
PRODUCTION_RATIO = """
WITH opened AS (SELECT night, ts AS open_ts, menu_date, plan FROM dcsim.nights WHERE scenario = 'observed'),
refills AS (
  SELECT p.night, p.dish_id, min(p.portions_out) AS smallest_refill
  FROM dcsim.pan_windows p JOIN opened o USING (night)
  WHERE p.scenario = 'observed' AND p.portions_out > 0 AND p.ts > o.open_ts
  GROUP BY 1, 2)
SELECT r.night::text AS night,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY r.smallest_refill::float / b.batch_size) AS production_ratio,
       count(*) AS dishes
FROM refills r JOIN opened o USING (night)
JOIN dcsim.planned_batches b ON b.menu_date = o.menu_date AND b.plan = o.plan AND b.dish_id = r.dish_id
GROUP BY 1 ORDER BY 1
"""


def production_ratios(conn) -> dict[str, float]:
    return {n: float(r) for n, r, _ in conn.execute(PRODUCTION_RATIO).fetchall()}


# ------------------------------------------------------------------ canned queries (`dcsim tsdb query <name>`)

QUERIES: dict[str, tuple[str, str]] = {
    "status": ("Rows per table and scenario", """
        SELECT 'nights' AS tbl, scenario, count(*) AS rows, count(DISTINCT night) AS nights FROM dcsim.nights GROUP BY scenario
        UNION ALL
        SELECT 'pan_windows', scenario, count(*), count(DISTINCT night) FROM dcsim.pan_windows GROUP BY scenario
        UNION ALL
        SELECT 'waste_log', 'observed', count(*), count(DISTINCT night) FROM dcsim.waste_log
        ORDER BY 1, 2"""),
    "situations": ("Observed vs simulated waste and students by situation (held-out nights)", """
        SELECT CASE WHEN event_night THEN 'event' ELSE weather END AS situation, scenario,
               count(DISTINCT night) AS nights, count(*) AS rows,
               round(avg(students)) AS students, round(avg(waste_lb)::numeric, 1) AS waste_lb,
               round(avg(stockout_min)::numeric, 1) AS stockout_min
        FROM dcsim.nights WHERE split = 'test'
        GROUP BY 1, 2 ORDER BY 1, 2"""),
    "weekdays": ("Observed vs simulated by weekday (all nights)", """
        SELECT weekday, scenario, count(DISTINCT night) AS nights,
               round(avg(students)) AS students, round(avg(waste_lb)::numeric, 1) AS waste_lb
        FROM dcsim.nights GROUP BY 1, 2 ORDER BY min(extract(isodow FROM night)), 2"""),
    "pans": ("Average portions left in the pan by time of night, observed vs replay, over how many nights", """
        SELECT to_char(ts AT TIME ZONE 'America/New_York', 'HH24:MI') AS window, scenario,
               count(DISTINCT night) AS nights, count(DISTINCT (night, seed)) AS runs,
               round(avg(portions_left)::numeric, 1) AS avg_left,
               round(avg((portions_left = 0)::int)::numeric * 100, 1) AS pct_empty
        FROM dcsim.pan_windows WHERE scenario IN ('observed', 'replay')
        GROUP BY 1, 2 ORDER BY 1, 2"""),
    "dishes": ("Dish mix: observed vs replay share of portions eaten (held-out nights), biggest gaps first", """
        WITH d AS (
          SELECT dish_id, scenario, sum(eaten)::float / count(DISTINCT (day, seed)) AS eaten_per_night
          FROM dcsim.dish_nightly WHERE scenario IN ('observed', 'replay') AND day >= %(test_from)s
          GROUP BY 1, 2),
        s AS (SELECT dish_id, scenario, eaten_per_night / sum(eaten_per_night) OVER (PARTITION BY scenario) AS share FROM d)
        SELECT o.dish_id, round(o.share::numeric * 100, 2) AS observed_pct, round(r.share::numeric * 100, 2) AS replay_pct,
               round((r.share - o.share)::numeric * 100, 2) AS gap_pts
        FROM s o JOIN s r ON r.dish_id = o.dish_id AND r.scenario = 'replay' WHERE o.scenario = 'observed'
        ORDER BY abs(r.share - o.share) DESC LIMIT 15"""),
    "coverage": ("Held-out nights: how often the observed value fell inside the simulated 10-90% range", """
        SELECT scenario, count(*) AS nights,
               sum(waste_inside::int) AS waste_inside, round(avg(waste_lb - waste_p50)::numeric, 1) AS waste_bias_lb,
               sum(stockout_inside::int) AS stockout_inside,
               round(avg(stockout_min - stockout_p50)::numeric, 1) AS stockout_bias_min
        FROM dcsim.forecast_check WHERE split = 'test' AND scenario LIKE 'forecast%%'
        GROUP BY 1 ORDER BY 1"""),
    "nights": ("Every held-out night, observed vs each forecast", """
        SELECT night, weekday, weather, adopted, scenario, students, round(students_p50) AS sim_students,
               waste_lb, round(waste_p10::numeric) AS p10, round(waste_p50::numeric) AS p50,
               round(waste_p90::numeric) AS p90, waste_inside
        FROM dcsim.forecast_check WHERE split = 'test' AND scenario LIKE 'forecast%%' ORDER BY night, scenario"""),
    "ledger": ("Forecast ledger: every logged forecast and how it scored once the night was observed", """
        SELECT for_night, method, label, to_char(issued_at AT TIME ZONE 'America/New_York', 'MM-DD HH24:MI') AS issued,
               issued_before_open AS before_open, round(waste_p10) AS p10, round(waste_p50) AS p50, round(waste_p90) AS p90,
               observed_waste_lb AS observed, waste_result, stockout_result
        FROM dcsim.forecast_scorecard ORDER BY for_night, issued_at, method"""),
    "production": ("Production ratio per observed night (smallest refill / planned batch size)", PRODUCTION_RATIO),
    "calibrations": ("Calibration runs", """
        SELECT calibration_id, created_at, test_from, params->'crowd' AS crowd, params->'production' AS production,
               scores->'forecast_calibrated'->'waste_lb'->'coverage80' AS cal_waste_cov,
               scores->'forecast_naive'->'waste_lb'->'coverage80' AS naive_waste_cov
        FROM dcsim.calibrations ORDER BY created_at DESC LIMIT 5"""),
}


def run_query(conn, name_or_sql: str, test_from: dt.date = TEST_FROM) -> tuple[list[str], list[tuple]]:
    sql = QUERIES[name_or_sql][1] if name_or_sql in QUERIES else name_or_sql
    with conn.cursor() as cur:
        cur.execute(sql, {"test_from": test_from} if "%(" in sql else None)
        cols = [c.name for c in cur.description] if cur.description else []
        return cols, cur.fetchall() if cur.description else []


def format_table(cols: list[str], rows: list[tuple]) -> str:
    cells = [[("" if v is None else str(v)) for v in r] for r in rows]
    w = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(cols)]
    line = "  ".join(c.ljust(w[i]) for i, c in enumerate(cols))
    return "\n".join([line, "  ".join("-" * x for x in w)] + ["  ".join(v.ljust(w[i]) for i, v in enumerate(r)) for r in cells])
