-- DC What-If: Tiger Data (TimescaleDB + pgvector) schema and categories for the website.
-- Generated 2026-11-14 by `uv run python -m dcsim.tiger_sql` (engine 0.6.2).
-- Idempotent: safe to run again. Existing rows are kept; new columns start NULL.
-- Fact tables are filled by `dcsim tsdb load` (observed, categories) and `dcsim calibrate` (simulated
-- metrics, dish_nights, station_hours, minute_series). Category tables are also filled here.
-- Observed data is SYNTHETIC until UMass Dining shares real exports.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;

-- ============ 1. Existing tables (as tsdb.py creates them) ============

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

-- ============ 2. New columns on existing tables (engine 0.4+) ============
ALTER TABLE dcsim.nights
  ADD COLUMN IF NOT EXISTS waste_cost_usd double precision,
  ADD COLUMN IF NOT EXISTS engine_version text,
  ADD COLUMN IF NOT EXISTS config_hash text,
  ADD COLUMN IF NOT EXISTS dinner_students int,
  ADD COLUMN IF NOT EXISTS late_night_students int,
  ADD COLUMN IF NOT EXISTS missed_first_choice_rate double precision,
  ADD COLUMN IF NOT EXISTS missed_meal int,
  ADD COLUMN IF NOT EXISTS wait_p50_min double precision,
  ADD COLUMN IF NOT EXISTS wait_p90_min double precision,
  ADD COLUMN IF NOT EXISTS lines_skipped int,
  ADD COLUMN IF NOT EXISTS empty_pans_seen int,
  ADD COLUMN IF NOT EXISTS in_hall_at_2330 int,
  ADD COLUMN IF NOT EXISTS batch_wait_min double precision,
  ADD COLUMN IF NOT EXISTS cook_busy_share double precision,
  ADD COLUMN IF NOT EXISTS equipment_peak jsonb,
  ADD COLUMN IF NOT EXISTS empty_pan_wait_min double precision,
  ADD COLUMN IF NOT EXISTS seat_wait_min double precision,
  ADD COLUMN IF NOT EXISTS peak_seated int,
  ADD COLUMN IF NOT EXISTS swipe_wait_min double precision,
  ADD COLUMN IF NOT EXISTS swipe_queue_max int,
  ADD COLUMN IF NOT EXISTS group_students int,
  ADD COLUMN IF NOT EXISTS allergy_students int,
  ADD COLUMN IF NOT EXISTS allergy_no_meal int,
  ADD COLUMN IF NOT EXISTS dinner_stockout_min double precision,
  ADD COLUMN IF NOT EXISTS late_night_stockout_min double precision,
  ADD COLUMN IF NOT EXISTS dinner_waste_portions int,
  ADD COLUMN IF NOT EXISTS late_night_waste_portions int;
ALTER TABLE dcsim.pan_windows
  ADD COLUMN IF NOT EXISTS period text;
ALTER TABLE dcsim.waste_log
  ADD COLUMN IF NOT EXISTS period text;
ALTER TABLE dcsim.planned_batches
  ADD COLUMN IF NOT EXISTS period text,
  ADD COLUMN IF NOT EXISTS cook_min double precision,
  ADD COLUMN IF NOT EXISTS hold_min double precision,
  ADD COLUMN IF NOT EXISTS last_batch_min int,
  ADD COLUMN IF NOT EXISTS expected_demand double precision,
  ADD COLUMN IF NOT EXISTS taper_from_min int,
  ADD COLUMN IF NOT EXISTS taper_batch_size int,
  ADD COLUMN IF NOT EXISTS taper_reorder_point int,
  ADD COLUMN IF NOT EXISTS forecast_reorder boolean;
ALTER TABLE dcsim.forecasts
  ADD COLUMN IF NOT EXISTS weather text,
  ADD COLUMN IF NOT EXISTS event_night boolean;

-- ============ 3. New fact tables (engine 0.4+) ============

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

CREATE TABLE IF NOT EXISTS dcsim.night_vectors (
  run_id text NOT NULL, night date NOT NULL,
  weekday text, weather text, event_night boolean, n_dishes int,
  embedding vector(77) NOT NULL,
  PRIMARY KEY (run_id, night)
);

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

-- ============ 4. Category tables ============

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

-- ============ 5. Hypertables, columnstore, continuous aggregate ============

SELECT create_hypertable('dcsim.nights', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
SELECT create_hypertable('dcsim.pan_windows', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
SELECT create_hypertable('dcsim.waste_log', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);

SELECT create_hypertable('dcsim.dish_nights', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
SELECT create_hypertable('dcsim.station_hours', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
SELECT create_hypertable('dcsim.minute_series', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);

SELECT create_hypertable('dcsim.forecast_results', by_range('night', INTERVAL '28 days'), if_not_exists => TRUE);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables
                 WHERE hypertable_schema = 'dcsim' AND hypertable_name = 'nights' AND compression_enabled) THEN
    ALTER TABLE dcsim.nights SET (timescaledb.enable_columnstore, timescaledb.segmentby = 'scenario', timescaledb.orderby = 'ts, run_id, draw, seed');
  END IF;
END $$;
CALL add_columnstore_policy('dcsim.nights', after => INTERVAL '1 day', if_not_exists => true);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables
                 WHERE hypertable_schema = 'dcsim' AND hypertable_name = 'pan_windows' AND compression_enabled) THEN
    ALTER TABLE dcsim.pan_windows SET (timescaledb.enable_columnstore, timescaledb.segmentby = 'scenario, dish_id', timescaledb.orderby = 'ts, seed');
  END IF;
END $$;
CALL add_columnstore_policy('dcsim.pan_windows', after => INTERVAL '1 day', if_not_exists => true);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables
                 WHERE hypertable_schema = 'dcsim' AND hypertable_name = 'waste_log' AND compression_enabled) THEN
    ALTER TABLE dcsim.waste_log SET (timescaledb.enable_columnstore, timescaledb.segmentby = 'dish_id', timescaledb.orderby = 'ts');
  END IF;
END $$;
CALL add_columnstore_policy('dcsim.waste_log', after => INTERVAL '1 day', if_not_exists => true);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables
                 WHERE hypertable_schema = 'dcsim' AND hypertable_name = 'dish_nights' AND compression_enabled) THEN
    ALTER TABLE dcsim.dish_nights SET (timescaledb.enable_columnstore, timescaledb.segmentby = 'scenario, dish_id', timescaledb.orderby = 'ts, seed');
  END IF;
END $$;
CALL add_columnstore_policy('dcsim.dish_nights', after => INTERVAL '1 day', if_not_exists => true);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables
                 WHERE hypertable_schema = 'dcsim' AND hypertable_name = 'station_hours' AND compression_enabled) THEN
    ALTER TABLE dcsim.station_hours SET (timescaledb.enable_columnstore, timescaledb.segmentby = 'scenario, station', timescaledb.orderby = 'ts, seed');
  END IF;
END $$;
CALL add_columnstore_policy('dcsim.station_hours', after => INTERVAL '1 day', if_not_exists => true);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables
                 WHERE hypertable_schema = 'dcsim' AND hypertable_name = 'minute_series' AND compression_enabled) THEN
    ALTER TABLE dcsim.minute_series SET (timescaledb.enable_columnstore, timescaledb.segmentby = 'scenario, kind, key', timescaledb.orderby = 'ts, seed');
  END IF;
END $$;
CALL add_columnstore_policy('dcsim.minute_series', after => INTERVAL '1 day', if_not_exists => true);
CREATE MATERIALIZED VIEW IF NOT EXISTS dcsim.dish_nightly WITH (timescaledb.continuous) AS
SELECT time_bucket('1 day', ts, 'America/New_York') AS day, source, scenario, run_id, seed, station, dish_id,
       sum(portions_out) AS cooked, sum(portions_eaten) AS eaten,
       last(portions_left, ts) AS left_at_close,
       count(*) FILTER (WHERE portions_left = 0) AS windows_empty
FROM dcsim.pan_windows
GROUP BY 1, source, scenario, run_id, seed, station, dish_id
 WITH NO DATA;

-- Dashboard aggregates: real-time, with refresh policies. They are created empty; fill them once with
--   CALL refresh_continuous_aggregate('dcsim.waste_daily_by_dish', NULL, NULL);
--   CALL refresh_continuous_aggregate('dcsim.pan_hourly', NULL, NULL);
-- (each as its own statement, outside a transaction; `dcsim tsdb refresh` does both).

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM timescaledb_information.continuous_aggregates
             WHERE view_schema = 'dcsim' AND view_name = 'waste_daily_by_dish')
     AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_schema = 'dcsim' AND table_name = 'waste_daily_by_dish' AND column_name = 'night') THEN
    DROP MATERIALIZED VIEW dcsim.waste_daily_by_dish;
  END IF;
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM timescaledb_information.continuous_aggregates
             WHERE view_schema = 'dcsim' AND view_name = 'pan_hourly')
     AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                     WHERE table_schema = 'dcsim' AND table_name = 'pan_hourly' AND column_name = 'sum_left') THEN
    DROP MATERIALIZED VIEW dcsim.pan_hourly;
  END IF;
END $$;
CREATE MATERIALIZED VIEW IF NOT EXISTS dcsim.waste_daily_by_dish WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 day', ts, 'America/New_York', TIMESTAMPTZ '2026-01-01 06:00:00-05') AS bucket, night, station, dish_id, dish, reason, period,
       sum(portions) AS portions, sum(weight_lb) AS weight_lb, count(*) AS events
FROM dcsim.waste_log
GROUP BY 1, night, station, dish_id, dish, reason, period
WITH NO DATA;
SELECT add_continuous_aggregate_policy('dcsim.waste_daily_by_dish', start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '1 hour', if_not_exists => true);
CREATE MATERIALIZED VIEW IF NOT EXISTS dcsim.pan_hourly WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket('1 hour', ts, 'America/New_York') AS bucket, night, source, scenario, station, dish_id, dish, period,
       sum(portions_out) AS portions_out, sum(portions_eaten) AS portions_eaten,
       min(portions_left) AS min_left, max(portions_left) AS max_left, sum(portions_left) AS sum_left,
       count(*) FILTER (WHERE portions_left = 0) AS windows_empty, count(*) AS windows
FROM dcsim.pan_windows
GROUP BY 1, night, source, scenario, station, dish_id, dish, period
WITH NO DATA;
SELECT add_continuous_aggregate_policy('dcsim.pan_hourly', start_offset => INTERVAL '2 days', end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '30 minutes', if_not_exists => true);

-- ============ 6. Views (forecast checks + website views) ============

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

-- ============ 7. Track record: scoring procedure, nightly job, weekly aggregate ============
-- Fill it once with CALL dcsim.score_forecasts(); the job then runs it every morning.

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

CREATE MATERIALIZED VIEW IF NOT EXISTS dcsim.forecast_weekly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '7 days', night, DATE '2026-10-01') AS week, origin, method,
       count(*) AS nights,
       count(*) FILTER (WHERE waste_inside) AS waste_inside,
       avg(waste_lb - waste_p50) AS waste_bias_lb,
       avg(abs(waste_lb - waste_p50)) AS waste_mae_lb,
       count(*) FILTER (WHERE stockout_inside) AS stockout_inside,
       avg(stockout_min - stockout_p50) AS stockout_bias_min,
       avg(students - students_p50) AS students_bias
FROM dcsim.forecast_results
GROUP BY 1, 2, 3
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

-- ============ 8. Comments (what an assistant reading the schema, e.g. the Tiger MCP, sees) ============

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

COMMENT ON VIEW dcsim.forecast_weekly IS 'Continuous aggregate (real-time) of forecast_results by week (weeks start Thursday 2026-10-01; weeks of 2026-10-29 and 2026-11-05 are the held-out split): nights, nights inside the 10-90% range, bias (observed minus median).';
COMMENT ON VIEW dcsim.waste_daily_by_dish IS 'Continuous aggregate (real-time) of waste_log per night, dish and reason.';
COMMENT ON VIEW dcsim.pan_hourly IS 'Continuous aggregate (real-time) of pan_windows per clock hour, dish and scenario. A dish''s final hour includes the close-out (0 left).';
COMMENT ON VIEW dcsim.dish_nightly IS 'Continuous aggregate of pan_windows per dish per day and run (materialized only: refreshed by `dcsim tsdb refresh` after each load).';

-- ============ 9. Category rows (from the current data files) ============
INSERT INTO dcsim.cat_values (category, code, label, sort) VALUES
  ('source', 'observed', 'Recorded (synthetic history until real exports)', 1),
  ('source', 'simulated', 'Simulated by the engine', 2),
  ('waste_reason', 'expired', 'Expired on the line (past hold time)', 1),
  ('waste_reason', 'close', 'Left in pans at close', 2),
  ('waste_reason', 'plate', 'Plate waste (students'' plates)', 3),
  ('weather', 'clear', 'Clear', 1),
  ('weather', 'rain', 'Rain', 2),
  ('weather', 'cold', 'Cold', 3),
  ('weather', 'snow', 'Snow', 4),
  ('weather', 'cloudy', 'Cloudy', 5),
  ('weather', 'windy', 'Windy', 6),
  ('weather', 'other', 'Other', 7),
  ('role', 'entree', 'Entree', 1),
  ('role', 'starch', 'Starch', 2),
  ('role', 'side', 'Side', 3),
  ('role', 'soup', 'Soup', 4),
  ('role', 'dessert', 'Dessert', 5),
  ('role', 'bread', 'Bread', 6),
  ('diet', 'none', 'No restriction', 1),
  ('diet', 'vegetarian', 'Vegetarian', 2),
  ('diet', 'vegan', 'Vegan', 3),
  ('diet', 'halal', 'Halal', 4),
  ('diet', 'pescetarian', 'Pescetarian', 5),
  ('vessel', 'plate', 'Plate', 1),
  ('vessel', 'bowl', 'Bowl', 2),
  ('vessel', 'cup', 'Cup', 3),
  ('method', 'naive', 'Naive forecast (plan as written)', 1),
  ('method', 'calibrated', 'Calibrated forecast (fitted crowd and production error)', 2),
  ('split', 'train', 'Calibration training nights', 1),
  ('split', 'test', 'Held-out nights', 2),
  ('series_kind', 'inside', 'Students inside the building', 1),
  ('series_kind', 'queue', 'Students in line at a station', 2),
  ('series_kind', 'pan', 'Portions on hand for a dish (line + hot-hold)', 3),
  ('resource_kind', 'cooks', 'Cooks on the line, per service period', 1),
  ('resource_kind', 'equipment', 'Equipment units (one batch each)', 2)
ON CONFLICT (category, code) DO UPDATE SET label = EXCLUDED.label, sort = EXCLUDED.sort;
INSERT INTO dcsim.periods (period, label, opens, closes, expected_students, note) VALUES
  ('dinner', 'Dinner', '16:30', '21:00', 3000, 'Lines formed at 9 pm are still served (last call)'),
  ('late_night', 'Late night', '21:00', '00:00', 1000, 'Serving stops at midnight; seated students may stay')
ON CONFLICT (period) DO UPDATE SET label = EXCLUDED.label, opens = EXCLUDED.opens, closes = EXCLUDED.closes, expected_students = EXCLUDED.expected_students, note = EXCLUDED.note;
INSERT INTO dcsim.stations (station, name, servers, self_serve, to_order, service_median_min, pos_x_m, pos_y_m, equipment, note) VALUES
  ('grill', 'Grill Station', 2, false, false, 0.5, 26.2, 19.5, 'flat_top', NULL),
  ('tandoor', 'Tandoor', 2, false, false, 0.5, 33.8, 18.2, 'tandoor', NULL),
  ('mediterranean', 'Mediterranean', 2, false, false, 0.5, 25.7, 25.7, NULL, NULL),
  ('seasons', 'Seasons', 2, false, false, 0.5, 36.0, 26.5, NULL, NULL),
  ('latino', 'Latino', 2, false, false, 0.5, 9.6, 17.5, NULL, NULL),
  ('street_food', 'Street Food', 2, false, false, 0.5, 48.7, 26.5, 'wok', NULL),
  ('noodle_bowl', 'Noodle Bowl', 2, false, false, 0.6, 52.9, 20.3, NULL, NULL),
  ('sushi', 'Sushi', 3, false, true, 2.5, 49.4, 17.5, NULL, NULL),
  ('display', 'Display Station', 4, true, false, 0.3, 55.5, 15.1, NULL, NULL),
  ('soups', 'Soups', 2, true, false, 0.25, 28.6, 14.7, NULL, NULL),
  ('breads', 'Breads', 2, true, false, 0.15, 20.5, 24.8, NULL, NULL),
  ('desserts', 'Desserts', 3, true, false, 0.2, 16.1, 24.9, NULL, NULL),
  ('carving', 'Carving', 2, false, false, 0.5, 53.5, 15.1, NULL, 'Not a published station: dishes move here by date via overrides.json -> station_by_date (E7). A staffed carving line beside Display; 2 carvers and 0.5 min per plate are guesses (team: hot stations take 30-60 s per plate).')
ON CONFLICT (station) DO UPDATE SET name = EXCLUDED.name, servers = EXCLUDED.servers, self_serve = EXCLUDED.self_serve, to_order = EXCLUDED.to_order, service_median_min = EXCLUDED.service_median_min, pos_x_m = EXCLUDED.pos_x_m, pos_y_m = EXCLUDED.pos_y_m, equipment = EXCLUDED.equipment, note = EXCLUDED.note;
INSERT INTO dcsim.dishes (menu_date, dish_id, name, station, period, role, vessel, hot, diet_tags, allergens, popularity, batch_cooked, equipment, prep_min) VALUES
  ('2026-11-13', 'soups.homemade_clam_chowder', 'Homemade Clam Chowder', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian']::text[], ARRAY['Milk', 'Shellfish', 'Gluten', 'Corn', 'Wheat']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-13', 'soups.spicy_lentil_veggie_soup', 'Spicy Lentil Veggie Soup', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-13', 'grill.black_bean_burger', 'Black Bean Burger', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-13', 'grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-13', 'grill.halal_french_fries', 'Halal French Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'fryer', 5.0),
  ('2026-11-13', 'grill.hand_cut_sweet_potato_fries', 'Hand Cut Sweet Potato Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'fryer', 5.0),
  ('2026-11-13', 'grill.herb_roasted_mushrooms', 'Herb Roasted Mushrooms', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7071, true, 'flat_top', 6.0),
  ('2026-11-13', 'grill.roasted_cauliflower', 'Roasted Cauliflower', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7071, true, 'flat_top', 6.0),
  ('2026-11-13', 'grill.savory_baked_chicken_thigh', 'Savory Baked Chicken Thigh', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-13', 'desserts.blueberry_crisp', 'Blueberry Crisp', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-13', 'desserts.orange_cake_w_cream_cheese_frosting', 'Orange Cake w/Cream Cheese Frosting', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY[]::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-13', 'breads.multigrain_bread', 'Multigrain Bread', 'breads', 'dinner', 'bread', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.25, true, NULL, 3.0),
  ('2026-11-13', 'noodle_bowl.hot_pot_chicken_beef_pork_fish_shellfish', 'Hot Pot (Chicken, Beef, Pork, Fish, Shellfish)', 'noodle_bowl', 'dinner', 'entree', 'bowl', true, ARRAY[]::text[], ARRAY['Milk', 'Eggs', 'Fish', 'Shellfish', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 2.0, true, 'oven', 10.0),
  ('2026-11-13', 'sushi.california_roll', 'California Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian']::text[], ARRAY['Eggs', 'Fish', 'Shellfish', 'Soy', 'Corn', 'Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-13', 'sushi.crispy_shrimp_sushi_roll', 'Crispy Shrimp Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian']::text[], ARRAY['Milk', 'Eggs', 'Shellfish', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-13', 'sushi.purple_sweet_potato_tempura_sushi_roll', 'Purple Sweet Potato Tempura Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-13', 'sushi.teriyaki_pulmuone_tofu_roll', 'Teriyaki Pulmuone Tofu Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-13', 'sushi.vegetable_sushi_roll', 'Vegetable Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-13', 'display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.6, true, NULL, 10.0),
  ('2026-11-13', 'street_food.plain_sushi_rice', 'Plain Sushi Rice', 'street_food', 'dinner', 'starch', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 1.4, true, 'wok', 5.0),
  ('2026-11-13', 'street_food.stir_fry_green_vegetables', 'Stir Fry Green Vegetables', 'street_food', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.0, true, 'wok', 6.0),
  ('2026-11-13', 'street_food.teriyaki_salmon', 'Teriyaki Salmon', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian']::text[], ARRAY['Fish', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4142, true, 'wok', 10.0),
  ('2026-11-13', 'street_food.teriyaki_tofu_w_sesame_seeds', 'Teriyaki Tofu w/Sesame Seeds', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4142, true, 'wok', 10.0),
  ('2026-11-13', 'tandoor.chicken_tikka_masala_rasoi', 'Chicken Tikka Masala Rasoi', 'tandoor', 'dinner', 'entree', 'bowl', true, ARRAY['halal']::text[], ARRAY['Milk', 'Corn']::text[], 0.5774, true, 'tandoor', 10.0),
  ('2026-11-13', 'tandoor.cumin_basmati_rice', 'Cumin Basmati Rice', 'tandoor', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.495, true, 'tandoor', 5.0),
  ('2026-11-13', 'tandoor.naan', 'Naan', 'tandoor', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Gluten', 'Corn', 'Sesame', 'Wheat']::text[], 0.495, true, 'tandoor', 5.0),
  ('2026-11-13', 'tandoor.paneer_jalfrezi_cheese', 'Paneer Jalfrezi (Cheese)', 'tandoor', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk']::text[], 0.5774, true, 'tandoor', 10.0),
  ('2026-11-13', 'tandoor.stir_fried_cabbage_w_curry_leaves_tamarind_chutney', 'Stir Fried Cabbage w/Curry Leaves & Tamarind Chutney', 'tandoor', 'dinner', 'side', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.3536, true, 'tandoor', 6.0),
  ('2026-11-13', 'tandoor.vegetable_curry_samosa', 'Vegetable Curry Samosa', 'tandoor', 'dinner', 'entree', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.5774, true, 'tandoor', 10.0),
  ('2026-11-13', 'tandoor.watermelon_curry_salad', 'Watermelon Curry Salad', 'tandoor', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Sesame']::text[], 0.3536, true, NULL, 6.0),
  ('2026-11-13', 'mediterranean.beef_bolognese_sauce_w_pancetta_pork', 'Beef Bolognese Sauce w/Pancetta (Pork)', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Milk', 'Corn']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-13', 'mediterranean.fish_w_almond_romesco_scallions', 'Fish w/Almond Romesco & Scallions', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian']::text[], ARRAY['Fish', 'Tree Nuts']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-13', 'mediterranean.linguini_pasta', 'Linguini Pasta', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Wheat']::text[], 0.4041, true, 'steamer', 5.0),
  ('2026-11-13', 'mediterranean.orecchiette_pasta', 'Orecchiette Pasta', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Wheat']::text[], 0.4041, true, 'steamer', 5.0),
  ('2026-11-13', 'mediterranean.orecchiette_w_peas_pancetta_pork', 'Orecchiette w/Peas & Pancetta (Pork)', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY[]::text[], ARRAY['Milk', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4041, true, 'steamer', 5.0),
  ('2026-11-13', 'mediterranean.roasted_root_vegetables_w_balsamic', 'Roasted Root Vegetables w/Balsamic', 'mediterranean', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.5, true, 'steamer', 6.0),
  ('2026-11-13', 'seasons.agrodolce_honeynut_squash', 'Agrodolce Honeynut Squash', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY[]::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-13', 'seasons.eggplant_parmesan', 'Eggplant Parmesan', 'seasons', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Corn']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-13', 'seasons.gf_garlic_bread', 'GF Garlic Bread', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Corn']::text[], 0.495, true, NULL, 5.0),
  ('2026-11-13', 'seasons.gf_penne_pasta', 'GF Penne Pasta', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-13', 'seasons.plant_based_gnocchi_mushrooms', 'Plant Based Gnocchi & Mushrooms', 'seasons', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-13', 'seasons.sauteed_broccoli', 'Sauteed Broccoli', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-13', 'latino.chicken_taco_filling', 'Chicken Taco Filling', 'latino', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Corn']::text[], 1.4142, true, 'oven', 10.0),
  ('2026-11-13', 'latino.corn_w_red_peppers', 'Corn w/Red Peppers', 'latino', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.7071, true, 'steamer', 6.0),
  ('2026-11-13', 'latino.filling_for_beef_tacos', 'Filling for Beef Tacos', 'latino', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Soy', 'Corn']::text[], 1.4142, true, 'oven', 10.0),
  ('2026-11-13', 'latino.pinto_beans', 'Pinto Beans', 'latino', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.7071, true, 'steamer', 6.0),
  ('2026-11-13', 'latino.quinoa_mango_salad', 'Quinoa & Mango Salad', 'latino', 'dinner', 'starch', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.9899, true, NULL, 5.0),
  ('2026-11-13', 'latino.yellow_rice', 'Yellow Rice', 'latino', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.9899, true, 'steamer', 5.0),
  ('2026-11-13', 'late.grill.black_bean_burger', 'Black Bean Burger', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-13', 'late.grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-13', 'late.grill.halal_french_fries', 'Halal French Fries', 'grill', 'late_night', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4, true, 'fryer', 5.0),
  ('2026-11-13', 'late.grill.roasted_cauliflower', 'Roasted Cauliflower', 'grill', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 1.0, true, 'flat_top', 6.0),
  ('2026-11-13', 'late.grill.savory_baked_chicken_thigh', 'Savory Baked Chicken Thigh', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-13', 'late.display.buffalo_chicken_for_mac_cheese', 'Buffalo Chicken for Mac & Cheese', 'display', 'late_night', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-13', 'late.display.homemade_macaroni_cheese', 'Homemade Macaroni & Cheese', 'display', 'late_night', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Gluten', 'Wheat']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-13', 'late.display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'late_night', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, NULL, 10.0),
  ('2026-11-13', 'late.display.plant_based_buffalo_chic_n', 'Plant Based Buffalo Chic''n', 'display', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-13', 'late.display.steamed_broccoli', 'Steamed Broccoli', 'display', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-13', 'late.display.waffle_fries', 'Waffle Fries', 'display', 'late_night', 'dessert', 'plate', false, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4243, true, 'fryer', 10.0),
  ('2026-11-14', 'soups.roasted_chicken_noodle_soup_w_herbs', 'Roasted Chicken Noodle Soup w/Herbs', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY[]::text[], ARRAY['Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-14', 'soups.tomato_basil_soup', 'Tomato Basil Soup', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Corn']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-14', 'grill.black_bean_burger', 'Black Bean Burger', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-14', 'grill.grilled_chicken_thighs', 'Grilled Chicken Thighs', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-14', 'grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-14', 'grill.halal_french_fries', 'Halal French Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.8083, true, 'fryer', 5.0),
  ('2026-11-14', 'grill.hand_cut_sweet_potato_fries', 'Hand Cut Sweet Potato Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.8083, true, 'fryer', 5.0),
  ('2026-11-14', 'grill.local_roasted_sweet_potatoes', 'Local Roasted Sweet Potatoes', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.8083, true, 'flat_top', 5.0),
  ('2026-11-14', 'grill.roasted_zucchini', 'Roasted Zucchini', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 1.0, true, 'flat_top', 6.0),
  ('2026-11-14', 'desserts.choc_straw_cake_w_nutella_icing_hazelnuts', 'Choc Straw Cake w/Nutella Icing (Hazelnuts)', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Tree Nuts', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-14', 'desserts.coconut_key_lime_cake', 'Coconut Key Lime Cake', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-14', 'breads.rustic_italian_bread', 'Rustic Italian Bread', 'breads', 'dinner', 'bread', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.25, true, NULL, 3.0),
  ('2026-11-14', 'noodle_bowl.chicken_shoyu_ramen_bar_fish_shellfish', 'Chicken Shoyu Ramen Bar (Fish, Shellfish)', 'noodle_bowl', 'dinner', 'entree', 'bowl', true, ARRAY[]::text[], ARRAY['Fish', 'Shellfish', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 1.0, true, 'oven', 10.0),
  ('2026-11-14', 'sushi.california_roll', 'California Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian']::text[], ARRAY['Eggs', 'Fish', 'Shellfish', 'Soy', 'Corn', 'Sesame']::text[], 0.5774, false, NULL, NULL),
  ('2026-11-14', 'sushi.purple_sweet_potato_tempura_sushi_roll', 'Purple Sweet Potato Tempura Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.5774, false, NULL, NULL),
  ('2026-11-14', 'sushi.vegetable_sushi_roll', 'Vegetable Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Sesame']::text[], 0.5774, false, NULL, NULL),
  ('2026-11-14', 'display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.6, true, NULL, 10.0),
  ('2026-11-14', 'street_food.fried_sweet_chili_pulmuone_tofu', 'Fried Sweet Chili Pulmuone Tofu', 'street_food', 'dinner', 'entree', 'bowl', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-14', 'street_food.kung_pao_peanut_chicken', 'Kung Pao Peanut Chicken', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Peanuts', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-14', 'street_food.stir_fry_green_vegetables', 'Stir Fry Green Vegetables', 'street_food', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.0, true, 'wok', 6.0),
  ('2026-11-14', 'street_food.vegetable_fried_rice_egg', 'Vegetable Fried Rice (Egg)', 'street_food', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4, true, 'wok', 5.0),
  ('2026-11-14', 'street_food.vegetable_spring_rolls', 'Vegetable Spring Rolls', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-14', 'tandoor.lemon_feta_roasted_potatoes', 'Lemon Feta Roasted Potatoes', 'tandoor', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk']::text[], 0.7, true, 'tandoor', 5.0),
  ('2026-11-14', 'tandoor.moraccan_green_garbanzo_bean_salad', 'Moraccan Green Garbanzo Bean Salad', 'tandoor', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2887, true, NULL, 6.0),
  ('2026-11-14', 'tandoor.rosemary_honey_glazed_turnips', 'Rosemary Honey Glazed Turnips', 'tandoor', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY[]::text[], 0.2887, true, 'tandoor', 6.0),
  ('2026-11-14', 'tandoor.savory_baked_chicken_leg', 'Savory Baked Chicken Leg', 'tandoor', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.0, true, 'tandoor', 10.0),
  ('2026-11-14', 'tandoor.tomato_salad_w_parsley_sumac', 'Tomato Salad w/Parsley & Sumac', 'tandoor', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2887, true, NULL, 6.0),
  ('2026-11-14', 'mediterranean.fettuccini_pasta', 'Fettuccini Pasta', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Wheat']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-14', 'mediterranean.lasagna_rollette_w_pesto_tomatoes_and_artichokes', 'Lasagna Rollette w/Pesto, Tomatoes, and Artichokes', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Corn', 'Wheat']::text[], 0.5774, true, 'oven', 10.0),
  ('2026-11-14', 'mediterranean.meatball_sauce', 'Meatball Sauce', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Milk', 'Eggs']::text[], 0.5774, true, 'oven', 10.0),
  ('2026-11-14', 'mediterranean.mediterranean_tomato_pie', 'Mediterranean Tomato Pie', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.5774, true, 'oven', 10.0),
  ('2026-11-14', 'mediterranean.whole_grain_penne', 'Whole Grain Penne', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Wheat']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-14', 'seasons.chickpeas_artichokes_spinach_rstd_red_peppers_jain', 'Chickpeas, Artichokes, Spinach & Rstd Red Peppers Jain', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2236, true, 'steamer', 6.0),
  ('2026-11-14', 'seasons.fresh_sauteed_spinach_jain', 'Fresh Sauteed Spinach Jain', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2236, true, 'steamer', 6.0),
  ('2026-11-14', 'seasons.gf_penne_pasta', 'GF Penne Pasta', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-14', 'seasons.roasted_cauliflower_salad', 'Roasted Cauliflower Salad', 'seasons', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2236, true, NULL, 6.0),
  ('2026-11-14', 'seasons.roasted_pepper_tomato_salad', 'Roasted Pepper, Tomato Salad', 'seasons', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2236, true, NULL, 6.0),
  ('2026-11-14', 'seasons.roasted_zucchini', 'Roasted Zucchini', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2236, true, 'steamer', 6.0),
  ('2026-11-14', 'seasons.saffron_rice_pilaf_jain', 'Saffron Rice Pilaf Jain', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-14', 'latino.beef_barbacoa_meat', 'Beef Barbacoa Meat', 'latino', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Corn']::text[], 1.4142, true, 'oven', 10.0),
  ('2026-11-14', 'latino.black_beans', 'Black Beans', 'latino', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.7071, true, 'steamer', 6.0),
  ('2026-11-14', 'latino.jerk_chicken', 'Jerk Chicken', 'latino', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Soy', 'Corn']::text[], 1.4142, true, 'oven', 10.0),
  ('2026-11-14', 'latino.mexican_tomato_salad', 'Mexican Tomato Salad', 'latino', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk']::text[], 0.7071, true, NULL, 6.0),
  ('2026-11-14', 'latino.yellow_rice', 'Yellow Rice', 'latino', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 1.4, true, 'steamer', 5.0),
  ('2026-11-14', 'late.grill.baked_herbs_de_provence_chicken', 'Baked Herbs de Provence Chicken', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-14', 'late.grill.black_bean_burger', 'Black Bean Burger', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-14', 'late.grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-14', 'late.grill.halal_french_fries', 'Halal French Fries', 'grill', 'late_night', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4, true, 'fryer', 5.0),
  ('2026-11-14', 'late.grill.roasted_zucchini', 'Roasted Zucchini', 'grill', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 1.0, true, 'flat_top', 6.0),
  ('2026-11-14', 'late.display.chicken_noodle', 'Chicken Noodle', 'display', 'late_night', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Eggs', 'Gluten', 'Corn', 'Wheat']::text[], 0.5, true, 'oven', 10.0),
  ('2026-11-14', 'late.display.grilled_cheese_sandwich', 'Grilled Cheese Sandwich', 'display', 'late_night', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.5, true, 'oven', 10.0),
  ('2026-11-14', 'late.display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'late_night', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.6, true, NULL, 10.0),
  ('2026-11-14', 'late.display.tomato_basil_soup', 'Tomato Basil Soup', 'display', 'late_night', 'entree', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Corn']::text[], 0.5, true, 'oven', 10.0),
  ('2026-11-14', 'late.display.violife_grilled_cheese_sandwich', 'VIOLIFE Grilled Cheese Sandwich', 'display', 'late_night', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.5, true, 'oven', 10.0),
  ('2026-11-15', 'soups.butternut_ginger_soup', 'Butternut Ginger Soup', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Corn']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-15', 'soups.native_three_sisters_soup', 'Native Three Sisters Soup', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-15', 'grill.black_bean_burger', 'Black Bean Burger', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-15', 'grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-15', 'grill.halal_french_fries', 'Halal French Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'fryer', 5.0),
  ('2026-11-15', 'grill.hand_cut_sweet_potato_fries', 'Hand Cut Sweet Potato Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'fryer', 5.0),
  ('2026-11-15', 'grill.key_west_baked_chicken', 'Key West Baked Chicken', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-15', 'grill.oven_roasted_summer_squash', 'Oven Roasted Summer Squash', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7071, true, 'flat_top', 6.0),
  ('2026-11-15', 'grill.roasted_butternut_squash', 'Roasted Butternut Squash', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7071, true, 'flat_top', 6.0),
  ('2026-11-15', 'desserts.churro_bread_pudding', 'Churro Bread Pudding', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, NULL, 10.0),
  ('2026-11-15', 'desserts.fudge_brownie_w_sesame', 'Fudge Brownie w/Sesame', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-15', 'breads.parmesan_rosemary_focaccia_bread', 'Parmesan Rosemary Focaccia Bread', 'breads', 'dinner', 'bread', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Gluten', 'Corn', 'Wheat']::text[], 0.25, true, NULL, 3.0),
  ('2026-11-15', 'noodle_bowl.chicken_pho_broth_fish', 'Chicken Pho Broth (Fish)', 'noodle_bowl', 'dinner', 'entree', 'bowl', true, ARRAY[]::text[], ARRAY['Fish', 'Corn']::text[], 1.1547, true, 'oven', 10.0),
  ('2026-11-15', 'noodle_bowl.green_curry_pho_broth_anchovies', 'Green Curry Pho Broth (Anchovies)', 'noodle_bowl', 'dinner', 'entree', 'bowl', true, ARRAY['halal', 'pescetarian']::text[], ARRAY['Milk', 'Fish', 'Corn']::text[], 1.1547, true, 'oven', 10.0),
  ('2026-11-15', 'noodle_bowl.vegetarian_pho_base', 'Vegetarian Pho Base', 'noodle_bowl', 'dinner', 'entree', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 1.1547, true, 'oven', 10.0),
  ('2026-11-15', 'sushi.california_roll', 'California Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian']::text[], ARRAY['Eggs', 'Fish', 'Shellfish', 'Soy', 'Corn', 'Sesame']::text[], 0.5774, false, NULL, NULL),
  ('2026-11-15', 'sushi.purple_sweet_potato_tempura_sushi_roll', 'Purple Sweet Potato Tempura Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.5774, false, NULL, NULL),
  ('2026-11-15', 'sushi.vegetable_sushi_roll', 'Vegetable Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Sesame']::text[], 0.5774, false, NULL, NULL),
  ('2026-11-15', 'display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.6, true, NULL, 10.0),
  ('2026-11-15', 'street_food.egg_tomato_stir_fry', 'Egg & Tomato Stir Fry', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Eggs', 'Corn', 'Sesame']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-15', 'street_food.mandarin_chicken_w_cashews', 'Mandarin Chicken w/Cashews', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Tree Nuts', 'Peanuts', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-15', 'street_food.mei_fun_noodles', 'Mei Fun Noodles', 'street_food', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4, true, 'wok', 5.0),
  ('2026-11-15', 'street_food.stir_fry_green_vegetables', 'Stir Fry Green Vegetables', 'street_food', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.0, true, 'wok', 6.0),
  ('2026-11-15', 'street_food.vegetable_spring_rolls', 'Vegetable Spring Rolls', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-15', 'tandoor.gigante_bean_tomato_stew', 'Gigante Bean & Tomato Stew', 'tandoor', 'dinner', 'entree', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy']::text[], 0.7071, true, 'tandoor', 10.0),
  ('2026-11-15', 'tandoor.moraccan_green_garbanzo_bean_salad', 'Moraccan Green Garbanzo Bean Salad', 'tandoor', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.3536, true, NULL, 6.0),
  ('2026-11-15', 'tandoor.slow_roasted_turkish_chicken_vegetables', 'Slow Roasted Turkish Chicken & Vegetables', 'tandoor', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 0.7071, true, 'tandoor', 10.0),
  ('2026-11-15', 'tandoor.tomato_salad_w_parsley_sumac', 'Tomato Salad w/Parsley & Sumac', 'tandoor', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.3536, true, NULL, 6.0),
  ('2026-11-15', 'tandoor.turmeric_basmati_rice', 'Turmeric Basmati Rice', 'tandoor', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7, true, 'tandoor', 5.0),
  ('2026-11-15', 'mediterranean.manicotti', 'Manicotti', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-15', 'mediterranean.penne_pasta', 'Penne Pasta', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Wheat']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-15', 'mediterranean.plant_based_spinach_pie', 'Plant Based Spinach Pie', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-15', 'mediterranean.whole_grain_penne', 'Whole Grain Penne', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Wheat']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-15', 'seasons.chickpeas_artichokes_spinach_rstd_red_peppers_jain', 'Chickpeas, Artichokes, Spinach & Rstd Red Peppers Jain', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.25, true, 'steamer', 6.0),
  ('2026-11-15', 'seasons.gf_penne_pasta', 'GF Penne Pasta', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-15', 'seasons.lemon_quinoa_w_craisins_thyme_jain', 'Lemon Quinoa w/Craisins & Thyme Jain', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-15', 'seasons.roasted_cauliflower_salad', 'Roasted Cauliflower Salad', 'seasons', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.25, true, NULL, 6.0),
  ('2026-11-15', 'seasons.roasted_pepper_tomato_salad', 'Roasted Pepper, Tomato Salad', 'seasons', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.25, true, NULL, 6.0),
  ('2026-11-15', 'seasons.sauteed_kale_jain', 'Sauteed Kale Jain', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.25, true, 'steamer', 6.0),
  ('2026-11-15', 'seasons.spanish_pisto_vegetable_stew', 'Spanish Pisto (Vegetable Stew)', 'seasons', 'dinner', 'entree', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 1.0, true, 'oven', 10.0),
  ('2026-11-15', 'latino.arroz_blanco', 'Arroz Blanco', 'latino', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 1.4, true, 'steamer', 5.0),
  ('2026-11-15', 'latino.braised_ancho_chili_lime_pork', 'Braised Ancho Chili Lime Pork', 'latino', 'dinner', 'entree', 'bowl', true, ARRAY[]::text[], ARRAY[]::text[], 2.0, true, 'oven', 10.0),
  ('2026-11-15', 'latino.corn_w_red_peppers', 'Corn w/Red Peppers', 'latino', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.5774, true, 'steamer', 6.0),
  ('2026-11-15', 'latino.mexican_tomato_salad', 'Mexican Tomato Salad', 'latino', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk']::text[], 0.5774, true, NULL, 6.0),
  ('2026-11-15', 'latino.pinto_beans', 'Pinto Beans', 'latino', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.5774, true, 'steamer', 6.0),
  ('2026-11-15', 'late.grill.black_bean_burger', 'Black Bean Burger', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-15', 'late.grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-15', 'late.grill.halal_french_fries', 'Halal French Fries', 'grill', 'late_night', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4, true, 'fryer', 5.0),
  ('2026-11-15', 'late.grill.key_west_baked_chicken', 'Key West Baked Chicken', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-15', 'late.grill.roasted_butternut_squash', 'Roasted Butternut Squash', 'grill', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 1.0, true, 'flat_top', 6.0),
  ('2026-11-15', 'late.display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'late_night', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.6, true, NULL, 10.0),
  ('2026-11-15', 'late.display.philly_cheese_steak_grinder', 'Philly Cheese Steak Grinder', 'display', 'late_night', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Milk', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-15', 'late.display.plant_based_pulmuone_steak_mushroom_grinder', 'Plant Based Pulmuone Steak & Mushroom Grinder', 'display', 'late_night', 'entree', 'plate', true, ARRAY['pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-15', 'late.display.seasoned_curly_fries', 'Seasoned Curly Fries', 'display', 'late_night', 'starch', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.7, true, 'fryer', 5.0),
  ('2026-11-16', 'soups.aged_cheddar_cauliflower_bisque', 'Aged Cheddar & Cauliflower Bisque', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-16', 'soups.spicy_lentil_veggie_soup', 'Spicy Lentil Veggie Soup', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-16', 'grill.grilled_carrots', 'Grilled Carrots', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7071, true, 'flat_top', 6.0),
  ('2026-11-16', 'grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-16', 'grill.halal_french_fries', 'Halal French Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'fryer', 5.0),
  ('2026-11-16', 'grill.hand_cut_sweet_potato_fries', 'Hand Cut Sweet Potato Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'fryer', 5.0),
  ('2026-11-16', 'grill.kelp_burger', 'Kelp Burger', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-16', 'grill.montreal_chicken_thigh', 'Montreal Chicken Thigh', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-16', 'grill.roasted_zucchini', 'Roasted Zucchini', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7071, true, 'flat_top', 6.0),
  ('2026-11-16', 'desserts.apple_cake', 'Apple Cake', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Corn', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-16', 'desserts.mixed_berry_crisp', 'Mixed Berry Crisp', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-16', 'breads.sour_cream_cornbread', 'Sour Cream Cornbread', 'breads', 'dinner', 'bread', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.25, true, NULL, 3.0),
  ('2026-11-16', 'noodle_bowl.birria_ramen_beef', 'Birria Ramen (Beef)', 'noodle_bowl', 'dinner', 'entree', 'bowl', true, ARRAY[]::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.0, true, 'oven', 10.0),
  ('2026-11-16', 'sushi.california_roll', 'California Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian']::text[], ARRAY['Eggs', 'Fish', 'Shellfish', 'Soy', 'Corn', 'Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-16', 'sushi.purple_sweet_potato_tempura_sushi_roll', 'Purple Sweet Potato Tempura Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-16', 'sushi.salmon_cream_cheese_sushi_roll', 'Salmon Cream Cheese Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian']::text[], ARRAY['Milk', 'Fish', 'Corn', 'Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-16', 'sushi.teriyaki_pulmuone_tofu_roll', 'Teriyaki Pulmuone Tofu Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-16', 'sushi.vegetable_sushi_roll', 'Vegetable Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-16', 'display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.6, true, NULL, 10.0),
  ('2026-11-16', 'street_food.baguette', 'Baguette', 'street_food', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Wheat']::text[], 0.9899, true, 'wok', 5.0),
  ('2026-11-16', 'street_food.banh_mi_bo_kho_vietnamese_beef_stew', 'Banh Mi Bo Kho (Vietnamese Beef Stew)', 'street_food', 'dinner', 'entree', 'bowl', true, ARRAY[]::text[], ARRAY['Soy', 'Corn']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-16', 'street_food.braised_pulmuone_tofu', 'Braised Pulmuone Tofu', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-16', 'street_food.jasmine_rice', 'Jasmine Rice', 'street_food', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.9899, true, 'wok', 5.0),
  ('2026-11-16', 'street_food.stir_fry_green_vegetables', 'Stir Fry Green Vegetables', 'street_food', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.0, true, 'wok', 6.0),
  ('2026-11-16', 'street_food.vegetable_spring_rolls', 'Vegetable Spring Rolls', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-16', 'tandoor.eggplant_w_muhammara_walnuts_feta', 'Eggplant w/Muhammara, Walnuts & Feta', 'tandoor', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Tree Nuts']::text[], 0.7071, true, 'tandoor', 10.0),
  ('2026-11-16', 'tandoor.persian_saffron_rice', 'Persian Saffron Rice', 'tandoor', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk']::text[], 0.7, true, 'tandoor', 5.0),
  ('2026-11-16', 'tandoor.tabouleh', 'Tabouleh', 'tandoor', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Wheat']::text[], 0.5, true, 'tandoor', 6.0),
  ('2026-11-16', 'tandoor.za_atar_roasted_lemon_chicken', 'Za''atar Roasted Lemon Chicken', 'tandoor', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Sesame']::text[], 0.7071, true, 'tandoor', 10.0),
  ('2026-11-16', 'mediterranean.gemelli_pasta', 'Gemelli Pasta', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Wheat']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-16', 'mediterranean.gemelli_w_spinach_tomato_cream', 'Gemelli w/Spinach & Tomato Cream', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Gluten', 'Corn', 'Wheat']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-16', 'mediterranean.mussels_w_saffron_cream', 'Mussels w/Saffron & Cream', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian']::text[], ARRAY['Milk', 'Shellfish', 'Corn']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-16', 'mediterranean.sauteed_kale', 'Sauteed Kale', 'mediterranean', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.5, true, 'steamer', 6.0),
  ('2026-11-16', 'mediterranean.whole_grain_penne', 'Whole Grain Penne', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Wheat']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-16', 'seasons.baked_greek_beans_w_tom_olives', 'Baked Greek Beans w/Tom & Olives', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2236, true, 'steamer', 6.0),
  ('2026-11-16', 'seasons.fresh_sauteed_spinach_jain', 'Fresh Sauteed Spinach Jain', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2236, true, 'steamer', 6.0),
  ('2026-11-16', 'seasons.gf_penne_pasta', 'GF Penne Pasta', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-16', 'seasons.oven_roasted_summer_squash', 'Oven Roasted Summer Squash', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2236, true, 'steamer', 6.0),
  ('2026-11-16', 'seasons.roasted_cauliflower_salad', 'Roasted Cauliflower Salad', 'seasons', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2236, true, NULL, 6.0),
  ('2026-11-16', 'seasons.roasted_pepper_tomato_salad', 'Roasted Pepper, Tomato Salad', 'seasons', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2236, true, NULL, 6.0),
  ('2026-11-16', 'seasons.saffron_rice_pilaf_jain', 'Saffron Rice Pilaf Jain', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-16', 'latino.corn_w_red_peppers', 'Corn w/Red Peppers', 'latino', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.7071, true, 'steamer', 6.0),
  ('2026-11-16', 'latino.filling_for_beef_tacos', 'Filling for Beef Tacos', 'latino', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Soy', 'Corn']::text[], 1.4142, true, 'oven', 10.0),
  ('2026-11-16', 'latino.pinto_beans', 'Pinto Beans', 'latino', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.7071, true, 'steamer', 6.0),
  ('2026-11-16', 'latino.plant_based_chorizo_crumbles', 'Plant Based Chorizo Crumbles', 'latino', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 1.4142, true, 'oven', 10.0),
  ('2026-11-16', 'latino.yellow_rice', 'Yellow Rice', 'latino', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 1.4, true, 'steamer', 5.0),
  ('2026-11-16', 'late.grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-16', 'late.grill.halal_french_fries', 'Halal French Fries', 'grill', 'late_night', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4, true, 'fryer', 5.0),
  ('2026-11-16', 'late.grill.kelp_burger', 'Kelp Burger', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-16', 'late.grill.montreal_chicken_thigh', 'Montreal Chicken Thigh', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-16', 'late.grill.roasted_zucchini', 'Roasted Zucchini', 'grill', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 1.0, true, 'flat_top', 6.0),
  ('2026-11-16', 'late.display.baked_waffle_fries', 'Baked Waffle Fries', 'display', 'late_night', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, 'fryer', 10.0),
  ('2026-11-16', 'late.display.celery_sticks', 'Celery Sticks', 'display', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-16', 'late.display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'late_night', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, NULL, 10.0),
  ('2026-11-16', 'late.display.plant_based_buffalo_chic_n', 'Plant Based Buffalo Chic''n', 'display', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-16', 'late.display.spicy_fried_chicken_wings', 'Spicy Fried Chicken Wings', 'display', 'late_night', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.0, true, 'oven', 10.0),
  ('2026-11-17', 'soups.roasted_chicken_noodle_soup_w_herbs', 'Roasted Chicken Noodle Soup w/Herbs', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY[]::text[], ARRAY['Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-17', 'soups.tomato_basil_soup', 'Tomato Basil Soup', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Corn']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-17', 'grill.black_bean_burger', 'Black Bean Burger', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-17', 'grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-17', 'grill.halal_french_fries', 'Halal French Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'fryer', 5.0),
  ('2026-11-17', 'grill.hand_cut_sweet_potato_fries', 'Hand Cut Sweet Potato Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'fryer', 5.0),
  ('2026-11-17', 'grill.herb_roasted_mushrooms', 'Herb Roasted Mushrooms', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7071, true, 'flat_top', 6.0),
  ('2026-11-17', 'grill.jerk_chicken_thigh', 'Jerk Chicken Thigh', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-17', 'grill.oven_roasted_summer_squash', 'Oven Roasted Summer Squash', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7071, true, 'flat_top', 6.0),
  ('2026-11-17', 'desserts.apple_crisp', 'Apple Crisp', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-17', 'desserts.oreo_cheesecake', 'Oreo Cheesecake', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-17', 'breads.oatmeal_honey_bread', 'Oatmeal Honey Bread', 'breads', 'dinner', 'bread', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Gluten', 'Wheat']::text[], 0.25, true, NULL, 3.0),
  ('2026-11-17', 'noodle_bowl.shrimp_tom_yum_soup_chicken_broth', 'Shrimp Tom Yum Soup (Chicken Broth)', 'noodle_bowl', 'dinner', 'entree', 'bowl', true, ARRAY[]::text[], ARRAY['Milk', 'Shellfish', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 2.0, true, 'oven', 10.0),
  ('2026-11-17', 'sushi.california_roll', 'California Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian']::text[], ARRAY['Eggs', 'Fish', 'Shellfish', 'Soy', 'Corn', 'Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-17', 'sushi.crispy_teriyaki_chicken_roll', 'Crispy Teriyaki Chicken Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY[]::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-17', 'sushi.purple_sweet_potato_tempura_sushi_roll', 'Purple Sweet Potato Tempura Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-17', 'sushi.teriyaki_pulmuone_tofu_roll', 'Teriyaki Pulmuone Tofu Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-17', 'sushi.vegetable_sushi_roll', 'Vegetable Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-17', 'display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.6, true, NULL, 10.0),
  ('2026-11-17', 'street_food.chap_che_noodles', 'Chap Che Noodles', 'street_food', 'dinner', 'starch', 'plate', true, ARRAY['pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4, true, 'wok', 5.0),
  ('2026-11-17', 'street_food.korean_chili_mussels', 'Korean Chili Mussels', 'street_food', 'dinner', 'entree', 'bowl', true, ARRAY['pescetarian']::text[], ARRAY['Shellfish', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-17', 'street_food.plant_based_pulmuone_bulgogi_w_peppers_and_onions', 'Plant Based Pulmuone Bulgogi w/Peppers and Onions', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-17', 'street_food.stir_fry_green_vegetables', 'Stir Fry Green Vegetables', 'street_food', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.0, true, 'wok', 6.0),
  ('2026-11-17', 'street_food.vegetable_spring_rolls', 'Vegetable Spring Rolls', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-17', 'tandoor.braised_kale_tomatoes', 'Braised Kale & Tomatoes', 'tandoor', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.2887, true, 'tandoor', 6.0),
  ('2026-11-17', 'tandoor.chicken_yassa', 'Chicken Yassa', 'tandoor', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Corn']::text[], 1.0, true, 'tandoor', 10.0),
  ('2026-11-17', 'tandoor.cumin_basmati_rice', 'Cumin Basmati Rice', 'tandoor', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7, true, 'tandoor', 5.0),
  ('2026-11-17', 'tandoor.tomato_salad_w_parsley_sumac', 'Tomato Salad w/Parsley & Sumac', 'tandoor', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2887, true, NULL, 6.0),
  ('2026-11-17', 'tandoor.tunisian_style_carrot_salad', 'Tunisian Style Carrot Salad', 'tandoor', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2887, true, NULL, 6.0),
  ('2026-11-17', 'mediterranean.arista_pork', 'Arista Pork', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Corn']::text[], 1.0, true, 'oven', 10.0),
  ('2026-11-17', 'mediterranean.balsamic_broccolini', 'Balsamic Broccolini', 'mediterranean', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.5, true, 'steamer', 6.0),
  ('2026-11-17', 'mediterranean.cavatappi_pasta', 'Cavatappi Pasta', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Wheat']::text[], 0.4041, true, 'steamer', 5.0),
  ('2026-11-17', 'mediterranean.fettuccini_pasta', 'Fettuccini Pasta', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Wheat']::text[], 0.4041, true, 'steamer', 5.0),
  ('2026-11-17', 'mediterranean.polenta_florentine', 'Polenta Florentine', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Corn']::text[], 0.4041, true, 'steamer', 5.0),
  ('2026-11-17', 'seasons.gf_penne_pasta', 'GF Penne Pasta', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-17', 'seasons.jasmine_rice', 'Jasmine Rice', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-17', 'seasons.thai_basil_tofu_jain', 'Thai Basil Tofu Jain', 'seasons', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn', 'Sesame']::text[], 1.0, true, 'oven', 10.0),
  ('2026-11-17', 'seasons.thai_stir_fried_green_beans_jain', 'Thai Stir Fried Green Beans Jain', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 0.5, true, 'steamer', 6.0),
  ('2026-11-17', 'latino.chicken_tinga', 'Chicken Tinga', 'latino', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Corn']::text[], 2.0, true, 'oven', 10.0),
  ('2026-11-17', 'latino.cilantro_lime_zucchini', 'Cilantro Lime Zucchini', 'latino', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.5, true, 'steamer', 6.0),
  ('2026-11-17', 'latino.esquites_mexican_corn_salad', 'Esquites (Mexican Corn Salad)', 'latino', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Soy', 'Corn']::text[], 0.5, true, NULL, 6.0),
  ('2026-11-17', 'latino.mexican_tomato_salad', 'Mexican Tomato Salad', 'latino', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk']::text[], 0.5, true, NULL, 6.0),
  ('2026-11-17', 'latino.pinto_beans', 'Pinto Beans', 'latino', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.5, true, 'steamer', 6.0),
  ('2026-11-17', 'latino.yellow_rice', 'Yellow Rice', 'latino', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 1.4, true, 'steamer', 5.0),
  ('2026-11-17', 'late.grill.black_bean_burger', 'Black Bean Burger', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-17', 'late.grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-17', 'late.grill.halal_french_fries', 'Halal French Fries', 'grill', 'late_night', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4, true, 'fryer', 5.0),
  ('2026-11-17', 'late.grill.jerk_chicken_thigh', 'Jerk Chicken Thigh', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-17', 'late.grill.oven_roasted_summer_squash', 'Oven Roasted Summer Squash', 'grill', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 1.0, true, 'flat_top', 6.0),
  ('2026-11-17', 'late.display.jasmine_rice', 'Jasmine Rice', 'display', 'late_night', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7, true, 'steamer', 5.0),
  ('2026-11-17', 'late.display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'late_night', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.6, true, NULL, 10.0),
  ('2026-11-17', 'late.display.steamed_broccoli', 'Steamed Broccoli', 'display', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.5, true, 'steamer', 6.0),
  ('2026-11-17', 'late.display.vegetable_spring_rolls', 'Vegetable Spring Rolls', 'display', 'late_night', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.5774, true, 'oven', 10.0),
  ('2026-11-17', 'late.display.zesty_orange_chicken', 'Zesty Orange Chicken', 'display', 'late_night', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.5774, true, 'oven', 10.0),
  ('2026-11-17', 'late.display.zesty_orange_tofu', 'Zesty Orange Tofu', 'display', 'late_night', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.5774, true, 'oven', 10.0),
  ('2026-11-18', 'soups.chana_masala_cauliflower_soup', 'Chana Masala Cauliflower Soup', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-18', 'soups.santa_fe_tortilla_soup', 'Santa Fe Tortilla Soup', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-18', 'grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.4142, true, 'flat_top', 10.0),
  ('2026-11-18', 'grill.halal_french_fries', 'Halal French Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.8083, true, 'fryer', 5.0),
  ('2026-11-18', 'grill.hand_cut_sweet_potato_fries', 'Hand Cut Sweet Potato Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.8083, true, 'fryer', 5.0),
  ('2026-11-18', 'grill.lemon_pepper_chicken_thigh', 'Lemon Pepper Chicken Thigh', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Corn']::text[], 1.4142, true, 'flat_top', 10.0),
  ('2026-11-18', 'grill.local_roasted_sweet_potatoes', 'Local Roasted Sweet Potatoes', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.8083, true, 'flat_top', 5.0),
  ('2026-11-18', 'grill.roasted_cauliflower', 'Roasted Cauliflower', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 1.0, true, 'flat_top', 6.0),
  ('2026-11-18', 'desserts.pecan_pie', 'Pecan Pie', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Tree Nuts', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-18', 'desserts.pumpkin_pie', 'Pumpkin Pie', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-18', 'breads.southern_cornbread', 'Southern Cornbread', 'breads', 'dinner', 'bread', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.25, true, NULL, 3.0),
  ('2026-11-18', 'noodle_bowl.taiwan_pork_pickled_vegetables', 'Taiwan Pork & Pickled Vegetables', 'noodle_bowl', 'dinner', 'entree', 'bowl', false, ARRAY[]::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 2.0, true, 'oven', 10.0),
  ('2026-11-18', 'sushi.california_roll', 'California Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian']::text[], ARRAY['Eggs', 'Fish', 'Shellfish', 'Soy', 'Corn', 'Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-18', 'sushi.purple_sweet_potato_tempura_sushi_roll', 'Purple Sweet Potato Tempura Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-18', 'sushi.shrimp_sushi_roll', 'Shrimp Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian']::text[], ARRAY['Shellfish', 'Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-18', 'sushi.teriyaki_pulmuone_tofu_roll', 'Teriyaki Pulmuone Tofu Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-18', 'sushi.vegetable_sushi_roll', 'Vegetable Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-18', 'display.brown_sugar_glazed_sweet_potatoes', 'Brown Sugar Glazed Sweet Potatoes', 'display', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk']::text[], 0.4041, true, 'steamer', 5.0),
  ('2026-11-18', 'carving.carved_local_roast_turkey', 'Carved Local Roast Turkey', 'carving', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY[]::text[], 1.0, true, 'oven', 10.0),
  ('2026-11-18', 'display.herb_dressing', 'Herb Dressing', 'display', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4041, true, 'steamer', 5.0),
  ('2026-11-18', 'display.local_seasoned_mashed_potatoes', 'Local Seasoned Mashed Potatoes', 'display', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk']::text[], 0.4041, true, 'steamer', 5.0),
  ('2026-11-18', 'display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.6, true, NULL, 10.0),
  ('2026-11-18', 'display.roasted_brussels_sprouts_squash_cranberries', 'Roasted Brussels Sprouts, Squash & Cranberries', 'display', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-18', 'display.sauteed_kale', 'Sauteed Kale', 'display', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-18', 'street_food.mei_fun_noodles', 'Mei Fun Noodles', 'street_food', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4, true, 'wok', 5.0),
  ('2026-11-18', 'street_food.spicy_tofu_vegetable_stir_fry', 'Spicy Tofu & Vegetable Stir Fry', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-18', 'street_food.stir_fry_green_vegetables', 'Stir Fry Green Vegetables', 'street_food', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.0, true, 'wok', 6.0),
  ('2026-11-18', 'street_food.thai_steamed_fish', 'Thai Steamed Fish', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian']::text[], ARRAY['Fish', 'Corn']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-18', 'street_food.vegetable_spring_rolls', 'Vegetable Spring Rolls', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.1547, true, 'wok', 10.0),
  ('2026-11-18', 'tandoor.charmoula_zucchini_summer_squash', 'Charmoula Zucchini & Summer Squash', 'tandoor', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2887, true, 'tandoor', 6.0),
  ('2026-11-18', 'tandoor.chickpea_salad_w_teardrop_peppers_artichoke_hearts', 'Chickpea Salad w/Teardrop Peppers & Artichoke Hearts', 'tandoor', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk']::text[], 0.2887, true, NULL, 6.0),
  ('2026-11-18', 'tandoor.cucumber_feta_dill_mint_salad', 'Cucumber, Feta, Dill & Mint Salad', 'tandoor', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk']::text[], 0.2887, true, NULL, 6.0),
  ('2026-11-18', 'tandoor.lavash_bread', 'Lavash Bread', 'tandoor', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Wheat']::text[], 0.495, true, NULL, 5.0),
  ('2026-11-18', 'tandoor.moroccan_beef_kefta_in_tomato_onion_sauce', 'Moroccan Beef Kefta in Tomato Onion Sauce', 'tandoor', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.0, true, 'tandoor', 10.0),
  ('2026-11-18', 'tandoor.moroccan_rice_w_almonds', 'Moroccan Rice w/Almonds', 'tandoor', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Tree Nuts', 'Corn']::text[], 0.495, true, 'tandoor', 5.0),
  ('2026-11-18', 'mediterranean.herb_rstd_honeynut_squash', 'Herb Rstd Honeynut Squash', 'mediterranean', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.5, true, 'steamer', 6.0),
  ('2026-11-18', 'mediterranean.penne_pasta', 'Penne Pasta', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Wheat']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-18', 'mediterranean.portuguese_cataplana_sausage_seafood', 'Portuguese Cataplana (Sausage & Seafood)', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Milk', 'Fish', 'Shellfish', 'Corn']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-18', 'mediterranean.spaghetti', 'Spaghetti', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Wheat']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-18', 'mediterranean.spaghetti_alla_nerano', 'Spaghetti alla Nerano', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-18', 'seasons.corn', 'Corn', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-18', 'seasons.edamame_mushroom', 'Edamame & Mushroom', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-18', 'seasons.gf_penne_pasta', 'GF Penne Pasta', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-18', 'seasons.plain_sushi_rice', 'Plain Sushi Rice', 'seasons', 'dinner', 'starch', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-18', 'seasons.teriyaki_tofu', 'Teriyaki Tofu', 'seasons', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn', 'Sesame']::text[], 1.0, true, 'oven', 10.0),
  ('2026-11-18', 'late.grill.black_bean_burger', 'Black Bean Burger', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-18', 'late.grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-18', 'late.grill.halal_french_fries', 'Halal French Fries', 'grill', 'late_night', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4, true, 'fryer', 5.0),
  ('2026-11-18', 'late.grill.lemon_pepper_chicken_thigh', 'Lemon Pepper Chicken Thigh', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-18', 'late.grill.oven_roasted_summer_squash', 'Oven Roasted Summer Squash', 'grill', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 1.0, true, 'flat_top', 6.0),
  ('2026-11-18', 'late.display.creamy_southern_slaw', 'Creamy Southern Slaw', 'display', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Eggs', 'Soy', 'Corn']::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-18', 'late.display.local_seasoned_mashed_potatoes', 'Local Seasoned Mashed Potatoes', 'display', 'late_night', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk']::text[], 0.7, true, 'steamer', 5.0),
  ('2026-11-18', 'late.display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'late_night', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.6, true, NULL, 10.0),
  ('2026-11-18', 'late.display.nashville_hot_chic_n', 'Nashville Hot Chic''n', 'display', 'late_night', 'side', 'plate', true, ARRAY['halal']::text[], ARRAY['Milk', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-18', 'late.display.nashville_hot_chicken_leg', 'Nashville Hot Chicken Leg', 'display', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Milk', 'Corn']::text[], 1.0, true, 'oven', 10.0),
  ('2026-11-19', 'soups.green_chile_posole', 'Green Chile Posole', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-19', 'soups.roasted_red_pepper_smoked_gouda_bisque', 'Roasted Red Pepper & Smoked Gouda Bisque', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-19', 'grill.golden_bbq_chicken_thigh', 'Golden BBQ Chicken Thigh', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-19', 'grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-19', 'grill.halal_french_fries', 'Halal French Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'fryer', 5.0),
  ('2026-11-19', 'grill.hand_cut_sweet_potato_fries', 'Hand Cut Sweet Potato Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'fryer', 5.0),
  ('2026-11-19', 'grill.kelp_burger', 'Kelp Burger', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-19', 'grill.roasted_butternut_squash', 'Roasted Butternut Squash', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7071, true, 'flat_top', 6.0),
  ('2026-11-19', 'grill.steamed_broccoli', 'Steamed Broccoli', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7071, true, 'flat_top', 6.0),
  ('2026-11-19', 'desserts.blueberry_buckle', 'Blueberry Buckle', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Corn', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-19', 'desserts.chocolate_malva_pudding', 'Chocolate Malva Pudding', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-19', 'breads.local_whole_wheat_bread', 'Local Whole Wheat Bread', 'breads', 'dinner', 'bread', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Wheat']::text[], 0.25, true, NULL, 3.0),
  ('2026-11-19', 'noodle_bowl.kumamoto_style_ramen_pork_chicken', 'Kumamoto Style Ramen (Pork & Chicken)', 'noodle_bowl', 'dinner', 'entree', 'bowl', true, ARRAY[]::text[], ARRAY['Fish', 'Shellfish', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 1.0, true, 'oven', 10.0),
  ('2026-11-19', 'sushi.california_roll', 'California Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian']::text[], ARRAY['Eggs', 'Fish', 'Shellfish', 'Soy', 'Corn', 'Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-19', 'sushi.purple_sweet_potato_tempura_sushi_roll', 'Purple Sweet Potato Tempura Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-19', 'sushi.spicy_tuna_roll', 'Spicy Tuna Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian']::text[], ARRAY['Eggs', 'Fish', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-19', 'sushi.teriyaki_pulmuone_tofu_roll', 'Teriyaki Pulmuone Tofu Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-19', 'sushi.vegetable_sushi_roll', 'Vegetable Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-19', 'display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.6, true, NULL, 10.0),
  ('2026-11-19', 'street_food.kimchi_fried_rice', 'Kimchi Fried Rice', 'street_food', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian']::text[], ARRAY['Eggs', 'Fish', 'Shellfish', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4, true, 'wok', 5.0),
  ('2026-11-19', 'street_food.korean_bbq_pulmuone_tofu', 'Korean BBQ Pulmuone Tofu', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4142, true, 'wok', 10.0),
  ('2026-11-19', 'street_food.stir_fry_green_vegetables', 'Stir Fry Green Vegetables', 'street_food', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.0, true, 'wok', 6.0),
  ('2026-11-19', 'street_food.vegetable_spring_rolls', 'Vegetable Spring Rolls', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4142, true, 'wok', 10.0),
  ('2026-11-19', 'tandoor.babaganoush', 'Babaganoush', 'tandoor', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Sesame']::text[], 0.25, true, 'tandoor', 6.0),
  ('2026-11-19', 'tandoor.chicken_shawarma', 'Chicken Shawarma', 'tandoor', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.0, true, 'tandoor', 10.0),
  ('2026-11-19', 'tandoor.lavash_bread', 'Lavash Bread', 'tandoor', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Wheat']::text[], 0.495, true, NULL, 5.0),
  ('2026-11-19', 'tandoor.roasted_onions_peppers', 'Roasted Onions & Peppers', 'tandoor', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.25, true, 'tandoor', 6.0),
  ('2026-11-19', 'tandoor.sweet_onion_salad', 'Sweet Onion Salad', 'tandoor', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.25, true, NULL, 6.0),
  ('2026-11-19', 'tandoor.tomato_slices', 'Tomato Slices', 'tandoor', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.25, true, 'tandoor', 6.0),
  ('2026-11-19', 'tandoor.turmeric_basmati_rice', 'Turmeric Basmati Rice', 'tandoor', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.495, true, 'tandoor', 5.0),
  ('2026-11-19', 'mediterranean.fettuccini_pasta', 'Fettuccini Pasta', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Wheat']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-19', 'mediterranean.garlic_parmesan_chicken_leg', 'Garlic Parmesan Chicken Leg', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Milk', 'Corn']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-19', 'mediterranean.rigatoni_pasta', 'Rigatoni Pasta', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Gluten', 'Wheat']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-19', 'mediterranean.roasted_butternut_squash_w_spices', 'Roasted Butternut Squash w/Spices', 'mediterranean', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.5, true, 'steamer', 6.0),
  ('2026-11-19', 'mediterranean.seafood_gnocchi_w_campbells_lobster_sauce', 'Seafood Gnocchi w/Campbells Lobster Sauce', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian']::text[], ARRAY['Milk', 'Eggs', 'Fish', 'Shellfish', 'Gluten', 'Corn', 'Wheat']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-19', 'seasons.corn', 'Corn', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-19', 'seasons.eggplant_donburi', 'Eggplant Donburi', 'seasons', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn', 'Sesame']::text[], 1.0, true, 'oven', 10.0),
  ('2026-11-19', 'seasons.gf_penne_pasta', 'GF Penne Pasta', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-19', 'seasons.plain_sushi_rice', 'Plain Sushi Rice', 'seasons', 'dinner', 'starch', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-19', 'seasons.sauteed_spinach_w_sesame_seeds', 'Sauteed Spinach w/Sesame Seeds', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn', 'Sesame']::text[], 0.3536, true, 'steamer', 6.0),
  ('2026-11-19', 'latino.arroz_blanco', 'Arroz Blanco', 'latino', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 1.4, true, 'steamer', 5.0),
  ('2026-11-19', 'latino.corn_w_red_peppers', 'Corn w/Red Peppers', 'latino', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.5774, true, 'steamer', 6.0),
  ('2026-11-19', 'latino.esquites_mexican_corn_salad', 'Esquites (Mexican Corn Salad)', 'latino', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Soy', 'Corn']::text[], 0.5774, true, NULL, 6.0),
  ('2026-11-19', 'latino.mole_amarillo_chicken', 'Mole Amarillo Chicken', 'latino', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Corn']::text[], 2.0, true, 'oven', 10.0),
  ('2026-11-19', 'latino.pinto_beans', 'Pinto Beans', 'latino', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.5774, true, 'steamer', 6.0),
  ('2026-11-19', 'late.grill.cajun_chicken_thigh', 'Cajun Chicken Thigh', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-19', 'late.grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-19', 'late.grill.halal_french_fries', 'Halal French Fries', 'grill', 'late_night', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 1.4, true, 'fryer', 5.0),
  ('2026-11-19', 'late.grill.kelp_burger', 'Kelp Burger', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-19', 'late.grill.roasted_butternut_squash', 'Roasted Butternut Squash', 'grill', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 1.0, true, 'flat_top', 6.0),
  ('2026-11-19', 'late.display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'late_night', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.6, true, NULL, 10.0),
  ('2026-11-19', 'late.display.pinto_beans', 'Pinto Beans', 'display', 'late_night', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.5, true, 'steamer', 6.0),
  ('2026-11-19', 'late.display.yellow_rice', 'Yellow Rice', 'display', 'late_night', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.7, true, 'steamer', 5.0),
  ('2026-11-20', 'soups.homemade_clam_chowder', 'Homemade Clam Chowder', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian']::text[], ARRAY['Milk', 'Shellfish', 'Gluten', 'Corn', 'Wheat']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-20', 'soups.spicy_lentil_veggie_soup', 'Spicy Lentil Veggie Soup', 'soups', 'dinner', 'soup', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.3536, true, 'kettle', 10.0),
  ('2026-11-20', 'grill.black_bean_burger', 'Black Bean Burger', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-20', 'grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-20', 'grill.halal_french_fries', 'Halal French Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'fryer', 5.0),
  ('2026-11-20', 'grill.hand_cut_sweet_potato_fries', 'Hand Cut Sweet Potato Fries', 'grill', 'dinner', 'starch', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'fryer', 5.0),
  ('2026-11-20', 'grill.herb_roasted_mushrooms', 'Herb Roasted Mushrooms', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7071, true, 'flat_top', 6.0),
  ('2026-11-20', 'grill.roasted_cauliflower', 'Roasted Cauliflower', 'grill', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.7071, true, 'flat_top', 6.0),
  ('2026-11-20', 'grill.savory_baked_chicken_thigh', 'Savory Baked Chicken Thigh', 'grill', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-20', 'desserts.bread_pudding', 'Bread Pudding', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Corn', 'Wheat']::text[], 0.4243, true, NULL, 10.0),
  ('2026-11-20', 'desserts.walnut_brownie', 'Walnut Brownie', 'desserts', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Tree Nuts', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.4243, true, 'oven', 10.0),
  ('2026-11-20', 'breads.sourdough_bread', 'Sourdough Bread', 'breads', 'dinner', 'bread', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.25, true, NULL, 3.0),
  ('2026-11-20', 'noodle_bowl.hot_pot_chicken_beef_pork_fish_shellfish', 'Hot Pot (Chicken, Beef, Pork, Fish, Shellfish)', 'noodle_bowl', 'dinner', 'entree', 'bowl', true, ARRAY[]::text[], ARRAY['Milk', 'Eggs', 'Fish', 'Shellfish', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 2.0, true, 'oven', 10.0),
  ('2026-11-20', 'sushi.california_roll', 'California Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian']::text[], ARRAY['Eggs', 'Fish', 'Shellfish', 'Soy', 'Corn', 'Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-20', 'sushi.crispy_shrimp_sushi_roll', 'Crispy Shrimp Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian']::text[], ARRAY['Milk', 'Eggs', 'Shellfish', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-20', 'sushi.purple_sweet_potato_tempura_sushi_roll', 'Purple Sweet Potato Tempura Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-20', 'sushi.teriyaki_pulmuone_tofu_roll', 'Teriyaki Pulmuone Tofu Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-20', 'sushi.vegetable_sushi_roll', 'Vegetable Sushi Roll', 'sushi', 'dinner', 'entree', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Sesame']::text[], 0.4472, false, NULL, NULL),
  ('2026-11-20', 'display.glutinous_rice_cake', 'Glutinous Rice Cake', 'display', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Sesame']::text[], 0.3464, true, 'oven', 10.0),
  ('2026-11-20', 'display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.3464, true, NULL, 10.0),
  ('2026-11-20', 'display.mango_sago', 'Mango Sago', 'display', 'dinner', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.3464, true, 'oven', 10.0),
  ('2026-11-20', 'street_food.cantonese_claypot_rice_pork', 'Cantonese Claypot Rice (Pork)', 'street_food', 'dinner', 'starch', 'plate', true, ARRAY[]::text[], ARRAY['Shellfish', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'wok', 5.0),
  ('2026-11-20', 'street_food.chinese_style_braised_prawns', 'Chinese Style Braised Prawns', 'street_food', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian']::text[], ARRAY['Milk', 'Shellfish', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 1.4142, true, 'wok', 10.0),
  ('2026-11-20', 'street_food.salt_pepper_stir_fry_potatoes', 'Salt & Pepper Stir Fry Potatoes', 'street_food', 'dinner', 'starch', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'wok', 5.0),
  ('2026-11-20', 'street_food.sour_beef_soup', 'Sour Beef Soup', 'street_food', 'dinner', 'entree', 'bowl', true, ARRAY[]::text[], ARRAY['Eggs', 'Soy', 'Corn']::text[], 1.4142, true, 'wok', 10.0),
  ('2026-11-20', 'street_food.stir_fried_cabbage_w_chilies', 'Stir Fried Cabbage w/Chilies', 'street_food', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Corn', 'Wheat']::text[], 1.0, true, 'wok', 6.0),
  ('2026-11-20', 'tandoor.chicken_tikka_masala_rasoi', 'Chicken Tikka Masala Rasoi', 'tandoor', 'dinner', 'entree', 'bowl', true, ARRAY['halal']::text[], ARRAY['Milk', 'Corn']::text[], 0.5774, true, 'tandoor', 10.0),
  ('2026-11-20', 'tandoor.chole_chickpea_curry', 'Chole Chickpea Curry', 'tandoor', 'dinner', 'entree', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.5774, true, 'tandoor', 10.0),
  ('2026-11-20', 'tandoor.cumin_basmati_rice', 'Cumin Basmati Rice', 'tandoor', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.495, true, 'tandoor', 5.0),
  ('2026-11-20', 'tandoor.naan', 'Naan', 'tandoor', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Gluten', 'Corn', 'Sesame', 'Wheat']::text[], 0.495, true, 'tandoor', 5.0),
  ('2026-11-20', 'tandoor.stir_fried_cabbage_w_curry_leaves_tamarind_chutney', 'Stir Fried Cabbage w/Curry Leaves & Tamarind Chutney', 'tandoor', 'dinner', 'side', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.3536, true, 'tandoor', 6.0),
  ('2026-11-20', 'tandoor.street_corn_salad', 'Street Corn Salad', 'tandoor', 'dinner', 'side', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn', 'Sesame']::text[], 0.3536, true, NULL, 6.0),
  ('2026-11-20', 'tandoor.vegetable_curry_samosa', 'Vegetable Curry Samosa', 'tandoor', 'dinner', 'entree', 'bowl', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.5774, true, 'tandoor', 10.0),
  ('2026-11-20', 'mediterranean.beef_bolognese_sauce_w_pancetta_pork', 'Beef Bolognese Sauce w/Pancetta (Pork)', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Milk', 'Corn']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-20', 'mediterranean.fish_w_lemon_herb_cream', 'Fish w/Lemon Herb Cream', 'mediterranean', 'dinner', 'entree', 'plate', true, ARRAY['pescetarian']::text[], ARRAY['Milk', 'Fish', 'Shellfish', 'Corn']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-20', 'mediterranean.orecchiette_pasta', 'Orecchiette Pasta', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Wheat']::text[], 0.35, true, 'steamer', 5.0),
  ('2026-11-20', 'mediterranean.orecchiette_w_sausage_broccolini_tomatoes', 'Orecchiette w/Sausage, Broccolini & Tomatoes', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY[]::text[], ARRAY['Milk', 'Gluten', 'Soy', 'Wheat']::text[], 0.35, true, 'steamer', 5.0),
  ('2026-11-20', 'mediterranean.sweet_potato_w_honey_feta', 'Sweet Potato w/Honey & Feta', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk']::text[], 0.35, true, 'steamer', 5.0),
  ('2026-11-20', 'mediterranean.whole_grain_penne', 'Whole Grain Penne', 'mediterranean', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Gluten', 'Soy', 'Wheat']::text[], 0.35, true, 'steamer', 5.0),
  ('2026-11-20', 'seasons.chickpea_kelp_meatballs', 'Chickpea & Kelp Meatballs', 'seasons', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-20', 'seasons.eggplant_parmesan', 'Eggplant Parmesan', 'seasons', 'dinner', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Corn']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-20', 'seasons.gf_garlic_bread', 'GF Garlic Bread', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Corn']::text[], 0.495, true, NULL, 5.0),
  ('2026-11-20', 'seasons.gf_penne_pasta', 'GF Penne Pasta', 'seasons', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.495, true, 'steamer', 5.0),
  ('2026-11-20', 'seasons.herb_roasted_tomatoes', 'Herb Roasted Tomatoes', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2887, true, 'steamer', 6.0),
  ('2026-11-20', 'seasons.roasted_zucchini', 'Roasted Zucchini', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2887, true, 'steamer', 6.0),
  ('2026-11-20', 'seasons.sauteed_swiss_chard_jain', 'Sauteed Swiss Chard Jain', 'seasons', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.2887, true, 'steamer', 6.0),
  ('2026-11-20', 'latino.chicken_taco_filling', 'Chicken Taco Filling', 'latino', 'dinner', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY['Corn']::text[], 1.4142, true, 'oven', 10.0),
  ('2026-11-20', 'latino.corn_w_red_peppers', 'Corn w/Red Peppers', 'latino', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.7071, true, 'steamer', 6.0),
  ('2026-11-20', 'latino.filling_for_beef_tacos', 'Filling for Beef Tacos', 'latino', 'dinner', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Soy', 'Corn']::text[], 1.4142, true, 'oven', 10.0),
  ('2026-11-20', 'latino.pinto_beans', 'Pinto Beans', 'latino', 'dinner', 'side', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.7071, true, 'steamer', 6.0),
  ('2026-11-20', 'latino.quinoa_mango_salad', 'Quinoa & Mango Salad', 'latino', 'dinner', 'starch', 'bowl', false, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.9899, true, NULL, 5.0),
  ('2026-11-20', 'latino.yellow_rice', 'Yellow Rice', 'latino', 'dinner', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Corn']::text[], 0.9899, true, 'steamer', 5.0),
  ('2026-11-20', 'late.grill.black_bean_burger', 'Black Bean Burger', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Soy', 'Corn']::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-20', 'late.grill.halal_burger_patty', 'Halal Burger Patty', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-20', 'late.grill.halal_french_fries', 'Halal French Fries', 'grill', 'late_night', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.9899, true, 'fryer', 5.0),
  ('2026-11-20', 'late.grill.roasted_diced_sweet_potato', 'Roasted Diced Sweet Potato', 'grill', 'late_night', 'starch', 'plate', true, ARRAY['halal', 'pescetarian', 'vegan', 'vegetarian']::text[], ARRAY[]::text[], 0.9899, true, 'flat_top', 5.0),
  ('2026-11-20', 'late.grill.savory_baked_chicken_thigh', 'Savory Baked Chicken Thigh', 'grill', 'late_night', 'entree', 'plate', true, ARRAY['halal']::text[], ARRAY[]::text[], 1.1547, true, 'flat_top', 10.0),
  ('2026-11-20', 'late.display.ancho_chile_pork_torta', 'Ancho Chile Pork Torta', 'display', 'late_night', 'entree', 'plate', true, ARRAY[]::text[], ARRAY['Milk', 'Gluten', 'Wheat']::text[], 0.7071, true, 'oven', 10.0),
  ('2026-11-20', 'late.display.fried_yuca_fries', 'Fried Yuca Fries', 'display', 'late_night', 'starch', 'plate', true, ARRAY['pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Sesame', 'Wheat']::text[], 0.7, true, 'fryer', 5.0),
  ('2026-11-20', 'late.display.make_your_own_waffles', 'Make Your Own Waffles', 'display', 'late_night', 'dessert', 'plate', false, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Eggs', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.6, true, NULL, 10.0),
  ('2026-11-20', 'late.display.plant_based_chorizo_torta', 'Plant Based Chorizo Torta', 'display', 'late_night', 'entree', 'plate', true, ARRAY['halal', 'pescetarian', 'vegetarian']::text[], ARRAY['Milk', 'Gluten', 'Soy', 'Corn', 'Wheat']::text[], 0.7071, true, 'oven', 10.0)
ON CONFLICT (menu_date, dish_id) DO UPDATE SET name = EXCLUDED.name, station = EXCLUDED.station, period = EXCLUDED.period, role = EXCLUDED.role, vessel = EXCLUDED.vessel, hot = EXCLUDED.hot, diet_tags = EXCLUDED.diet_tags, allergens = EXCLUDED.allergens, popularity = EXCLUDED.popularity, batch_cooked = EXCLUDED.batch_cooked, equipment = EXCLUDED.equipment, prep_min = EXCLUDED.prep_min;
INSERT INTO dcsim.date_context (menu_date, weekday, weekend, dinner_crowd_factor, late_night_crowd_factor, events, expected_dinner_students, expected_late_night_students) VALUES
  ('2026-11-13', 'Friday', false, 0.9, 1.0, ARRAY[]::text[], 2700, 1000),
  ('2026-11-14', 'Saturday', true, 0.85, 1.0, ARRAY[]::text[], 2550, 1000),
  ('2026-11-15', 'Sunday', true, 0.95, 1.0, ARRAY[]::text[], 2850, 1000),
  ('2026-11-16', 'Monday', false, 1.0, 1.0, ARRAY[]::text[], 3000, 1000),
  ('2026-11-17', 'Tuesday', false, 1.0, 1.0, ARRAY[]::text[], 3000, 1000),
  ('2026-11-18', 'Wednesday', false, 1.15, 1.0, ARRAY['Friendsgiving dinner', 'UMass vs Akron home football']::text[], 3450, 1000),
  ('2026-11-19', 'Thursday', false, 1.0, 1.0, ARRAY[]::text[], 3000, 1000),
  ('2026-11-20', 'Friday', false, 0.99, 1.0, ARRAY['TCSA dinner']::text[], 2970, 1000)
ON CONFLICT (menu_date) DO UPDATE SET weekday = EXCLUDED.weekday, weekend = EXCLUDED.weekend, dinner_crowd_factor = EXCLUDED.dinner_crowd_factor, late_night_crowd_factor = EXCLUDED.late_night_crowd_factor, events = EXCLUDED.events, expected_dinner_students = EXCLUDED.expected_dinner_students, expected_late_night_students = EXCLUDED.expected_late_night_students;
INSERT INTO dcsim.personas (persona, label, diet, share, seconds_prob, dwell_median_min, description) VALUES
  ('regular', 'Regular diner', 'none', 0.415, 0.5, 25.0, ''),
  ('athlete', 'Athlete, high protein', 'none', 0.1, 0.8, 30.0, ''),
  ('rushed', 'Between classes, in a hurry', 'none', 0.16, 0.15, 12.0, ''),
  ('social', 'Eats with a big group', 'none', 0.14, 0.7, 45.0, ''),
  ('pescetarian', 'Pescetarian', 'pescetarian', 0.02, 0.5, 25.0, 'Eats vegetarian dishes and fish or seafood, but no meat.'),
  ('vegetarian', 'Vegetarian', 'vegetarian', 0.083, 0.45, 25.0, ''),
  ('vegan', 'Vegan', 'vegan', 0.012, 0.35, 25.0, ''),
  ('halal', 'Halal', 'halal', 0.07, 0.5, 25.0, '')
ON CONFLICT (persona) DO UPDATE SET label = EXCLUDED.label, diet = EXCLUDED.diet, share = EXCLUDED.share, seconds_prob = EXCLUDED.seconds_prob, dwell_median_min = EXCLUDED.dwell_median_min, description = EXCLUDED.description;
INSERT INTO dcsim.allergies (allergy, share, menu_labels) VALUES
  ('tree_nuts', 0.0273, ARRAY['Tree Nuts']::text[]),
  ('peanuts', 0.023, ARRAY['Peanuts']::text[]),
  ('gluten', 0.0132, ARRAY['Gluten', 'Wheat']::text[]),
  ('shellfish', 0.01, ARRAY['Shellfish']::text[]),
  ('milk', 0.0093, ARRAY['Milk']::text[]),
  ('fish', 0.0043, ARRAY['Fish']::text[]),
  ('egg', 0.0038, ARRAY['Eggs']::text[]),
  ('sesame', 0.003, ARRAY['Sesame']::text[]),
  ('soy', 0.0023, ARRAY['Soy']::text[]),
  ('corn', 0.0004, ARRAY['Corn']::text[])
ON CONFLICT (allergy) DO UPDATE SET share = EXCLUDED.share, menu_labels = EXCLUDED.menu_labels;
INSERT INTO dcsim.kitchen_resources (kind, name, units) VALUES
  ('cooks', 'dinner', 12),
  ('cooks', 'late_night', 5),
  ('equipment', 'oven', 14),
  ('equipment', 'steamer', 8),
  ('equipment', 'kettle', 4),
  ('equipment', 'flat_top', 6),
  ('equipment', 'wok', 5),
  ('equipment', 'fryer', 3),
  ('equipment', 'tandoor', 5)
ON CONFLICT (kind, name) DO UPDATE SET units = EXCLUDED.units;

