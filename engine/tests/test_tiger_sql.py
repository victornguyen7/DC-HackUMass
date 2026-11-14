"""The Tiger console script (sql/tiger_website.sql) is generated from the engine's schema and data."""

from __future__ import annotations

from pathlib import Path

from dcsim.tiger_sql import NEW_COLUMNS, script


def test_script_has_every_table_new_column_and_category():
    sql = script()
    for t in ("nights", "pan_windows", "waste_log", "planned_batches", "forecasts", "calibrations",
              "dish_nights", "station_hours", "minute_series", "night_vectors",
              "cat_values", "periods", "stations", "dishes", "personas", "allergies", "date_context", "kitchen_resources"):
        assert f"dcsim.{t}" in sql, t
    for table, cols in NEW_COLUMNS.items():
        for c, _ in cols:
            assert f"ADD COLUMN IF NOT EXISTS {c} " in sql, (table, c)
    assert "'late_night', 'Late night'" in sql and "'pescetarian'" in sql and "'carving'" in sql
    assert "CREATE TABLE dcsim." not in sql  # always IF NOT EXISTS: safe to re-run


def test_committed_script_is_current():
    """Re-generate with `uv run python -m dcsim.tiger_sql > sql/tiger_website.sql` after data changes."""
    committed = (Path(__file__).resolve().parents[1] / "sql" / "tiger_website.sql").read_text()
    strip = lambda s: [ln for ln in s.splitlines() if not ln.startswith("-- Generated")]  # noqa: E731
    assert strip(committed) == strip(script() + "\n")
