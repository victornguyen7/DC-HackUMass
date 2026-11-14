"""Dashboard aggregates, the raw-vs-aggregate benchmark and the /api/tiger endpoints (docs/TIGER.md
section 8). No database: SQL is checked as text and the timing code runs against a fake connection."""

from __future__ import annotations

import json

import pytest

from dcsim import tsdb
from dcsim.tiger_sql import script


def test_dashboard_aggregates_are_real_time_and_bucket_a_whole_night():
    for name, spec in tsdb.DASH_CAGGS.items():
        sql = tsdb.dash_cagg_sql(name)
        assert f"CREATE MATERIALIZED VIEW IF NOT EXISTS dcsim.{name}" in sql
        assert "timescaledb.continuous" in sql and "timescaledb.materialized_only = false" in sql
        assert sql.rstrip().endswith("WITH NO DATA")
        assert f"FROM dcsim.{spec['source']}" in sql and "America/New_York" in sql
        assert " night," in sql                        # joins straight to nights and forecasts
        assert f" AS {spec['marker']}" in sql or f" {spec['marker']}," in sql
        policy = tsdb.dash_policy_sql(name)
        assert f"add_continuous_aggregate_policy('dcsim.{name}'" in policy and "if_not_exists => true" in policy
        plain = tsdb.dash_cagg_sql(name, plain=True)   # plain PostgreSQL: an ordinary view, same columns
        assert plain.startswith(f"CREATE OR REPLACE VIEW dcsim.{name}") and "time_bucket" not in plain
    # the waste day starts at 6 am Eastern, so close-out weigh-ins after midnight stay with their night
    assert "06:00:00-05" in tsdb.dash_cagg_sql("waste_daily_by_dish")
    assert "time_bucket('1 hour'" in tsdb.dash_cagg_sql("pan_hourly")


def test_old_draft_aggregates_are_replaced_only_when_the_marker_column_is_missing():
    for name, spec in tsdb.DASH_CAGGS.items():
        assert f"DROP MATERIALIZED VIEW dcsim.{name}" in tsdb.DROP_DRAFT_CAGG
        assert f"table_name = '{name}' AND column_name = '{spec['marker']}'" in tsdb.DROP_DRAFT_CAGG
    assert tsdb.DROP_DRAFT_CAGG.count("NOT EXISTS") == len(tsdb.DASH_CAGGS)


def test_console_script_creates_the_aggregates_after_the_hypertables():
    sql = script()
    for name in tsdb.DASH_CAGGS:
        assert sql.index("create_hypertable('dcsim.waste_log'") < sql.index(f"VIEW IF NOT EXISTS dcsim.{name}")
        assert f"add_continuous_aggregate_policy('dcsim.{name}'" in sql


def test_named_queries_and_benchmark_pairs_use_the_aggregates():
    for q in ("stack", "hypertables", "aggregates", "waste-daily", "waste-dishes", "pans-hourly"):
        assert q in tsdb.QUERIES
    stack = tsdb.QUERIES["stack"][1]
    for rel in ("waste_daily_by_dish", "pan_hourly", "dcsim.nights", "planned_batches", "forecast_check", "dcsim.forecasts"):
        assert rel in stack, rel
    for b in tsdb.BENCH:
        assert f"dcsim.{b['raw_table']}" in b["raw"] and f"dcsim.{b['aggregate']}" not in b["raw"]
        assert f"dcsim.{b['aggregate']}" in b["agg"] and f"dcsim.{b['raw_table']}" not in b["agg"]
        assert tsdb.DASH_CAGGS[b["aggregate"]]["source"] == b["raw_table"]


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class _FakeConn:
    """Answers bench()'s statements: counts, the two result sets, and EXPLAIN ANALYZE timings."""

    def __init__(self, raw_ms: float, agg_ms: float, same: bool = True):
        self.raw_ms, self.agg_ms, self.same, self.explains = raw_ms, agg_ms, same, 0

    def execute(self, sql: str):
        on_aggregate = any(f"FROM dcsim.{a}" in sql for a in tsdb.DASH_CAGGS)
        if sql.startswith("EXPLAIN"):
            self.explains += 1
            t = self.agg_ms if on_aggregate else self.raw_ms
            return _Cursor([(json.dumps([{"Planning Time": 0.5, "Execution Time": t}]),)])
        if sql.startswith("SELECT count(*)"):
            return _Cursor([(100 if on_aggregate else 1000,)])
        return _Cursor([("a", 1)] if (self.same or on_aggregate) else [("a", 2)])


def test_bench_reports_median_server_times_speedup_and_whether_answers_match():
    conn = _FakeConn(raw_ms=99.5, agg_ms=9.5)
    out = tsdb.bench(conn, repeat=3)
    assert [b["id"] for b in out] == [b["id"] for b in tsdb.BENCH]
    assert conn.explains == len(tsdb.BENCH) * 2 * 4            # one warm-up + 3 timed runs per query
    for b in out:
        assert b["raw"] == {"plan_ms": 0.5, "exec_ms": 99.5, "total_ms": 100.0}
        assert b["agg"]["total_ms"] == 10.0 and b["speedup"] == 10.0
        assert b["raw_rows"] == 1000 and b["aggregate_rows"] == 100 and b["same_answer"] is True
    assert not any(b["same_answer"] for b in tsdb.bench(_FakeConn(5, 5, same=False), repeat=1))


def test_median():
    assert tsdb._median([3, 1, 2]) == 2 and tsdb._median([4, 1, 3, 2]) == 2.5


@pytest.mark.parametrize("path", ["/api/tiger/dashboard", "/api/tiger/bench"])
def test_tiger_endpoints_say_so_when_tiger_is_not_configured(path, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DCSIM_CACHE", str(tmp_path))
    monkeypatch.setenv("DCSIM_WORKERS", "1")
    from dcsim.api import app

    monkeypatch.setattr(tsdb, "configured", lambda: False)
    with TestClient(app) as c:
        r = c.get(path)
        assert r.status_code == 503 and "Tiger not configured" in r.json()["detail"]


def test_tiger_dashboard_endpoint_passes_the_night_through(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from fastapi.testclient import TestClient

    monkeypatch.setenv("DCSIM_CACHE", str(tmp_path))
    monkeypatch.setenv("DCSIM_WORKERS", "1")
    from dcsim.api import app

    @contextmanager
    def fake_connect(*a, **k):
        yield "conn"

    monkeypatch.setattr(tsdb, "configured", lambda: True)
    monkeypatch.setattr(tsdb, "connect", fake_connect)
    monkeypatch.setattr(tsdb, "dashboard", lambda conn, night=None: {"night": night, "conn": conn})
    monkeypatch.setattr(tsdb, "bench", lambda conn, repeat=5: [{"repeat": repeat}])
    with TestClient(app) as c:
        assert c.get("/api/tiger/dashboard?night=2026-11-07").json() == {"night": "2026-11-07", "conn": "conn"}
        assert c.get("/api/tiger/bench?repeat=99").json() == [{"repeat": 10}]   # capped


def test_dashboard_page_is_served_by_the_api_only(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    monkeypatch.setenv("DCSIM_CACHE", str(tmp_path))
    monkeypatch.setenv("DCSIM_WORKERS", "1")
    from dcsim.api import app

    with TestClient(app) as c:
        r = c.get("/tiger")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
        assert "/api/tiger/dashboard" in r.text and "<title>Live Kitchen Data</title>" in r.text
        assert "/tiger" not in c.get("/openapi.json").json()["paths"]   # a page, not part of the API contract
    web = Path(__file__).resolve().parents[2] / "web"
    assert not (web / "public" / "tiger.html").exists()
    assert "tiger.html" not in (web / "src" / "components" / "Shell.tsx").read_text()
