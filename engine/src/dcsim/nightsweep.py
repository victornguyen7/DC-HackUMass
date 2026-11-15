"""Many simulated nights of one menu date in Tiger: the "500 nights" question (docs/TIGER.md section 11).

`dcsim tsdb sweep --date 2026-11-13 --nights 500` runs (or reads from the results cache) N nights of that
date's baseline plan and writes them to Tiger as scenario 'sweep':

    nights       one row per simulated night (seed 1..N), same columns as every other simulated row
    dish_nights  one row per dish per night: cooked, served, waste, minutes out of stock, first run-out

One SQL statement then answers "how long is <dish> out of stock, across 500 nights?" (DISH_SQL), with the
spread (median, p90), how often it runs out at all, and the typical clock time it first runs out.

The rows are replaced on every load of the same date and plan (run_id sweep:<date>:<config hash>).
forecast_check / the track record only read scenarios named forecast_*, so sweep rows never enter the
calibration scores.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from . import tsdb
from .config import ScenarioConfig, config_hash
from .data import load_baseline

SCENARIO = "sweep"
DEFAULT_NIGHTS = 500
MAX_NIGHTS = 2000


def run_id_of(date: str, h: str) -> str:
    return f"{SCENARIO}:{date}:{h[:12]}"


def rows(cfg: ScenarioConfig, nights, h: str) -> tuple[list[dict], list[dict]]:
    """NightMetrics (one per seed) -> (nights rows, dish_nights rows) for Tiger."""
    from .calibration import night_values
    from .synthetic import dish_cost, dish_oz

    oz, cost = dish_oz(cfg), dish_cost(cfg)
    rid, night = run_id_of(cfg.date, h), cfg.date
    open_ts = tsdb.at(night, cfg.open_min)
    weekday = dt.date.fromisoformat(night).strftime("%A")
    station_of = {d.id: d.station for d in cfg.menu}
    night_rows, dish_rows = [], []
    for m in nights:
        key = {"night": night, "source": "simulated", "scenario": SCENARIO, "run_id": rid, "seed": m.seed}
        night_rows.append(key | {
            "ts": open_ts, "draw": 0, "menu_date": night, "plan": "baseline", "weekday": weekday,
            "weather": None, "event_night": None, "adopted": False, "split": SCENARIO,
            "crowd_multiplier": None, "over_prep": None, "plate_lb": None,
            **night_values(cfg, m, oz), **tsdb.metric_columns(cfg, m, h, cost)})
        dish_rows += [key | {"ts": open_ts, "dish_id": k, "station": station_of.get(k), "period": tsdb.period_of(k),
                             **{f: getattr(d, f) for f in ("to_order", "cooked", "served", "expired", "leftover",
                                                           "waste", "stockout_min", "runout_min",
                                                           "first_choice_demand")}}
                      for k, d in m.dishes.items()]
    return night_rows, dish_rows


def load(conn, svc, date: str, n: int = DEFAULT_NIGHTS, log: Callable[[str], None] = print) -> dict:
    """Simulate (or read cached) n nights of `date`'s baseline plan and replace that sweep in Tiger."""
    if not 1 <= n <= MAX_NIGHTS:
        raise ValueError(f"nights must be 1-{MAX_NIGHTS}")
    cfg = load_baseline(date)
    h = config_hash(cfg)
    todo = sum(len(b) for b in svc.batches(cfg, n))
    log(f"{date}: {n} nights of the baseline plan ({n - todo} cached, {todo} to simulate)")
    t0 = dt.datetime.now()
    last = [0]

    def progress(done: int, total: int) -> None:
        if done - last[0] >= 50 or done == total:
            last[0] = done
            log(f"  simulated {done}/{total}")

    nights = svc.ensure_nights(cfg, n, on_batch=progress)
    night_rows, dish_rows = rows(cfg, nights, h)
    rid = run_id_of(date, h)
    # Two loads of the same date at once would both delete, then both insert (duplicate key): the second
    # waits here until the first commits, then replaces its rows. Released at commit or rollback.
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"dcsim.sweep:{date}",))
    for t in ("nights", "dish_nights"):
        conn.execute(f"DELETE FROM dcsim.{t} WHERE scenario = %s AND night = %s", (SCENARIO, date))
    tsdb.load_categories(conn)  # dish names for the menu dates (dishes table)
    written = {"nights": tsdb.copy_rows(conn, "nights", tsdb.NIGHT_COLS, night_rows),
               "dish_nights": tsdb.copy_rows(conn, "dish_nights", tsdb.DISH_NIGHT_COLS, dish_rows)}
    conn.commit()
    secs = (dt.datetime.now() - t0).total_seconds()
    log(f"-> Tiger: {written['nights']:,} nights, {written['dish_nights']:,} dish rows (run_id {rid}, {secs:.0f} s)")
    return {"date": date, "run_id": rid, "config_hash": h, "simulated": todo, **written}


# The demo query: one statement over the sweep's dish_nights, named dishes from the menu, the spread, how often
# the dish runs out at all, and when it first does (runout_min is the minute of the day; the median is over the
# nights it ran out). Grouped by dish_id: a dish on both the dinner and the late-night menu is two rows.
DISH_SQL = """
SELECT d.name AS dish, x.station, replace(x.period, '_', ' ') AS period, count(*) AS nights,
       round(avg(x.stockout_min)::numeric, 1) AS avg_empty_min,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY x.stockout_min)::numeric, 1) AS p50_min,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY x.stockout_min)::numeric, 1) AS p90_min,
       round(100 * avg((x.stockout_min > 0)::int)::numeric, 1) AS pct_nights_out,
       to_char(TIME '00:00' + percentile_cont(0.5) WITHIN GROUP (ORDER BY x.runout_min) * INTERVAL '1 minute',
               'HH12:MI am') AS median_first_out
FROM dcsim.dish_nights x
JOIN dcsim.dishes d ON d.menu_date = x.night AND d.dish_id = x.dish_id
WHERE x.scenario = 'sweep' AND x.night = %(night)s AND NOT x.to_order
  AND (%(dish)s::text IS NULL OR d.name ILIKE '%%' || %(dish)s || '%%')
GROUP BY x.dish_id, d.name, x.station, x.period
ORDER BY avg(x.stockout_min) DESC, d.name
LIMIT %(limit)s
"""

def dish_report(conn, date: str, dish: str | None = None, limit: int = 15) -> tuple[list[str], list[tuple]]:
    with conn.cursor() as cur:
        cur.execute(DISH_SQL, {"night": date, "dish": dish or None, "limit": limit})
        return [c.name for c in cur.description], cur.fetchall()


def sweeps(conn) -> list[dict]:
    """Which sweeps are in Tiger: date, nights, run_id."""
    with conn.cursor() as cur:
        cur.execute("SELECT night::text, count(*) AS nights, min(run_id) AS run_id, min(engine_version) AS engine "
                    "FROM dcsim.nights WHERE scenario = 'sweep' GROUP BY night ORDER BY night")
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


__all__ = ["SCENARIO", "DEFAULT_NIGHTS", "MAX_NIGHTS", "run_id_of", "rows", "load", "dish_report", "sweeps",
           "DISH_SQL"]
