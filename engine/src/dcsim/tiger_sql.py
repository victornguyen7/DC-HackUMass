"""One SQL script for the Tiger Data console: every dcsim table, the columns added since engine 0.4,
and category (lookup) tables seeded from the current data files.

    uv run python -m dcsim.tiger_sql > sql/tiger_website.sql

Paste the file into the Tiger console's SQL editor (or `psql -f`). It is idempotent: tables are
created if missing, new columns are added with ADD COLUMN IF NOT EXISTS (existing rows keep their
data, new columns start NULL), and category rows are upserted, so it is safe to run again after the
data files change.

What fills what:
- `dcsim tsdb load` fills observed nights, pan_windows, waste_log and planned_batches (incl. the
  new columns a real record has: waste cost, students and waste by period, period, plan fields)
  and the category tables.
- `dcsim calibrate` fills simulated nights with every engine 0.4+ metric column, plus
  dish_nights, station_hours and minute_series for its replays.
- The schema lives in tsdb.py (one source for this script and the loader).
"""

from __future__ import annotations

import datetime as dt
import json

from . import dishsearch, fleet, searchlog
from .config import ENGINE_VERSION
from .data import available_dates, crowd_factors, default_data_dir, load_baseline
from .tsdb import (  # noqa: F401  (NEW_COLUMNS re-exported for tests)
    CAGG, CATEGORY_TABLES, COLUMNSTORE, COLUMNSTORE_AFTER, COMMENTS, COMMENTS_TIMESCALE, FORECAST_CHECK, HYPERTABLES,
    NEW_COLUMNS, NEW_HYPERTABLES, NEW_TABLES, SCORE_PROC, SCORECARD, TABLES, TRACK_CAGG, TRACK_HYPERTABLE, TRACK_TABLE,
    VECTOR_TABLE, WEB_VIEWS, alter_columns_sql, dash_caggs_script,
)


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
                        round(base.late_crowd.expected_students), "Serving stops at midnight; seated students may stay")) # pyright: ignore[reportOptionalMemberAccess]
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

# ---------------------------------------------------------------- 3. new fact tables (engine 0.4+)

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
-- Fact tables are filled by `dcsim tsdb load` (observed, categories) and `dcsim calibrate` (simulated
-- metrics, dish_nights, station_hours, minute_series). Category tables are also filled here.
-- Observed data is SYNTHETIC until UMass Dining shares real exports.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
"""
    parts = [
        head,
        "\n-- ============ 1. Existing tables (as tsdb.py creates them) ============\n", TABLES,
        "\n-- ============ 2. New columns on existing tables (engine 0.4+) ============\n", alter_columns_sql(),
        "\n-- ============ 3. New fact tables (engine 0.4+) ============\n", NEW_TABLES, VECTOR_TABLE, TRACK_TABLE,
        "\n-- Dish search (dishsearch.py; `dcsim tsdb dishes` fills it and adds a DiskANN index)\n", dishsearch.SCHEMA,
        searchlog.TABLES, fleet.TABLES,
        "\n-- ============ 4. Category tables ============\n", CATEGORY_TABLES,
        "\n-- ============ 5. Hypertables, columnstore, continuous aggregate ============\n",
        HYPERTABLES, NEW_HYPERTABLES, TRACK_HYPERTABLE, searchlog.HYPERTABLE, columnstore_sql(COLUMNSTORE), CAGG + ";\n",
        "\n-- Dashboard aggregates: real-time, with refresh policies. They are created empty; fill them once with\n"
        "--   CALL refresh_continuous_aggregate('dcsim.waste_daily_by_dish', NULL, NULL);\n"
        "--   CALL refresh_continuous_aggregate('dcsim.pan_hourly', NULL, NULL);\n"
        "-- (each as its own statement, outside a transaction; `dcsim tsdb refresh` does both).\n",
        dash_caggs_script(),
        "\n-- ============ 6. Views (forecast checks + website views) ============\n", FORECAST_CHECK, SCORECARD, WEB_VIEWS,
        "\n-- ============ 7. Track record: scoring procedure, nightly job, weekly aggregate ============\n"
        "-- Fill it once with CALL dcsim.score_forecasts(); the job then runs it every morning.\n",
        SCORE_PROC, TRACK_CAGG,
        "\n-- ============ 8. Comments (what an assistant reading the schema, e.g. the Tiger MCP, sees) ============\n",
        COMMENTS, searchlog.COMMENTS, COMMENTS_TIMESCALE,
        "\n-- ============ 9. Category rows (from the current data files) ============\n", category_rows(),
    ]
    return "".join(parts)


if __name__ == "__main__":
    print(script())
