"""One SQL script for the Tiger Data console: every dcsim table, the columns added since engine 0.4,
and category (lookup) tables seeded from the current data files.

    uv run python -m dcsim.tiger_sql > sql/tiger_website.sql

Paste the file into the Tiger console's SQL editor (or `psql -f`). It is idempotent: tables are
created if missing, new columns are added with ADD COLUMN IF NOT EXISTS (existing rows keep their
data, new columns start NULL), and category rows are upserted, so it is safe to run again after the
data files change.

What fills what:
- `dcsim tsdb load` / `dcsim calibrate` (tsdb.py) fill nights, pan_windows, waste_log,
  planned_batches, forecasts and calibrations, using the original columns.
- The columns and tables marked "engine 0.4+" below are the engine's newer outputs; tsdb.py does
  not write them yet, so they stay NULL / empty until its loader is extended.
- The category tables are filled by this script.
"""

from __future__ import annotations

import datetime as dt
import json

from .config import ENGINE_VERSION
from .data import available_dates, crowd_factors, default_data_dir, load_baseline
from .tsdb import CAGG, COLUMNSTORE, COLUMNSTORE_AFTER, FORECAST_CHECK, HYPERTABLES, SCORECARD, TABLES

VECTOR_DIM = 7 + 4 + 1 + 64  # weekday one-hot + weather one-hot + event flag + 64 menu buckets (similar nights)


def q(v) -> str:
    """SQL literal."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(round(v, 6)) if isinstance(v, float) else str(v)
    if isinstance(v, (list, tuple)):
        return "ARRAY[" + ", ".join(q(x) for x in v) + "]::text[]" if v else "ARRAY[]::text[]"
    if isinstance(v, dict):
        return q(json.dumps(v, sort_keys=True)) + "::jsonb"
    return "'" + str(v).replace("'", "''") + "'"


def _rows(table: str, cols: list[str], rows: list[tuple], key: list[str]) -> str:
    if not rows:
        return ""
    upd = [c for c in cols if c not in key]
    values = ",\n  ".join("(" + ", ".join(q(v) for v in r) + ")" for r in rows)
    conflict = (f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in upd)
                if upd else f"ON CONFLICT ({', '.join(key)}) DO NOTHING")
    return f"INSERT INTO dcsim.{table} ({', '.join(cols)}) VALUES\n  {values}\n{conflict};\n"


def _clock(m: int) -> str:
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


# ---------------------------------------------------------------- 1. categories (lookup tables)

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


def category_rows() -> str:
    dates = available_dates()
    base = load_baseline(dates[0])
    out = []
    cats = [
        ("source", "observed", "Recorded (synthetic history until real exports)", 1),
        ("source", "simulated", "Simulated by the engine", 2),
        ("waste_reason", "expired", "Expired on the line (past hold time)", 1),
        ("waste_reason", "close", "Left in pans at close", 2),
        ("waste_reason", "plate", "Plate waste (students' plates)", 3),
        ("weather", "clear", "Clear", 1), ("weather", "rain", "Rain", 2), ("weather", "cold", "Cold", 3),
        ("weather", "snow", "Snow", 4), ("weather", "cloudy", "Cloudy", 5), ("weather", "windy", "Windy", 6),
        ("weather", "other", "Other", 7),
        ("role", "entree", "Entree", 1), ("role", "starch", "Starch", 2), ("role", "side", "Side", 3),
        ("role", "soup", "Soup", 4), ("role", "dessert", "Dessert", 5), ("role", "bread", "Bread", 6),
        ("diet", "none", "No restriction", 1), ("diet", "vegetarian", "Vegetarian", 2), ("diet", "vegan", "Vegan", 3),
        ("diet", "halal", "Halal", 4), ("diet", "pescetarian", "Pescetarian", 5),
        ("vessel", "plate", "Plate", 1), ("vessel", "bowl", "Bowl", 2), ("vessel", "cup", "Cup", 3),
        ("method", "naive", "Naive forecast (plan as written)", 1),
        ("method", "calibrated", "Calibrated forecast (fitted crowd and production error)", 2),
        ("split", "train", "Calibration training nights", 1), ("split", "test", "Held-out nights", 2),
        ("series_kind", "inside", "Students inside the building", 1),
        ("series_kind", "queue", "Students in line at a station", 2),
        ("series_kind", "pan", "Portions on hand for a dish (line + hot-hold)", 3),
        ("resource_kind", "cooks", "Cooks on the line, per service period", 1),
        ("resource_kind", "equipment", "Equipment units (one batch each)", 2),
    ]
    out.append(_rows("cat_values", ["category", "code", "label", "sort"], cats, ["category", "code"]))

    periods = [("dinner", "Dinner", _clock(base.open_min), _clock(base.close_min),
                round(base.crowd.expected_students), "Lines formed at 9 pm are still served (last call)")]
    if base.late_close_min is not None:
        periods.append(("late_night", "Late night", _clock(base.close_min), _clock(base.late_close_min % (24 * 60)),
                        round(base.late_crowd.expected_students), "Serving stops at midnight; seated students may stay"))
    out.append(_rows("periods", ["period", "label", "opens", "closes", "expected_students", "note"], periods, ["period"]))

    st_json = json.loads((default_data_dir() / "stations.json").read_text())["stations"]
    kr = base.kitchen_resources
    st_rows = [(s["id"], s.get("display_name") or s["names"][0], s["servers"], s.get("self_serve", False),
                s.get("to_order", False), s["service_median_min"], s["pos"][0], s["pos"][1],
                kr.station_equipment.get(s["id"]) if kr else None, s.get("_note")) for s in st_json]
    out.append(_rows("stations", ["station", "name", "servers", "self_serve", "to_order", "service_median_min",
                                  "pos_x_m", "pos_y_m", "equipment", "note"], st_rows, ["station"]))

    dish_rows, ctx_rows = [], []
    cal = {d["date"]: d for d in json.loads((default_data_dir() / "calendar.json").read_text())["dates"]}
    for date in dates:
        cfg = load_baseline(date)
        for d in cfg.menu:
            batch = d.id in cfg.kitchen
            eq = cfg.kitchen_resources.equipment_for(d) if cfg.kitchen_resources and batch else None
            prep = cfg.kitchen_resources.prep_for(d, cfg.kitchen[d.id].cook_min) if cfg.kitchen_resources and batch else None
            dish_rows.append((date, d.id, d.name, d.station, d.period, d.role, d.vessel, d.hot,
                              list(d.diet_tags), list(d.allergens), round(d.popularity, 4), batch, eq, prep))
        fd, fl = crowd_factors(date)
        c = cal.get(date, {})
        wd = dt.date.fromisoformat(date).strftime("%A")
        ctx_rows.append((date, wd, c.get("weekend", wd in ("Saturday", "Sunday")), fd, fl,
                         [e["name"] for e in c.get("events_at_or_affecting_worcester", [])],
                         round(cfg.crowd.expected_students * cfg.crowd.multiplier),
                         round(cfg.late_crowd.expected_students * cfg.late_crowd.multiplier) if cfg.late_crowd else None))
    out.append(_rows("dishes", ["menu_date", "dish_id", "name", "station", "period", "role", "vessel", "hot",
                                "diet_tags", "allergens", "popularity", "batch_cooked", "equipment", "prep_min"],
                     dish_rows, ["menu_date", "dish_id"]))
    out.append(_rows("date_context", ["menu_date", "weekday", "weekend", "dinner_crowd_factor",
                                      "late_night_crowd_factor", "events", "expected_dinner_students",
                                      "expected_late_night_students"], ctx_rows, ["menu_date"]))

    out.append(_rows("personas", ["persona", "label", "diet", "share", "seconds_prob", "dwell_median_min", "description"],
                     [(p.id, p.label, p.diet, p.share, p.seconds_prob, p.dwell_median_min, p.description)
                      for p in base.personas], ["persona"]))
    out.append(_rows("allergies", ["allergy", "share", "menu_labels"],
                     [(a.id, a.share, list(a.labels)) for a in base.allergies], ["allergy"]))
    if kr:
        res = [("cooks", p, n) for p, n in kr.cooks.items()] + [("equipment", k, n) for k, n in kr.equipment.items()]
        out.append(_rows("kitchen_resources", ["kind", "name", "units"], res, ["kind", "name"]))
    return "".join(out)


# ---------------------------------------------------------------- 2. new columns on the existing tables

NEW_COLUMNS = {
    # engine 0.4+ outputs per night (NightMetrics), and context the website shows next to them
    "nights": [
        ("waste_cost_usd", "double precision"),         # in nights.csv; the loader didn't copy it before
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
}


def alter_columns() -> str:
    lines = []
    for table, cols in NEW_COLUMNS.items():
        adds = ",\n  ".join(f"ADD COLUMN IF NOT EXISTS {c} {t}" for c, t in cols)
        lines.append(f"ALTER TABLE dcsim.{table}\n  {adds};")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 3. new fact tables (engine 0.4+)

NEW_TABLES = f"""
-- Per dish per night per run (NightMetrics.dishes): what the cook sheet and Waste pages show.
CREATE TABLE IF NOT EXISTS dcsim.dish_nights (
  ts timestamptz NOT NULL, night date NOT NULL, source text NOT NULL, scenario text NOT NULL,
  run_id text NOT NULL, seed int NOT NULL DEFAULT 0,
  dish_id text NOT NULL, station text, period text,
  to_order boolean, cooked int, served int, expired int, leftover int, waste int,
  stockout_min double precision, runout_min double precision, first_choice_demand int
);
CREATE INDEX IF NOT EXISTS dish_nights_dish_idx ON dcsim.dish_nights (dish_id, ts);
-- Servers needed per station and hour (engine 0.5.0, ISSUES N3): items served and servers for <= 85% busy.
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
-- Similar nights (pgvector): one vector per observed night ({VECTOR_DIM} = weekday 7 + weather 4 + event 1 + menu 64).
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

NEW_COLUMNSTORE = {
    "dish_nights": ("scenario, dish_id", "ts, seed"),
    "station_hours": ("scenario, station", "ts, seed"),
    "minute_series": ("scenario, kind, key", "ts, seed"),
}

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
       c.dinner_crowd_factor, c.late_night_crowd_factor, c.events, n.engine_version
FROM dcsim.nights n
LEFT JOIN dcsim.cat_values sv ON sv.category = 'source' AND sv.code = n.source
LEFT JOIN dcsim.date_context c ON c.menu_date = n.menu_date;

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


def columnstore_sql(spec: dict[str, tuple[str, str]]) -> str:
    """Enable the columnstore once per table (skipped if already on), then the policy."""
    out = []
    for t, (seg, order) in spec.items():
        out.append(f"""DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM timescaledb_information.hypertables
                 WHERE hypertable_schema = 'dcsim' AND hypertable_name = '{t}' AND compression_enabled) THEN
    ALTER TABLE dcsim.{t} SET (timescaledb.enable_columnstore, timescaledb.segmentby = '{seg}', timescaledb.orderby = '{order}');
  END IF;
END $$;
CALL add_columnstore_policy('dcsim.{t}', after => INTERVAL '{COLUMNSTORE_AFTER}', if_not_exists => true);""")
    return "\n".join(out) + "\n"


def script() -> str:
    head = f"""-- DC What-If: Tiger Data (TimescaleDB + pgvector) schema and categories for the website.
-- Generated {dt.date.today().isoformat()} by `uv run python -m dcsim.tiger_sql` (engine {ENGINE_VERSION}).
-- Idempotent: safe to run again. Existing rows are kept; new columns start NULL.
-- Fact tables are filled by `dcsim tsdb load` / `dcsim calibrate`; columns and tables marked
-- "engine 0.4+" need the loader extended before they hold data. Category tables are filled here.
-- Observed data is SYNTHETIC until UMass Dining shares real exports.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
"""
    parts = [
        head,
        "\n-- ============ 1. Existing tables (as tsdb.py creates them) ============\n", TABLES,
        "\n-- ============ 2. New columns on existing tables (engine 0.4+) ============\n", alter_columns(),
        "\n-- ============ 3. New fact tables (engine 0.4+) ============\n", NEW_TABLES,
        "\n-- ============ 4. Category tables ============\n", CATEGORY_TABLES,
        "\n-- ============ 5. Hypertables, columnstore, continuous aggregate ============\n",
        HYPERTABLES, NEW_HYPERTABLES, columnstore_sql({**COLUMNSTORE, **NEW_COLUMNSTORE}), CAGG + ";\n",
        "\n-- ============ 6. Views (forecast checks + website views) ============\n", FORECAST_CHECK, SCORECARD, WEB_VIEWS,
        "\n-- ============ 7. Category rows (from the current data files) ============\n", category_rows(),
    ]
    return "".join(parts)


if __name__ == "__main__":
    print(script())
