"""The 500-night sweep (nightsweep.py, docs/TIGER.md section 11). No database: rows from real nights, SQL as text."""

from __future__ import annotations

from dcsim import nightsweep, tsdb
from dcsim.config import config_hash
from dcsim.data import load_baseline
from dcsim.runner import _night_metrics

DATE = "2026-11-13"


def _rows(n: int = 2):
    cfg = load_baseline(DATE)
    nights = [_night_metrics((cfg, s)) for s in range(1, n + 1)]
    return cfg, nights, nightsweep.rows(cfg, nights, config_hash(cfg))


def test_rows_fill_the_tiger_columns_one_night_per_seed_one_dish_row_per_dish():
    cfg, nights, (night_rows, dish_rows) = _rows()
    assert set(night_rows[0]) == set(tsdb.NIGHT_COLS)
    assert set(dish_rows[0]) == set(tsdb.DISH_NIGHT_COLS)
    assert [r["seed"] for r in night_rows] == [1, 2]
    assert len(dish_rows) == 2 * len(cfg.menu)
    rid = nightsweep.run_id_of(DATE, config_hash(cfg))
    for r in night_rows + dish_rows:
        assert r["scenario"] == "sweep" and r["source"] == "simulated" and r["run_id"] == rid
        assert str(r["night"]) == DATE
    assert night_rows[0]["weekday"] == "Friday" and night_rows[0]["split"] == "sweep"
    # every dish row carries the engine's own numbers for that seed
    m = nights[0]
    by_dish = {r["dish_id"]: r for r in dish_rows if r["seed"] == m.seed}
    for k, d in m.dishes.items():
        assert by_dish[k]["stockout_min"] == d.stockout_min and by_dish[k]["waste"] == d.waste
    assert night_rows[0]["stockout_min"] == m.stockout_min_total


def test_dish_sql_names_dishes_filters_optionally_and_ignores_made_to_order():
    sql = nightsweep.DISH_SQL
    for part in ("dcsim.dish_nights", "JOIN dcsim.dishes d ON d.menu_date = x.night AND d.dish_id = x.dish_id",
                 "x.scenario = 'sweep'", "NOT x.to_order", "%(dish)s::text IS NULL", "ILIKE",
                 "percentile_cont(0.9)", "GROUP BY x.dish_id"):
        assert part in sql
    assert "%%" in sql  # literal % for psycopg with parameters


def test_named_query_and_scores_never_read_sweeps():
    assert "sweep" in tsdb.QUERIES and "scenario = 'sweep'" in tsdb.QUERIES["sweep"][1]
    # the track record only scores forecast_* scenarios, so a sweep on an observed date can't enter it
    assert "c.scenario IN ('forecast_naive', 'forecast_calibrated')" in tsdb.SCORE_PROC
