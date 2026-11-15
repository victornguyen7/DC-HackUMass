"""Plan searches kept in Tiger (docs/TIGER.md section 10).

Every Optimize plan run (optimize.py) and every Plausible worlds run (worlds.sweep) can be saved:

    dcsim.plan_searches   one row per search: date, weekday, limits, baseline, and its summary (JSONB)
    dcsim.search_trials   hypertable on ts: one row per plan the optimizer tried (waste, stockouts, wait,
                          within limits, on the frontier, unseen-night check, changes, levers)
    dcsim.search_worlds   one row per world and side (baseline | plan) of a worlds run

so SQL can ask what a single run can't: which change keeps showing up on the frontier, and is the
best trade-off different on Fridays? Saving is gated like the forecast ledger (API: Tiger configured
and DCSIM_LEDGER_WRITES != 0; CLI: --save). Unlike the ledger these tables are not append-only.
"""

from __future__ import annotations

import datetime as dt
import uuid

from .nightsearch import WEEKDAYS

TABLES = """
-- Plan searches (searchlog.py): one row per Optimize plan or Plausible worlds run.
CREATE TABLE IF NOT EXISTS dcsim.plan_searches (
  search_id text PRIMARY KEY,
  kind text NOT NULL CHECK (kind IN ('optimize', 'worlds')),
  menu_date date NOT NULL, weekday text,
  started_at timestamptz, finished_at timestamptz NOT NULL DEFAULT now(),
  engine_version text, baseline_hash text, plan_hash text,
  trials int, seeds int, nights int,
  limits jsonb, baseline jsonb, summary jsonb,
  source text NOT NULL DEFAULT 'api'
);
CREATE TABLE IF NOT EXISTS dcsim.search_trials (
  ts timestamptz NOT NULL, search_id text NOT NULL, menu_date date NOT NULL, n int NOT NULL,
  waste double precision, stockout_min double precision, wait_p90 double precision,
  within boolean, on_frontier boolean,
  validated_waste_pct double precision, validated_stockout_diff double precision,
  params jsonb, levers jsonb, changes text[]
);
CREATE INDEX IF NOT EXISTS search_trials_search_idx ON dcsim.search_trials (search_id, n);
CREATE TABLE IF NOT EXISTS dcsim.search_worlds (
  search_id text NOT NULL, side text NOT NULL CHECK (side IN ('baseline', 'plan')), seed int NOT NULL,
  waste double precision, stockout_min double precision, wait_p90 double precision,
  PRIMARY KEY (search_id, side, seed)
);
"""
HYPERTABLE = """
SELECT create_hypertable('dcsim.search_trials', by_range('ts', INTERVAL '7 days'), if_not_exists => TRUE);
"""
COMMENTS = """
COMMENT ON TABLE dcsim.plan_searches IS 'One row per plan search: kind optimize (Optuna search over kitchen plans: summary has the waste-vs-stockouts frontier, each plan re-checked on unseen seeds) or worlds (a plan vs the baseline in plausible worlds: summary has win rates and the waste tornado). Simulated, not observed.';
COMMENT ON TABLE dcsim.search_trials IS 'Every kitchen plan the optimizer tried: median waste, stockout minutes and p90 wait over its search seeds; within = met the stockout and wait limits; on_frontier = no other plan had both less waste and fewer stockouts; validated_* = frontier plans re-checked on seeds the search never saw (waste % vs today, stockout minutes added). changes are readable (e.g. ''Soups: batches x0.6'').';
COMMENT ON TABLE dcsim.search_worlds IS 'Per plausible world (seed) of a worlds run: waste, stockout minutes and p90 wait for the baseline and the plan, which faced the same worlds.';
"""

SEARCHES_SQL = """
SELECT s.search_id, s.kind, s.menu_date, s.weekday, s.started_at, s.finished_at, s.trials, s.seeds, s.nights,
       s.source, s.baseline, s.limits,
       s.summary->'feasible_trials' AS feasible_trials,
       s.summary->'vs_baseline'->'metrics'->'waste_total'->'win_rate' AS worlds_waste_win,
       s.summary->'n_worlds' AS n_worlds,
       (SELECT min(t.validated_waste_pct) FROM dcsim.search_trials t
         WHERE t.search_id = s.search_id AND t.on_frontier) AS best_validated_waste_pct,
       (SELECT t.validated_stockout_diff FROM dcsim.search_trials t
         WHERE t.search_id = s.search_id AND t.on_frontier AND t.validated_waste_pct IS NOT NULL
         ORDER BY t.validated_waste_pct LIMIT 1) AS best_validated_stockout_diff
FROM dcsim.plan_searches s
WHERE (%(date)s::date IS NULL OR s.menu_date = %(date)s::date)
  AND (%(kind)s::text IS NULL OR s.kind = %(kind)s::text)
ORDER BY s.finished_at DESC
LIMIT %(limit)s
"""
# "Which change keeps showing up on the frontier?" across every saved optimizer run: per lever (a search
# parameter such as batch:soups), how often the frontier plans that changed anything moved it down or up.
RECURRING_CHANGES_SQL = """
SELECT p.key AS lever, coalesce(max(s.summary->'param_labels'->>p.key), p.key) AS label,
       count(DISTINCT t.search_id) AS searches, count(*) AS frontier_plans,
       count(*) FILTER (WHERE p.value::float8 < 0.999) AS lower,
       count(*) FILTER (WHERE p.value::float8 > 1.001) AS higher,
       round(avg(p.value::float8)::numeric, 2) AS avg_multiplier,
       round(avg(t.validated_waste_pct)::numeric, 1) AS waste_pct,
       round(avg(t.validated_stockout_diff)::numeric, 1) AS stockout_diff_min
FROM dcsim.search_trials t
JOIN dcsim.plan_searches s USING (search_id)
CROSS JOIN LATERAL jsonb_each_text(t.params) AS p(key, value)
WHERE t.on_frontier AND t.validated_waste_pct IS NOT NULL
  AND (%(date)s::date IS NULL OR t.menu_date = %(date)s::date)
GROUP BY p.key
HAVING count(*) FILTER (WHERE abs(p.value::float8 - 1) > 0.001) > 0
ORDER BY greatest(count(*) FILTER (WHERE p.value::float8 < 0.999),
                  count(*) FILTER (WHERE p.value::float8 > 1.001))::float8 / count(*) DESC,
         abs(avg(p.value::float8) - 1) DESC, p.key
LIMIT %(limit)s
"""
# Per weekday: the median best unseen-night waste change among frontier plans, one value per search.
BY_WEEKDAY_SQL = """
WITH best AS (
  SELECT s.search_id, s.weekday, min(t.validated_waste_pct) AS waste_pct
  FROM dcsim.plan_searches s JOIN dcsim.search_trials t USING (search_id)
  WHERE s.kind = 'optimize' AND t.on_frontier AND t.validated_waste_pct IS NOT NULL
  GROUP BY 1, 2)
SELECT weekday, count(*) AS searches,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY waste_pct)::numeric, 1) AS median_best_waste_pct
FROM best GROUP BY weekday ORDER BY searches DESC, weekday
"""
TOTALS_SQL = """
SELECT (SELECT count(*) FROM dcsim.plan_searches) AS searches,
       (SELECT count(*) FROM dcsim.plan_searches WHERE kind = 'optimize') AS optimize_runs,
       (SELECT count(*) FROM dcsim.plan_searches WHERE kind = 'worlds') AS worlds_runs,
       (SELECT count(*) FROM dcsim.search_trials) AS trials,
       (SELECT count(*) FROM dcsim.search_worlds) AS worlds
"""

TRIAL_COLS = ("ts", "search_id", "menu_date", "n", "waste", "stockout_min", "wait_p90", "within", "on_frontier",
              "validated_waste_pct", "validated_stockout_diff", "params", "levers", "changes")
WORLD_COLS = ("search_id", "side", "seed", "waste", "stockout_min", "wait_p90")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def trial_rows(search_id: str, result: dict) -> list[dict]:
    """One row per optimizer trial; frontier plans carry their levers and unseen-night check."""
    front = {f["n"]: f for f in result.get("frontier", [])}
    out = []
    for h in result.get("history", []):
        f = front.get(h["n"])
        v = (f or {}).get("validation") or {}
        out.append({
            "ts": h.get("ts") or _now(), "search_id": search_id, "menu_date": result["date"], "n": h["n"],
            "waste": h["waste"], "stockout_min": h["stockout_min"], "wait_p90": h["wait_p90"],
            "within": h["within"], "on_frontier": f is not None,
            "validated_waste_pct": v.get("waste_pct"), "validated_stockout_diff": v.get("stockout_diff_min"),
            "params": h.get("params") or {}, "levers": f["levers"] if f else None,
            "changes": list(f["changes"] if f else h.get("changes") or []),
        })
    return out


def world_rows(search_id: str, result: dict) -> list[dict]:
    rows = []
    for w in result.get("_per_world") or []:
        for side in ("baseline", "plan"):
            m = w[side]
            rows.append({"search_id": search_id, "side": side, "seed": w["seed"], "waste": m["waste"],
                         "stockout_min": m["stockout_min"], "wait_p90": m["wait_p90"]})
    return rows


def summary_of(kind: str, result: dict) -> dict:
    """What plan_searches.summary keeps: the result minus what the trial and world tables hold."""
    drop = {"history"} if kind == "optimize" else {"_per_world"}
    return {k: v for k, v in result.items() if k not in drop and k != "tiger"}


def search_row(kind: str, result: dict, meta: dict) -> dict:
    date = meta["date"]
    return {
        "search_id": meta["search_id"], "kind": kind, "menu_date": date,
        "weekday": WEEKDAYS[dt.date.fromisoformat(date).weekday()],
        "started_at": meta.get("started_at"), "engine_version": meta.get("engine_version"),
        "baseline_hash": meta.get("baseline_hash"), "plan_hash": meta.get("plan_hash"),
        "trials": result.get("trials"), "seeds": result.get("seeds") or result.get("n_worlds"),
        "nights": result.get("nights_searched") or (2 * result["n_worlds"] if result.get("n_worlds") else None),
        "limits": result.get("limits"), "baseline": result.get("baseline"),
        "summary": summary_of(kind, result), "source": meta.get("source", "api"),
    }


def init(conn, timescale: bool | None = None) -> None:
    from . import tsdb

    conn.execute(TABLES)
    if tsdb.has_timescale(conn) if timescale is None else timescale:
        conn.execute(HYPERTABLE)
    conn.execute(COMMENTS)


def exists(conn) -> bool:
    """True once the tables exist (reads never create them: concurrent DDL from two requests collides)."""
    return conn.execute("SELECT to_regclass('dcsim.search_trials') IS NOT NULL AND "
                        "to_regclass('dcsim.plan_searches') IS NOT NULL").fetchone()[0]


def save(conn, kind: str, result: dict, meta: dict) -> dict:
    """Write one search (search row, trials or worlds) in one transaction. Returns {saved, search_id}."""
    from psycopg.types.json import Jsonb

    from .tsdb import copy_rows

    row = search_row(kind, result, meta)
    cols = tuple(row)
    vals = tuple(Jsonb(v) if c in ("limits", "baseline", "summary") and v is not None else v for c, v in row.items())
    with conn.transaction():
        conn.execute(f"INSERT INTO dcsim.plan_searches ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                     vals)
        if kind == "optimize":
            copy_rows(conn, "search_trials", TRIAL_COLS, trial_rows(meta["search_id"], result))
        else:
            copy_rows(conn, "search_worlds", WORLD_COLS, world_rows(meta["search_id"], result))
    return {"saved": True, "search_id": meta["search_id"]}


def _plain(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if hasattr(v, "is_finite"):  # Decimal
        return float(v)
    return v


def _dicts(cur) -> list[dict]:
    cols = [c.name for c in cur.description]
    return [{c: _plain(x) for c, x in zip(cols, r)} for r in cur.fetchall()]


def list_searches(conn, date: str | None = None, kind: str | None = None, limit: int = 20) -> list[dict]:
    return _dicts(conn.execute(SEARCHES_SQL, {"date": date, "kind": kind, "limit": limit}))


def recurring_changes(conn, date: str | None = None, limit: int = 12) -> list[dict]:
    return _dicts(conn.execute(RECURRING_CHANGES_SQL, {"date": date, "limit": limit}))


def by_weekday(conn) -> list[dict]:
    return _dicts(conn.execute(BY_WEEKDAY_SQL))


def totals(conn) -> dict:
    return _dicts(conn.execute(TOTALS_SQL))[0]


def get_search(conn, search_id: str) -> dict | None:
    """A saved search in the shape its live result had (optimize: history rebuilt from search_trials)."""
    r = conn.execute("SELECT kind, summary, source, finished_at FROM dcsim.plan_searches WHERE search_id = %s",
                     (search_id,)).fetchone()
    if r is None:
        return None
    kind, summary, source, finished = r
    out = dict(summary) | {"search_id": search_id, "kind": kind, "source": source, "finished_at": _plain(finished)}
    if kind == "optimize":
        rows = _dicts(conn.execute(
            "SELECT n, waste, stockout_min, wait_p90, within FROM dcsim.search_trials WHERE search_id = %s ORDER BY n",
            (search_id,)))
        best, hist = None, []
        for h in rows:
            if h["within"]:
                best = h["waste"] if best is None else min(best, h["waste"])
            hist.append(h | {"best_waste": best})
        out["history"] = hist
    return out


__all__ = ["TABLES", "HYPERTABLE", "COMMENTS", "exists", "trial_rows", "world_rows", "search_row", "summary_of", "init", "save",
           "list_searches", "recurring_changes", "by_weekday", "totals", "get_search", "new_id"]
