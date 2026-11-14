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

    dish_nights   per dish per simulated night (replays): cooked / served / expired / leftover / stockout
    station_hours per station and hour (replays): servers planned, servers needed, peak line
    minute_series per-minute students inside and line per station (replays, one seed per night)
    category tables (periods, stations, dishes, personas, allergies, date_context, kitchen_resources,
                  cat_values) and website views (v_nights, v_waste_by_reason, v_menu)

Continuous aggregate `dish_nightly` rolls pan_windows up to one row per dish per night per run.
Two more feed the live dashboard (DASH_CAGGS, docs/TIGER.md section 8): `waste_daily_by_dish` over
waste_log and `pan_hourly` over pan_windows, each with a refresh policy and real-time aggregation.
`bench()` times the same question on the raw hypertable and on its aggregate; `dashboard()` is what
GET /api/tiger/dashboard serves.
Columns added since engine 0.4 (NEW_COLUMNS) are added by init_schema; simulated rows fill every
metric column, observed rows only what a real record has (waste cost, per-period waste, students by
period). `sql/tiger_website.sql` (dcsim.tiger_sql) is the same schema as one console script.

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

# Columns added since engine 0.4 (init_schema adds them with ADD COLUMN IF NOT EXISTS).
NEW_COLUMNS = {
    "nights": [
        ("waste_cost_usd", "double precision"),
        ("engine_version", "text"), ("config_hash", "text"),
        ("dinner_students", "int"), ("late_night_students", "int"),
        ("missed_first_choice_rate", "double precision"), ("missed_meal", "int"),
        ("wait_p50_min", "double precision"), ("wait_p90_min", "double precision"),
        ("lines_skipped", "int"), ("empty_pans_seen", "int"),          # balks (seen from a distance), empty_seen
        ("in_hall_at_2330", "int"),                                      # students inside 30 min before serving stops
        ("batch_wait_min", "double precision"), ("cook_busy_share", "double precision"),  # 0.5.0 kitchen capacity
        ("equipment_peak", "jsonb"),                                     # {"oven": 14, "cooks:dinner": 12, ...}
        ("empty_pan_wait_min", "double precision"),                      # 0.5.0 waits at an empty pan
        ("seat_wait_min", "double precision"), ("peak_seated", "int"),   # 0.5.0 seats
        ("swipe_wait_min", "double precision"), ("swipe_queue_max", "int"),  # 0.6.0 entrance
        ("group_students", "int"),                                       # 0.6.0 friends arriving together
        ("allergy_students", "int"), ("allergy_no_meal", "int"),         # 0.6.0 allergies
        ("dinner_stockout_min", "double precision"), ("late_night_stockout_min", "double precision"),
        ("dinner_waste_portions", "int"), ("late_night_waste_portions", "int"),
    ],
    "pan_windows": [("period", "text")],                                  # dinner | late_night
    "waste_log": [("period", "text")],
    "planned_batches": [
        ("period", "text"), ("cook_min", "double precision"), ("hold_min", "double precision"),
        ("last_batch_min", "int"), ("expected_demand", "double precision"),
        ("taper_from_min", "int"), ("taper_batch_size", "int"), ("taper_reorder_point", "int"),  # 0.4.2 late-night taper
        ("forecast_reorder", "boolean"),                                  # 0.6.0 preset
    ],
    # The situation a forecast assumed (calibration.situational_draws); NULL = not given, pooled crowd error.
    "forecasts": [("weather", "text"), ("event_night", "boolean")],
}
_new = lambda t: tuple(c for c, _ in NEW_COLUMNS[t])  # noqa: E731

NIGHT_COLS = (
    "ts", "night", "source", "scenario", "run_id", "draw", "seed", "menu_date", "plan", "weekday", "weather",
    "event_night", "adopted", "split", "crowd_multiplier", "over_prep", "students", "cooked_portions",
    "waste_portions", "waste_lb", "expired_lb", "close_lb", "plate_lb", "stockout_min",
) + _new("nights")
PAN_COLS = ("ts", "night", "source", "scenario", "run_id", "seed", "station", "dish_id", "dish",
            "portions_out", "portions_eaten", "portions_left") + _new("pan_windows")
WASTE_COLS = ("ts", "night", "station", "dish_id", "dish", "portions", "weight_lb", "reason") + _new("waste_log")
PLAN_FIELDS = ("batch_size", "starting_batches", "reorder_point", "max_batches", "cook_min", "hold_min",
               "last_batch_min", "expected_demand", "taper_from_min", "taper_batch_size", "taper_reorder_point",
               "forecast_reorder")
PLAN_COLS = ("menu_date", "plan", "dish_id", "station", "period") + PLAN_FIELDS
DISH_NIGHT_COLS = ("ts", "night", "source", "scenario", "run_id", "seed", "dish_id", "station", "period", "to_order",
                   "cooked", "served", "expired", "leftover", "waste", "stockout_min", "runout_min", "first_choice_demand")
STATION_HOUR_COLS = ("ts", "night", "scenario", "run_id", "seed", "station", "hour", "servers_planned",
                     "servers_needed", "peak_line")
SERIES_COLS = ("ts", "night", "scenario", "run_id", "seed", "kind", "key", "value")

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

VECTOR_DIM = 7 + 4 + 2 + 64  # weekday one-hot + weather one-hot + event one-hot + 64 menu buckets (nightsearch.DIM)

NEW_TABLES = """
-- Per dish per night per run (NightMetrics.dishes): what the cook sheet and Waste pages show.
CREATE TABLE IF NOT EXISTS dcsim.dish_nights (
  ts timestamptz NOT NULL, night date NOT NULL, source text NOT NULL, scenario text NOT NULL,
  run_id text NOT NULL, seed int NOT NULL DEFAULT 0,
  dish_id text NOT NULL, station text, period text,
  to_order boolean, cooked int, served int, expired int, leftover int, waste int,
  stockout_min double precision, runout_min double precision, first_choice_demand int
);
CREATE INDEX IF NOT EXISTS dish_nights_dish_idx ON dcsim.dish_nights (dish_id, ts);
-- Servers needed per station and hour (engine 0.5.0, ISSUES N3): servers for <= 85% busy, peak line that hour.
CREATE TABLE IF NOT EXISTS dcsim.station_hours (
  ts timestamptz NOT NULL, night date NOT NULL, scenario text NOT NULL, run_id text NOT NULL, seed int NOT NULL DEFAULT 0,
  station text NOT NULL, hour int NOT NULL, servers_planned int, servers_needed int, peak_line int
);
-- Per-minute series (engine 0.6.0, ?include=series): one row per minute and thing measured.
CREATE TABLE IF NOT EXISTS dcsim.minute_series (
  ts timestamptz NOT NULL, night date NOT NULL, scenario text NOT NULL, run_id text NOT NULL, seed int NOT NULL DEFAULT 0,
  kind text NOT NULL,              -- inside | queue | pan  (see cat_values series_kind)
  key text NOT NULL,               -- 'hall', a station id or a dish id
  value double precision NOT NULL
);
"""
# Similar nights (pgvector; nightsearch.py fills it and builds its index).
VECTOR_TABLE = f"""
CREATE TABLE IF NOT EXISTS dcsim.night_vectors (
  run_id text NOT NULL, night date NOT NULL,
  weekday text, weather text, event_night boolean, n_dishes int,
  embedding vector({VECTOR_DIM}) NOT NULL,
  PRIMARY KEY (run_id, night)
);
"""
NEW_HYPERTABLES = """
SELECT create_hypertable('dcsim.dish_nights', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
SELECT create_hypertable('dcsim.station_hours', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
SELECT create_hypertable('dcsim.minute_series', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
"""

CATEGORY_TABLES = """
-- Small fixed lists: what each code in the fact tables means.
CREATE TABLE IF NOT EXISTS dcsim.cat_values (
  category text NOT NULL,          -- source | waste_reason | weather | role | diet | vessel | method | split | series_kind | resource_kind
  code text NOT NULL,
  label text NOT NULL,
  sort int NOT NULL DEFAULT 0,
  PRIMARY KEY (category, code)
);
-- Service periods (engine 0.4.1): dinner 4:30-9 pm, late night 9 pm-12 am.
CREATE TABLE IF NOT EXISTS dcsim.periods (
  period text PRIMARY KEY, label text NOT NULL, opens time NOT NULL, closes time NOT NULL,
  expected_students int, note text
);
CREATE TABLE IF NOT EXISTS dcsim.stations (
  station text PRIMARY KEY, name text NOT NULL, servers int, self_serve boolean, to_order boolean,
  service_median_min double precision, pos_x_m double precision, pos_y_m double precision,
  equipment text,                  -- engine 0.5.0: kitchen equipment its batches use (NULL = by dish role)
  note text
);
-- One row per dish on a menu date (dinner and late night; late-night ids start with "late.").
CREATE TABLE IF NOT EXISTS dcsim.dishes (
  menu_date date NOT NULL, dish_id text NOT NULL, name text NOT NULL, station text NOT NULL,
  period text NOT NULL, role text, vessel text, hot boolean,
  diet_tags text[], allergens text[],
  popularity double precision,     -- survey-adjusted weight incl. staff and per-dish boosts (E6)
  batch_cooked boolean,            -- false = made to order (no pans)
  equipment text, prep_min double precision,   -- engine 0.5.0 kitchen capacity
  PRIMARY KEY (menu_date, dish_id)
);
CREATE TABLE IF NOT EXISTS dcsim.personas (
  persona text PRIMARY KEY, label text, diet text, share double precision,
  seconds_prob double precision, dwell_median_min double precision, description text
);
-- Engine 0.6.0: allergy shares (UMass Dining survey 2022-23) and the menu labels each one avoids.
CREATE TABLE IF NOT EXISTS dcsim.allergies (
  allergy text PRIMARY KEY, share double precision, menu_labels text[]
);
-- Engine 0.4.2: crowd multipliers by date (weekday x event; placeholders until swipe data).
CREATE TABLE IF NOT EXISTS dcsim.date_context (
  menu_date date PRIMARY KEY, weekday text, weekend boolean,
  dinner_crowd_factor double precision, late_night_crowd_factor double precision,
  events text[], expected_dinner_students int, expected_late_night_students int
);
-- Engine 0.5.0: cooks per period and equipment units (placeholders).
CREATE TABLE IF NOT EXISTS dcsim.kitchen_resources (
  kind text NOT NULL,              -- cooks | equipment
  name text NOT NULL,              -- period for cooks, equipment type otherwise
  units int NOT NULL,
  PRIMARY KEY (kind, name)
);
"""

# Views the website can read directly: codes joined to their categories.
WEB_VIEWS = """
CREATE OR REPLACE VIEW dcsim.v_nights AS
SELECT n.night, n.source, sv.label AS source_label, n.scenario, n.run_id, n.seed, n.weekday, n.weather,
       n.event_night, n.adopted, n.students, n.dinner_students, n.late_night_students,
       n.waste_portions, n.waste_lb, n.waste_cost_usd, n.expired_lb, n.close_lb, n.plate_lb,
       n.stockout_min, n.dinner_stockout_min, n.late_night_stockout_min,
       n.missed_first_choice_rate, n.missed_meal, n.wait_p50_min, n.wait_p90_min,
       n.batch_wait_min, n.cook_busy_share, n.empty_pan_wait_min, n.peak_seated, n.swipe_wait_min,
       n.group_students, n.allergy_students, n.allergy_no_meal, n.in_hall_at_2330,
       c.dinner_crowd_factor, c.late_night_crowd_factor, c.events, n.engine_version,
       n.dinner_waste_portions, n.late_night_waste_portions   -- appended: views can only grow at the end
FROM dcsim.nights n
LEFT JOIN dcsim.cat_values sv ON sv.category = 'source' AND sv.code = n.source
-- Context of the night's own date: past nights reuse a same-weekday menu, but not its events.
LEFT JOIN dcsim.date_context c ON c.menu_date = n.night;

CREATE OR REPLACE VIEW dcsim.v_waste_by_reason AS
SELECT w.night, w.period, w.station, s.name AS station_name, w.reason, r.label AS reason_label,
       sum(w.weight_lb) AS weight_lb, sum(w.portions) AS portions
FROM dcsim.waste_log w
LEFT JOIN dcsim.stations s ON s.station = w.station
LEFT JOIN dcsim.cat_values r ON r.category = 'waste_reason' AND r.code = w.reason
GROUP BY 1, 2, 3, 4, 5, 6;

CREATE OR REPLACE VIEW dcsim.v_menu AS
SELECT d.menu_date, d.period, p.label AS period_label, d.station, s.name AS station_name, d.dish_id, d.name,
       d.role, d.diet_tags, d.allergens, d.popularity, d.batch_cooked, d.equipment, d.prep_min
FROM dcsim.dishes d
JOIN dcsim.periods p ON p.period = d.period
LEFT JOIN dcsim.stations s ON s.station = d.station;
"""


def alter_columns_sql() -> str:
    lines = []
    for table, cols in NEW_COLUMNS.items():
        adds = ",\n  ".join(f"ADD COLUMN IF NOT EXISTS {c} {t}" for c, t in cols)
        lines.append(f"ALTER TABLE dcsim.{table}\n  {adds};")
    return "\n".join(lines) + "\n"


# Columnstore (compression): segment by what queries and calibrate's delete-and-reload filter on,
# so a re-run drops whole compressed segments. Chunks older than a day convert automatically.
COLUMNSTORE = {
    "nights": ("scenario", "ts, run_id, draw, seed"),
    "pan_windows": ("scenario, dish_id", "ts, seed"),
    "waste_log": ("dish_id", "ts"),
    "dish_nights": ("scenario, dish_id", "ts, seed"),
    "station_hours": ("scenario, station", "ts, seed"),
    "minute_series": ("scenario, kind, key", "ts, seed"),
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

# ------------------------------------------------------------------ dashboard aggregates (docs/TIGER.md section 8)
# A service night runs 4:30 pm to midnight Eastern and its close-out weigh-ins land just after midnight
# (and dinner crosses midnight UTC), so the "day" is a local day that starts at 6 am: every row of a
# night lands in one bucket, on both sides of the clock change. `night` is grouped too, so the
# aggregate joins straight to dcsim.nights and dcsim.forecasts.
NIGHT_BUCKET = "time_bucket('1 day', ts, 'America/New_York', TIMESTAMPTZ '2026-01-01 06:00:00-05')"
NIGHT_BUCKET_PLAIN = ("(date_trunc('day', (ts AT TIME ZONE 'America/New_York') - INTERVAL '6 hours') + INTERVAL '6 hours')"
                      " AT TIME ZONE 'America/New_York'")
# name -> source hypertable, SELECT (with {bucket}), bucket expressions, refresh policy (start, end, every)
DASH_CAGGS: dict[str, dict] = {
    "waste_daily_by_dish": {
        "source": "waste_log",
        "select": """
SELECT {bucket} AS bucket, night, station, dish_id, dish, reason, period,
       sum(portions) AS portions, sum(weight_lb) AS weight_lb, count(*) AS events
FROM dcsim.waste_log
GROUP BY 1, night, station, dish_id, dish, reason, period""",
        "bucket": NIGHT_BUCKET, "bucket_plain": NIGHT_BUCKET_PLAIN,
        "policy": ("3 days", "1 hour", "1 hour"), "marker": "night",
    },
    # Per dish per clock hour: how much went out, how much was eaten, how low the pan got.
    # Runs of a scenario (replay seeds) are pooled; `windows` is the number of 30-min windows behind a row.
    # A dish's last window is the close-out (pans are pulled, so it reads 0): max_left keeps the level
    # before it, and windows_empty counts it, so readers treat a dish's final hour separately (PANS_SQL).
    "pan_hourly": {
        "source": "pan_windows",
        "select": """
SELECT {bucket} AS bucket, night, source, scenario, station, dish_id, dish, period,
       sum(portions_out) AS portions_out, sum(portions_eaten) AS portions_eaten,
       min(portions_left) AS min_left, max(portions_left) AS max_left, sum(portions_left) AS sum_left,
       count(*) FILTER (WHERE portions_left = 0) AS windows_empty, count(*) AS windows
FROM dcsim.pan_windows
GROUP BY 1, night, source, scenario, station, dish_id, dish, period""",
        "bucket": "time_bucket('1 hour', ts, 'America/New_York')",
        "bucket_plain": "date_trunc('hour', ts, 'America/New_York')",
        "policy": ("2 days", "1 hour", "30 minutes"), "marker": "sum_left",
    },
}
# An aggregate's definition can't be altered in place. Each one names a `marker` column of its current
# shape; an older draft without it is dropped and recreated (it only holds derived rows). This is how
# the first waste_daily_by_dish, made in the Tiger console with UTC-day buckets that split every night
# in two and no `night` column, is replaced.
DROP_DRAFT_CAGG = "".join(f"""
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM timescaledb_information.continuous_aggregates
             WHERE view_schema = 'dcsim' AND view_name = '{name}')
     AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_schema = 'dcsim' AND table_name = '{name}' AND column_name = '{spec["marker"]}') THEN
    DROP MATERIALIZED VIEW dcsim.{name};
  END IF;
END $$;
""" for name, spec in DASH_CAGGS.items())


def dash_cagg_sql(name: str, plain: bool = False) -> str:
    """CREATE for one dashboard aggregate: a real-time continuous aggregate, or a plain view without TimescaleDB."""
    spec = DASH_CAGGS[name]
    if plain:
        return f"CREATE OR REPLACE VIEW dcsim.{name} AS" + spec["select"].format(bucket=spec["bucket_plain"])
    # materialized_only = false: rows newer than the last materialized bucket are read from the
    # hypertable at query time, so a weigh-in shows up before the next refresh.
    return (f"CREATE MATERIALIZED VIEW IF NOT EXISTS dcsim.{name}"
            " WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS"
            + spec["select"].format(bucket=spec["bucket"]) + "\nWITH NO DATA")


def dash_policy_sql(name: str) -> str:
    start, end, every = DASH_CAGGS[name]["policy"]
    return (f"SELECT add_continuous_aggregate_policy('dcsim.{name}', start_offset => INTERVAL '{start}',"
            f" end_offset => INTERVAL '{end}', schedule_interval => INTERVAL '{every}', if_not_exists => true)")


def dash_caggs_script() -> str:
    """The dashboard aggregates as console SQL (dcsim.tiger_sql)."""
    return DROP_DRAFT_CAGG + "".join(f"{dash_cagg_sql(n)};\n{dash_policy_sql(n)};\n" for n in DASH_CAGGS)


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

# ------------------------------------------------------------------ calibration over time (docs/TIGER.md #4)
# One scored row per night and forecast method, from both the back-test (forecast_check: naive on every
# recorded night, calibrated on the held-out weeks) and the live ledger (forecast_scorecard: the latest
# forecast issued before doors opened). dcsim.score_forecasts() upserts it; a Tiger job runs it every
# morning and the loaders call it after each write, and forecast_weekly rolls it up by week.
TRACK_TABLE = """
CREATE TABLE IF NOT EXISTS dcsim.forecast_results (
  night date NOT NULL,
  origin text NOT NULL CHECK (origin IN ('backtest', 'ledger')),
  method text NOT NULL,             -- naive | calibrated
  forecast_id text, issued_at timestamptz, calibration_id text,   -- ledger rows only
  weekday text, weather text, event_night boolean, adopted boolean, split text,
  students int, students_p50 double precision,
  waste_lb double precision, waste_p10 double precision, waste_p50 double precision, waste_p90 double precision,
  waste_inside boolean,
  stockout_min double precision, stockout_p10 double precision, stockout_p50 double precision,
  stockout_p90 double precision, stockout_inside boolean,
  scored_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (night, origin, method)
);
"""
TRACK_HYPERTABLE = """
SELECT create_hypertable('dcsim.forecast_results', by_range('night', INTERVAL '28 days'), if_not_exists => TRUE);
"""
# After the views it reads (a SQL procedure's body is checked when it is created).
SCORE_PROC = """
CREATE OR REPLACE PROCEDURE dcsim.score_forecasts(job_id int DEFAULT NULL, config jsonb DEFAULT NULL)
LANGUAGE sql AS $$
-- a re-calibration replaces the back-test, so its old rows go first; ledger rows are only ever upserted
DELETE FROM dcsim.forecast_results WHERE origin = 'backtest';
INSERT INTO dcsim.forecast_results (night, origin, method, forecast_id, issued_at, calibration_id,
    weekday, weather, event_night, adopted, split, students, students_p50,
    waste_lb, waste_p10, waste_p50, waste_p90, waste_inside,
    stockout_min, stockout_p10, stockout_p50, stockout_p90, stockout_inside, scored_at)
SELECT c.night, 'backtest', replace(c.scenario, 'forecast_', ''), NULL, NULL, NULL,
       c.weekday, c.weather, c.event_night, c.adopted, c.split, c.students, c.students_p50,
       c.waste_lb, c.waste_p10, c.waste_p50, c.waste_p90, c.waste_inside,
       c.stockout_min, c.stockout_p10, c.stockout_p50, c.stockout_p90, c.stockout_inside, now()
FROM dcsim.forecast_check c
WHERE c.scenario IN ('forecast_naive', 'forecast_calibrated')
UNION ALL
SELECT * FROM (
  SELECT DISTINCT ON (s.for_night, s.method)
         s.for_night, 'ledger', s.method, s.forecast_id, s.issued_at, s.calibration_id,
         o.weekday, o.weather, o.event_night, o.adopted, o.split, s.observed_students, s.students_p50,
         s.observed_waste_lb, s.waste_p10, s.waste_p50, s.waste_p90, s.waste_result = 'inside',
         s.observed_stockout_min, s.stockout_p10, s.stockout_p50, s.stockout_p90, s.stockout_result = 'inside', now()
  FROM dcsim.forecast_scorecard s
  JOIN dcsim.nights o ON o.night = s.for_night AND o.source = 'observed'
  WHERE s.waste_result <> 'awaiting' AND s.issued_before_open   -- only forecasts made before the night count
  ORDER BY s.for_night, s.method, s.issued_at DESC
) latest
ON CONFLICT (night, origin, method) DO UPDATE SET
  forecast_id = EXCLUDED.forecast_id, issued_at = EXCLUDED.issued_at, calibration_id = EXCLUDED.calibration_id,
  weekday = EXCLUDED.weekday, weather = EXCLUDED.weather, event_night = EXCLUDED.event_night,
  adopted = EXCLUDED.adopted, split = EXCLUDED.split, students = EXCLUDED.students,
  students_p50 = EXCLUDED.students_p50, waste_lb = EXCLUDED.waste_lb, waste_p10 = EXCLUDED.waste_p10,
  waste_p50 = EXCLUDED.waste_p50, waste_p90 = EXCLUDED.waste_p90, waste_inside = EXCLUDED.waste_inside,
  stockout_min = EXCLUDED.stockout_min, stockout_p10 = EXCLUDED.stockout_p10, stockout_p50 = EXCLUDED.stockout_p50,
  stockout_p90 = EXCLUDED.stockout_p90, stockout_inside = EXCLUDED.stockout_inside, scored_at = EXCLUDED.scored_at;
$$;
"""
# Weeks start on the first recorded night (Thu 2026-10-01), so weeks 5 and 6 are exactly the held-out split.
TRACK_WEEK_ORIGIN = "2026-10-01"
TRACK_SELECT = """
SELECT {bucket} AS week, origin, method,
       count(*) AS nights,
       count(*) FILTER (WHERE waste_inside) AS waste_inside,
       avg(waste_lb - waste_p50) AS waste_bias_lb,
       avg(abs(waste_lb - waste_p50)) AS waste_mae_lb,
       count(*) FILTER (WHERE stockout_inside) AS stockout_inside,
       avg(stockout_min - stockout_p50) AS stockout_bias_min,
       avg(students - students_p50) AS students_bias
FROM dcsim.forecast_results
GROUP BY 1, 2, 3"""
TRACK_CAGG = f"""
CREATE MATERIALIZED VIEW IF NOT EXISTS dcsim.forecast_weekly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
{TRACK_SELECT.format(bucket=f"time_bucket(INTERVAL '7 days', night, DATE '{TRACK_WEEK_ORIGIN}')").strip()}
WITH NO DATA;
SELECT add_continuous_aggregate_policy('dcsim.forecast_weekly', start_offset => NULL, end_offset => NULL,
       schedule_interval => INTERVAL '1 hour', if_not_exists => true);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM timescaledb_information.jobs
                 WHERE proc_schema = 'dcsim' AND proc_name = 'score_forecasts') THEN
    -- every morning at 6 am Eastern, after the night's close-out weigh-ins
    PERFORM add_job('dcsim.score_forecasts', INTERVAL '1 day',
                    initial_start => (date_trunc('day', now() AT TIME ZONE 'America/New_York')
                                      + INTERVAL '1 day 6 hours') AT TIME ZONE 'America/New_York');
  END IF;
END $$;
"""
TRACK_PLAIN_VIEW = ("CREATE OR REPLACE VIEW dcsim.forecast_weekly AS" + TRACK_SELECT.format(
    bucket=f"DATE '{TRACK_WEEK_ORIGIN}' + 7 * ((night - DATE '{TRACK_WEEK_ORIGIN}') / 7)") + ";\n")


def score_forecasts(conn) -> bool:
    """Score every observed night into dcsim.forecast_results now (the nightly Tiger job does the same).
    False when the schema predates it (run `dcsim tsdb init`)."""
    if conn.execute("SELECT to_regprocedure('dcsim.score_forecasts(integer, jsonb)')").fetchone()[0] is None:
        return False
    conn.execute("CALL dcsim.score_forecasts()")
    return True


# ------------------------------------------------------------------ comments (what `db_schema` shows the Tiger MCP)
# A model answering live questions through the Tiger MCP reads these with the schema: which rows are
# observed vs simulated, what each scenario is, and how "ran out" is measured.
COMMENTS = """
COMMENT ON SCHEMA dcsim IS 'DC What-If: one dining hall (Worcester, UMass Amherst). Every recorded ("observed") night and every simulated night side by side. Observed data is SYNTHETIC until real MetaFoodX/LeanPath exports arrive.';
COMMENT ON TABLE dcsim.nights IS 'One row per night (observed) or per simulated night (one scenario x draw x seed). Filter source/scenario before averaging. A service night runs 4:30 pm to midnight Eastern.';
COMMENT ON COLUMN dcsim.nights.source IS 'observed = the recorded night (one row per night); simulated = an engine run';
COMMENT ON COLUMN dcsim.nights.scenario IS 'observed | forecast_naive (plan as written, expected crowd) | forecast_calibrated (crowd and production drawn from the fitted errors; held-out nights only) | replay (after the night: actual crowd and production)';
COMMENT ON COLUMN dcsim.nights.run_id IS 'observed = recorded history, observed:live = a night observed after its forecast was logged; simulated: <scenario>:<night>';
COMMENT ON COLUMN dcsim.nights.split IS 'train = nights the calibration was fitted on (before 2026-10-29), test = held-out nights, live = observed after a logged forecast';
COMMENT ON COLUMN dcsim.nights.weather IS 'clear | rain | cold | snow, as known on the day';
COMMENT ON COLUMN dcsim.nights.event_night IS 'true on nights with a campus event (bigger crowd)';
COMMENT ON COLUMN dcsim.nights.adopted IS 'true when the kitchen followed the recommended plan that night instead of the baseline';
COMMENT ON COLUMN dcsim.nights.crowd_multiplier IS 'simulated rows: crowd used, relative to the engine''s expected students';
COMMENT ON COLUMN dcsim.nights.over_prep IS 'simulated rows: production ratio used (batches cooked / batch size planned)';
COMMENT ON COLUMN dcsim.nights.waste_lb IS 'food thrown away that night in pounds (expired in the pan + left at close + plate waste)';
COMMENT ON COLUMN dcsim.nights.stockout_min IS 'minutes summed over dishes that a dish was out (empty pan) while serving';
COMMENT ON TABLE dcsim.pan_windows IS 'Pan levels per dish per 30-minute window (MetaFoodX shape). A dish ran out in a window when portions_left = 0, except its last window of the night: that is the close-out, when pans are pulled.';
COMMENT ON TABLE dcsim.waste_log IS 'Observed weigh-ins (LeanPath shape), one row per bin weighed. reason: expired (held too long) | close (left at close) | plate (left on plates). Labels in cat_values (category waste_reason).';
COMMENT ON TABLE dcsim.dish_nights IS 'Per dish per simulated replay night: cooked, served, waste, minutes out of stock (stockout_min) and when it first ran out (runout_min, minutes after open).';
COMMENT ON TABLE dcsim.planned_batches IS 'The production plan per menu date and plan (baseline | recommended | cfg:<hash>): batch size, starting batches, reorder point per dish.';
COMMENT ON TABLE dcsim.forecasts IS 'Append-only forecast ledger: every forecast logged before its night (issued_at is the database clock). Updates and deletes are blocked by trigger. Scored in forecast_scorecard.';
COMMENT ON COLUMN dcsim.forecasts.weather IS 'situation the forecast assumed (NULL = not given: pooled crowd error)';
COMMENT ON TABLE dcsim.calibrations IS 'One row per calibration run: fitted crowd error (params.crowd, incl. params.crowd.situational by weather/event), production ratio, and held-out scores (scores).';
COMMENT ON TABLE dcsim.forecast_results IS 'Track record: one scored row per observed night and method (naive | calibrated). origin backtest = held-out back-test, ledger = latest live forecast issued before doors opened. Filled by dcsim.score_forecasts() (nightly Tiger job).';
COMMENT ON COLUMN dcsim.forecast_results.waste_inside IS 'observed waste fell inside the forecast 10-90% range (target: about 80% of nights)';
COMMENT ON VIEW dcsim.forecast_check IS 'Per observed night and simulated scenario: the 10/50/90% range of waste and stockout and whether the observation fell inside.';
COMMENT ON VIEW dcsim.forecast_scorecard IS 'Every logged forecast next to what happened: waste_result inside | outside | awaiting (night not observed yet).';
COMMENT ON TABLE dcsim.dishes IS 'Menu: one row per dish per menu date. Past nights reuse a same-weekday menu (nights.menu_date).';
COMMENT ON TABLE dcsim.date_context IS 'Per date: weekday, crowd factors and named campus events.';
COMMENT ON TABLE dcsim.cat_values IS 'Code -> label lookup by category (source, waste_reason, weather, role, diet, ...).';
"""
COMMENTS_TIMESCALE = """
COMMENT ON VIEW dcsim.forecast_weekly IS 'Continuous aggregate (real-time) of forecast_results by week (weeks start Thursday 2026-10-01; weeks of 2026-10-29 and 2026-11-05 are the held-out split): nights, nights inside the 10-90% range, bias (observed minus median).';
COMMENT ON VIEW dcsim.waste_daily_by_dish IS 'Continuous aggregate (real-time) of waste_log per night, dish and reason.';
COMMENT ON VIEW dcsim.pan_hourly IS 'Continuous aggregate (real-time) of pan_windows per clock hour, dish and scenario. A dish''s final hour includes the close-out (0 left).';
COMMENT ON VIEW dcsim.dish_nightly IS 'Continuous aggregate of pan_windows per dish per day and run (materialized only: refreshed by `dcsim tsdb refresh` after each load).';
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
    conn.execute(alter_columns_sql())
    conn.execute(NEW_TABLES)
    conn.execute(CATEGORY_TABLES)
    conn.execute(TRACK_TABLE)
    try:  # similar nights need pgvector
        conn.execute("SAVEPOINT vec")
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(VECTOR_TABLE)
        conn.execute("RELEASE SAVEPOINT vec")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT vec")
    if ts:
        conn.execute(HYPERTABLES)
        conn.execute(NEW_HYPERTABLES)
        conn.execute(TRACK_HYPERTABLE)
        conn.execute(CAGG)
        conn.execute(DROP_DRAFT_CAGG)
        for name in DASH_CAGGS:
            conn.execute(dash_cagg_sql(name))
            conn.execute(dash_policy_sql(name))
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
        for name in DASH_CAGGS:
            conn.execute(dash_cagg_sql(name, plain=True))
    conn.execute(FORECAST_CHECK)
    conn.execute(SCORECARD)
    conn.execute(WEB_VIEWS)
    conn.execute(SCORE_PROC)
    conn.execute(TRACK_CAGG if ts else TRACK_PLAIN_VIEW)
    conn.execute(COMMENTS)
    if ts:
        conn.execute(COMMENTS_TIMESCALE)
    load_categories(conn)
    score_forecasts(conn)
    conn.commit()
    return ts


def load_categories(conn) -> None:
    """Upsert the category tables from the current data files (dcsim.tiger_sql.category_rows)."""
    from .tiger_sql import category_rows

    conn.execute(category_rows())


def configured() -> bool:
    """True when a Tiger connection is set up (the API only logs forecasts then)."""
    from .ai.llm import load_env_file

    load_env_file()
    return bool(os.environ.get("DCSIM_TSDB_URL") or os.environ.get("TIGER_SERVICE_ID"))


def refresh(conn) -> None:
    """Bring every continuous aggregate up to date over its whole history (needs a non-transaction
    connection). The refresh policies only look back a few days; a reload of older nights needs this."""
    if not has_timescale(conn):
        return
    have = {r[0] for r in conn.execute(
        "SELECT view_name FROM timescaledb_information.continuous_aggregates WHERE view_schema = 'dcsim'")}
    was = conn.autocommit
    if not was:
        conn.commit()
        conn.autocommit = True
    try:
        for name in ("dish_nightly", *DASH_CAGGS, "forecast_weekly"):
            if name in have:
                conn.execute(f"CALL refresh_continuous_aggregate('dcsim.{name}', NULL, NULL)")
    finally:
        conn.autocommit = was


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
    from psycopg.types.json import Jsonb

    n = 0
    with conn.cursor() as cur, cur.copy(f"COPY dcsim.{table} ({', '.join(cols)}) FROM STDIN") as cp:
        for r in rows:
            vals = [r.get(c, 0 if c in ("draw", "seed") else None) for c in cols]  # COPY skips DEFAULTs
            cp.write_row([Jsonb(v) if isinstance(v, dict) else v for v in vals])
            n += 1
    return n


def delete_scenario(conn, scenario: str) -> None:
    for t in ("nights", "pan_windows", "dish_nights", "station_hours", "minute_series"):
        conn.execute(f"DELETE FROM dcsim.{t} WHERE scenario = %s", (scenario,))


def period_of(dish_id: str | None) -> str:
    """Service period of a dish: late-night dish ids start with "late." (engine 0.4.1)."""
    return "late_night" if dish_id and dish_id.startswith("late.") else "dinner"


def plan_rows(menu_date: str, plan: str, cfg) -> list[dict]:
    """planned_batches rows: every BatchPlan field the engine has, plus the dish's station and period."""
    dish = {x.id: x for x in cfg.menu}
    return [{"menu_date": menu_date, "plan": plan, "dish_id": k, "station": dish[k].station if k in dish else None,
             "period": dish[k].period if k in dish else period_of(k), **b.model_dump(include=set(PLAN_FIELDS))}
            for k, b in cfg.kitchen.items()]


def metric_columns(cfg, m, config_hash: str | None = None, cost: dict | None = None) -> dict:
    """The engine 0.4+ night columns from one simulated night's NightMetrics (`cost`: dish -> $ per
    portion, synthetic.dish_cost; pass it when calling per seed)."""
    from .config import ENGINE_VERSION
    from .config import config_hash as hash_of
    from .synthetic import dish_cost

    cost = cost if cost is not None else dish_cost(cfg)

    per = {d.id: d.period for d in cfg.menu}
    so = {"dinner": 0.0, "late_night": 0.0}
    waste = {"dinner": 0, "late_night": 0}
    for k, d in m.dishes.items():
        p = per.get(k, period_of(k))
        so[p] += d.stockout_min
        if not d.to_order:
            waste[p] += d.waste
    late = m.late_night_students
    return {
        "engine_version": ENGINE_VERSION, "config_hash": config_hash or hash_of(cfg),
        "waste_cost_usd": round(sum(d.waste * cost.get(k, 0.0) for k, d in m.dishes.items() if not d.to_order), 2),
        "dinner_students": m.students - late, "late_night_students": late,
        "missed_first_choice_rate": m.missed_first_choice_rate, "missed_meal": m.missed_meal,
        "wait_p50_min": m.wait_p50, "wait_p90_min": m.wait_p90,
        "lines_skipped": m.balks, "empty_pans_seen": m.empty_seen, "in_hall_at_2330": m.in_hall_before_close,
        "batch_wait_min": m.batch_wait_min, "cook_busy_share": m.cook_busy_share,
        "equipment_peak": dict(m.equipment_peak) or None, "empty_pan_wait_min": m.empty_wait_min,
        "seat_wait_min": m.seat_wait_min, "peak_seated": m.peak_seated,
        "swipe_wait_min": m.swipe_wait_min, "swipe_queue_max": m.swipe_queue_max,
        "group_students": m.group_students, "allergy_students": m.allergy_students,
        "allergy_no_meal": m.allergy_no_meal,
        "dinner_stockout_min": round(so["dinner"], 2), "late_night_stockout_min": round(so["late_night"], 2),
        "dinner_waste_portions": waste["dinner"], "late_night_waste_portions": waste["late_night"],
    }


def observed_extras(summary: dict, waste_rows: list[dict]) -> dict:
    """The engine 0.4+ night columns a real record has: waste cost, students by period (swipe
    times), and pre-consumer waste by period (from the weigh-ins)."""
    out = {"waste_cost_usd": float(summary["waste_cost_usd"]) if summary.get("waste_cost_usd") not in (None, "") else None}
    if summary.get("late_night_students") not in (None, ""):
        late = int(float(summary["late_night_students"]))
        out |= {"late_night_students": late, "dinner_students": int(float(summary["students"])) - late}
    waste = {"dinner": 0, "late_night": 0}
    for w in waste_rows:
        if w["reason"] in ("expired", "close") and w.get("portions"):
            waste[w["period"]] += int(w["portions"])
    return out | {"dinner_waste_portions": waste["dinner"], "late_night_waste_portions": waste["late_night"]}


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

    waste = [{
        "ts": dt.datetime.fromisoformat(r["timestamp"]).replace(tzinfo=TZ), "night": night_of(r["timestamp"]),
        "station": r["station"], "dish_id": r["dish_id"], "dish": r["dish"],
        "portions": int(r["portions"]) if r["portions"] else None, "weight_lb": float(r["weight_lb"]),
        "reason": r["reason"], "period": period_of(r["dish_id"]),
    } for r in csv.DictReader((d / "waste_log.csv").open())]
    waste_by_night: dict = {}
    for w in waste:
        waste_by_night.setdefault(w["night"], []).append(w)

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
            **observed_extras(r, waste_by_night.get(r["date"], [])),
        })
    pans = ({
        "ts": at(r["date"], hhmm_to_min(r["window_start"])), "night": r["date"], "source": "observed",
        "scenario": "observed", "run_id": "observed", "station": r["station"], "dish_id": r["dish_id"],
        "dish": r["dish"], "portions_out": int(r["portions_out"]), "portions_eaten": int(r["portions_eaten"]),
        "portions_left": int(r["portions_left"]), "period": period_of(r["dish_id"]),
    } for r in csv.DictReader((d / "consumption.csv").open()))

    from .calibration import executed_plan

    plans = []
    for menu_date, adopted in sorted({(n["menu_date"], n["adopted"]) for n in nights}):
        plans += plan_rows(menu_date, "recommended" if adopted else "baseline", executed_plan(menu_date, adopted))

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
    score_forecasts(conn)
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
                 "samples") + _new("forecasts")


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
    copy_rows(conn, "planned_batches", PLAN_COLS, plan_rows(cfg.date, plan, cfg))
    waste = [{"ts": dt.datetime.fromisoformat(w["timestamp"]).replace(tzinfo=TZ), "night": night,
              "period": period_of(w["dish_id"]),
              **{k: w[k] for k in ("station", "dish_id", "dish", "portions", "weight_lb", "reason")}} for w in result["waste"]]
    copy_rows(conn, "nights", NIGHT_COLS, [{
        "ts": at(night, cfg.open_min), "night": night, "source": "observed", "scenario": "observed",
        "run_id": LIVE_RUN, "menu_date": cfg.date, "plan": plan, "weekday": s["weekday"], "weather": gt["weather"],
        "event_night": gt["event_night"], "adopted": s["adopted"], "split": "live",
        **{k: s[k] for k in ("students", "cooked_portions", "waste_portions", "waste_lb", "expired_lb", "close_lb",
                             "plate_lb", "stockout_min")}, **observed_extras(s, waste)}])
    n_pans = copy_rows(conn, "pan_windows", PAN_COLS, ({
        "ts": at(night, hhmm_to_min(r["window_start"])), "night": night, "source": "observed", "scenario": "observed",
        "run_id": LIVE_RUN, "period": period_of(r["dish_id"]),
        **{k: r[k] for k in ("station", "dish_id", "dish", "portions_out", "portions_eaten",
                             "portions_left")}} for r in result["consumption"]))
    copy_rows(conn, "waste_log", WASTE_COLS, waste)
    score_forecasts(conn)  # the night's ledger forecasts join the track record (forecast_weekly) right away
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
    "track": ("Track record by week: nights inside the 10-90% range and bias (continuous aggregate forecast_weekly)", """
        SELECT week, origin, method, nights, waste_inside, round(waste_bias_lb::numeric, 1) AS waste_bias_lb,
               stockout_inside, round(stockout_bias_min::numeric, 1) AS stockout_bias_min,
               round(students_bias::numeric) AS students_bias
        FROM dcsim.forecast_weekly ORDER BY origin, method, week"""),
    "situational": ("Crowd error by weather and event, as each calibration fitted it", """
        SELECT calibration_id, created_at,
               params->'crowd'->'situational'->'effects' AS effects,
               params->'crowd'->'situational'->'held_out'->'pooled'->'crps' AS pooled_crps,
               params->'crowd'->'situational'->'held_out'->'situational'->'crps' AS situational_crps,
               params->'crowd'->'situational'->'kept' AS kept
        FROM dcsim.calibrations ORDER BY created_at DESC LIMIT 5"""),
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


# ------------------------------------------------------------------ the unified stack, the aggregates, the dashboard
# (docs/TIGER.md section 8)

# One statement across the whole stack: two continuous aggregates (over the weigh-in and pan
# hypertables), the nights hypertable, the relational plan, the simulated forecast ranges and the ledger.
STACK_SQL = """
WITH waste AS (      -- continuous aggregate over the waste_log hypertable (LeanPath-style weigh-ins)
  SELECT night, sum(weight_lb) FILTER (WHERE reason <> 'plate') AS weighed_lb, sum(events) AS weigh_ins
  FROM dcsim.waste_daily_by_dish GROUP BY night),
pans AS (            -- continuous aggregate over the pan_windows hypertable (MetaFoodX-style pan levels)
  SELECT night, sum(portions_out) AS portions_out, sum(portions_eaten) AS portions_eaten
  FROM dcsim.pan_hourly WHERE scenario = 'observed' GROUP BY night),
plan AS (            -- relational: the production plan the night was meant to follow
  SELECT menu_date, plan, count(*) AS dishes, sum(batch_size * starting_batches) AS opening_portions
  FROM dcsim.planned_batches GROUP BY menu_date, plan),
ledger AS (          -- relational, append-only: forecasts written down before the night
  SELECT for_night, count(*) AS forecasts FROM dcsim.forecasts GROUP BY for_night)
SELECT n.night, n.weekday, n.weather, n.plan, n.students,
       round(w.weighed_lb::numeric, 1) AS weighed_lb, w.weigh_ins::int AS weigh_ins,
       p.portions_out::int AS portions_out, p.portions_eaten::int AS portions_eaten,
       pl.dishes::int AS planned_dishes, pl.opening_portions::int AS opening_portions,
       round(f.waste_p10::numeric) AS sim_p10, round(f.waste_p50::numeric) AS sim_p50,
       round(f.waste_p90::numeric) AS sim_p90, f.waste_inside AS inside_range,
       coalesce(l.forecasts, 0)::int AS ledger_forecasts
FROM dcsim.nights n                                          -- hypertable: one row per recorded night
LEFT JOIN waste w USING (night)
LEFT JOIN pans p USING (night)
LEFT JOIN plan pl ON pl.menu_date = n.menu_date AND pl.plan = n.plan
LEFT JOIN dcsim.forecast_check f ON f.night = n.night AND f.scenario = 'forecast_calibrated'   -- simulated runs
LEFT JOIN ledger l ON l.for_night = n.night
WHERE n.scenario = 'observed'
ORDER BY n.night DESC
"""

HYPERTABLES_SQL = """
SELECT h.hypertable_name AS name, h.num_chunks::int AS chunks,
       (SELECT count(*) FROM timescaledb_information.chunks c
         WHERE c.hypertable_schema = h.hypertable_schema AND c.hypertable_name = h.hypertable_name
           AND c.is_compressed)::int AS columnstore_chunks,
       hypertable_size(format('%I.%I', h.hypertable_schema, h.hypertable_name)::regclass) AS bytes,
       coalesce(s.before_compression_total_bytes, 0) AS rowstore_bytes,
       coalesce(s.after_compression_total_bytes, 0) AS columnstore_bytes
FROM timescaledb_information.hypertables h
LEFT JOIN LATERAL hypertable_columnstore_stats(format('%I.%I', h.hypertable_schema, h.hypertable_name)::regclass) s ON true
WHERE h.hypertable_schema = 'dcsim'
ORDER BY bytes DESC
"""

AGGREGATES_SQL = """
SELECT a.view_name AS name, a.hypertable_name AS source, NOT a.materialized_only AS real_time,
       j.schedule_interval::text AS refresh_every, j.config->>'start_offset' AS looks_back,
       js.last_successful_finish AS last_refresh, js.next_start AS next_refresh
FROM timescaledb_information.continuous_aggregates a
LEFT JOIN timescaledb_information.jobs j
       ON j.proc_name = 'policy_refresh_continuous_aggregate'
      AND (j.hypertable_schema, j.hypertable_name) IN ((a.view_schema, a.view_name),
            (a.materialization_hypertable_schema, a.materialization_hypertable_name))
LEFT JOIN timescaledb_information.job_stats js ON js.job_id = j.job_id
WHERE a.view_schema = 'dcsim'
ORDER BY a.view_name
"""

WASTE_BY_NIGHT_SQL = """
SELECT night::text AS night,
       round(coalesce(sum(weight_lb) FILTER (WHERE reason = 'expired'), 0)::numeric, 1)::float AS expired_lb,
       round(coalesce(sum(weight_lb) FILTER (WHERE reason = 'close'), 0)::numeric, 1)::float AS close_lb,
       round(coalesce(sum(weight_lb) FILTER (WHERE reason = 'plate'), 0)::numeric, 1)::float AS plate_lb,
       sum(events)::int AS weigh_ins
FROM dcsim.waste_daily_by_dish GROUP BY night ORDER BY night
"""

TOP_WASTE_SQL = """
SELECT dish, station, round(sum(weight_lb)::numeric, 1)::float AS waste_lb, sum(portions)::int AS portions,
       sum(events)::int AS weigh_ins, count(DISTINCT night)::int AS nights
FROM dcsim.waste_daily_by_dish WHERE reason <> 'plate'
GROUP BY dish, station ORDER BY sum(weight_lb) DESC LIMIT 10
"""

# One night's pans by clock hour. `closing` marks a dish's final hour, whose last window is the
# close-out: there the level is max_left (before the pans were pulled) and one empty window is expected.
PANS_SQL = """
SELECT dish, station, period, extract(hour FROM bucket AT TIME ZONE 'America/New_York')::int AS hour,
       portions_out::int AS portions_out, portions_eaten::int AS portions_eaten, min_left::int AS min_left,
       max_left::int AS max_left, windows_empty::int AS windows_empty, windows::int AS windows,
       bucket = max(bucket) OVER (PARTITION BY dish_id) AS closing
FROM dcsim.pan_hourly
WHERE scenario = 'observed' AND night = %(night)s
ORDER BY dish, bucket
"""

QUERIES.update({
    "stack": ("One query across the stack: aggregates over two hypertables + nights + plan + forecasts + ledger",
              STACK_SQL + "LIMIT 20"),
    "hypertables": ("Hypertables: chunks, how many are in the columnstore, size on disk, row data vs columnstore",
                    HYPERTABLES_SQL),
    "aggregates": ("Continuous aggregates: source hypertable, real-time, refresh policy, last and next refresh",
                   AGGREGATES_SQL),
    "waste-daily": ("Waste by night and reason, from the waste_daily_by_dish continuous aggregate", WASTE_BY_NIGHT_SQL),
    "waste-dishes": ("Most wasted dishes (pre-consumer), from the waste_daily_by_dish continuous aggregate", TOP_WASTE_SQL),
    "pans-hourly": ("Pans by hour of night: portions eaten and average portions on hand, from pan_hourly", """
        SELECT extract(hour FROM bucket AT TIME ZONE 'America/New_York')::int AS hour, scenario,
               sum(portions_eaten)::int AS eaten, round(sum(sum_left) / sum(windows), 1) AS avg_on_hand
        FROM dcsim.pan_hourly GROUP BY 1, 2 ORDER BY 1, 2"""),
})

# The same question asked of the raw hypertable and of its continuous aggregate (`dcsim tsdb bench`).
# The four are picked to show where each feature earns its keep, including where the aggregate doesn't:
# `why` says what to expect, the measured numbers say what happened.
BENCH: list[dict] = [
    {"id": "pans_by_hour", "title": "Portions eaten and average portions on hand by hour, every night and scenario",
     "raw_table": "pan_windows", "aggregate": "pan_hourly",
     "why": "The raw query works out a local-time hour for every row; the aggregate keeps one row per dish and "
            "hour, about nine times fewer to read.",
     "raw": """SELECT extract(hour FROM ts AT TIME ZONE 'America/New_York')::int AS hour, scenario,
       sum(portions_eaten)::int AS eaten, round(avg(portions_left), 1) AS avg_on_hand
FROM dcsim.pan_windows GROUP BY 1, 2 ORDER BY 1, 2""",
     "agg": """SELECT extract(hour FROM bucket AT TIME ZONE 'America/New_York')::int AS hour, scenario,
       sum(portions_eaten)::int AS eaten, round(sum(sum_left) / sum(windows), 1) AS avg_on_hand
FROM dcsim.pan_hourly GROUP BY 1, 2 ORDER BY 1, 2"""},
    {"id": "pans_one_night", "title": "One night's pans by dish and hour (the dashboard's pan chart)",
     "raw_table": "pan_windows", "aggregate": "pan_hourly",
     "why": "The dashboard's own read: one night out of the whole table.",
     "raw": """SELECT dish_id, extract(hour FROM ts AT TIME ZONE 'America/New_York')::int AS hour,
       sum(portions_out)::int AS put_out, sum(portions_eaten)::int AS eaten, min(portions_left) AS lowest
FROM dcsim.pan_windows WHERE scenario = 'observed' AND night = '2026-11-07' GROUP BY 1, 2 ORDER BY 1, 2""",
     "agg": """SELECT dish_id, extract(hour FROM bucket AT TIME ZONE 'America/New_York')::int AS hour,
       sum(portions_out)::int AS put_out, sum(portions_eaten)::int AS eaten, min(min_left) AS lowest
FROM dcsim.pan_hourly WHERE scenario = 'observed' AND night = '2026-11-07' GROUP BY 1, 2 ORDER BY 1, 2"""},
    {"id": "dish_totals", "title": "Portions put out and eaten per dish, replays of every night",
     "raw_table": "pan_windows", "aggregate": "pan_hourly",
     "why": "Grouped by the columns the columnstore is segmented by (scenario, dish): fully converted chunks are "
            "summed batch by batch, so the raw table needs no aggregate here. After a reload leaves chunks in the "
            "row store, the aggregate is the faster one until they are converted again (dcsim tsdb compress).",
     "raw": """SELECT dish_id, sum(portions_out)::bigint AS put_out, sum(portions_eaten)::bigint AS eaten,
       min(portions_left) AS lowest
FROM dcsim.pan_windows WHERE scenario = 'replay' GROUP BY 1 ORDER BY 1""",
     "agg": """SELECT dish_id, sum(portions_out)::bigint AS put_out, sum(portions_eaten)::bigint AS eaten,
       min(min_left) AS lowest
FROM dcsim.pan_hourly WHERE scenario = 'replay' GROUP BY 1 ORDER BY 1"""},
    {"id": "waste_by_night", "title": "Waste by night and reason (the dashboard's bars)",
     "raw_table": "waste_log", "aggregate": "waste_daily_by_dish",
     "why": "A small table: about 250 weigh-ins a night, and the aggregate keeps one row per dish, night and "
            "reason, so it only halves the rows.",
     "raw": """SELECT night, reason, round(sum(weight_lb)::numeric, 1) AS lb, count(*) AS weigh_ins
FROM dcsim.waste_log GROUP BY 1, 2 ORDER BY 1, 2""",
     "agg": """SELECT night, reason, round(sum(weight_lb)::numeric, 1) AS lb, sum(events)::bigint AS weigh_ins
FROM dcsim.waste_daily_by_dish GROUP BY 1, 2 ORDER BY 1, 2"""},
]


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    return s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2


def _timed(conn, sql: str, repeat: int) -> dict:
    """Server-side time of one statement from EXPLAIN ANALYZE (no network): median of `repeat` runs after a warm-up."""
    plan, run = [], []
    for i in range(repeat + 1):
        doc = conn.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql).fetchone()[0]
        doc = (json.loads(doc) if isinstance(doc, str) else doc)[0]
        if i:  # the first run warms the cache
            plan.append(doc["Planning Time"])
            run.append(doc["Execution Time"])
    return {"plan_ms": round(_median(plan), 2), "exec_ms": round(_median(run), 2),
            "total_ms": round(_median([p + r for p, r in zip(plan, run)]), 2)}


def bench(conn, repeat: int = 5) -> list[dict]:
    """Each BENCH question on the raw hypertable and on its continuous aggregate: rows behind each,
    server-side times, and whether the two answers are identical."""
    out = []
    for b in BENCH:
        raw_rows = conn.execute(f"SELECT count(*) FROM dcsim.{b['raw_table']}").fetchone()[0]
        agg_rows = conn.execute(f"SELECT count(*) FROM dcsim.{b['aggregate']}").fetchone()[0]
        same = conn.execute(b["raw"]).fetchall() == conn.execute(b["agg"]).fetchall()
        raw, agg = _timed(conn, b["raw"], repeat), _timed(conn, b["agg"], repeat)
        out.append({"id": b["id"], "title": b["title"], "why": b["why"], "raw_table": b["raw_table"],
                    "aggregate": b["aggregate"],
                    "raw_rows": raw_rows, "aggregate_rows": agg_rows, "same_answer": same, "raw": raw, "agg": agg,
                    "speedup": round(raw["total_ms"] / agg["total_ms"], 1) if agg["total_ms"] else None,
                    "raw_sql": b["raw"], "agg_sql": b["agg"]})
    return out


def _plain(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if hasattr(v, "is_finite"):  # Decimal
        return float(v)
    return v


def _dicts(conn, sql: str, args: dict | None = None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        cols = [c.name for c in cur.description]
        return [{c: _plain(v) for c, v in zip(cols, r)} for r in cur.fetchall()]


def dashboard(conn, night: str | None = None) -> dict:
    """Everything the live Tiger dashboard shows, read from the continuous aggregates and the
    unified join. `ms` is the wall time of each read as the API saw it (network included)."""
    import time

    if not has_timescale(conn):
        raise RuntimeError("the dashboard needs TimescaleDB (hypertables and continuous aggregates)")
    ms: dict[str, float] = {}

    def read(key: str, sql: str, args: dict | None = None) -> list[dict]:
        t = time.perf_counter()
        rows = _dicts(conn, sql, args)
        ms[key] = round((time.perf_counter() - t) * 1000, 1)
        return rows

    meta = read("meta", "SELECT now() AS as_of, (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb')"
                        " AS timescaledb, current_setting('server_version_num')::int / 10000 AS postgres")[0]
    hypertables = read("hypertables", HYPERTABLES_SQL)
    counts = read("rows", " UNION ALL ".join(
        f"SELECT '{t}' AS name, count(*) AS rows FROM dcsim.{t}"
        for t in [h["name"] for h in hypertables] + ["planned_batches", "forecasts", "calibrations", "dish_nightly",
                                                      *DASH_CAGGS]))
    n_rows = {c["name"]: c["rows"] for c in counts}
    for h in hypertables:
        h["rows"] = n_rows.get(h["name"])
    aggregates = [a | {"rows": n_rows.get(a["name"]), "source_rows": n_rows.get(a["source"])}
                  for a in read("aggregates", AGGREGATES_SQL)]
    waste = read("waste_by_night", WASTE_BY_NIGHT_SQL)
    nights = [w["night"] for w in waste]
    night = night if night in nights else (nights[-1] if nights else None)
    pans = read("pans", PANS_SQL, {"night": night}) if night else []
    return {
        **meta, "synthetic": True, "night": night, "nights": nights,
        "hypertables": hypertables,
        "tables": [{"name": t, "rows": n_rows.get(t)} for t in ("planned_batches", "forecasts", "calibrations")],
        "aggregates": aggregates,
        "waste_by_night": waste,
        "top_waste": read("top_waste", TOP_WASTE_SQL),
        "pans": pans,
        "stack": read("stack", STACK_SQL),
        "ledger": scorecard(conn)[-8:],
        "ms": ms,
        "note": "Recorded nights are SYNTHETIC (generated by the engine) until UMass Dining shares real exports.",
    }
