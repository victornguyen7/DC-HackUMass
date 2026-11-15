"""Plan searches kept in Tiger (docs/TIGER.md section 10): rows built from optimizer and worlds results,
the schema in the console script, and the API routes without Tiger."""

from __future__ import annotations

import os

import pytest

from dcsim import searchlog as sl
from dcsim.tiger_sql import script

OPT = {
    "date": "2026-11-13", "trials": 3, "seeds": 2, "nights_searched": 6, "feasible_trials": 1,
    "baseline": {"waste": 100.0, "stockout_min": 10.0, "wait_p90": 8.0},
    "limits": {"stockout_min_total": 11.0, "wait_p90": 8.5},
    "history": [
        {"n": 1, "waste": 100.0, "stockout_min": 10.0, "wait_p90": 8.0, "wait_ok": True, "within": True,
         "best_waste": 100.0, "params": {"batch:soups": 1.0}, "ts": "2026-11-14T10:00:00+00:00", "changes": ["Today's plan"]},
        {"n": 2, "waste": 70.0, "stockout_min": 14.0, "wait_p90": 8.1, "wait_ok": True, "within": False,
         "best_waste": 100.0, "params": {"batch:soups": 0.6}, "ts": "2026-11-14T10:00:01+00:00", "changes": ["Soups: batches x0.6"]},
        {"n": 3, "waste": 120.0, "stockout_min": 30.0, "wait_p90": 9.0, "wait_ok": False, "within": False,
         "best_waste": 100.0, "params": {"batch:soups": 1.2}, "ts": "2026-11-14T10:00:02+00:00", "changes": ["Soups: batches x1.2"]},
    ],
    "frontier": [
        {"n": 2, "waste": 70.0, "stockout_min": 14.0, "changes": ["Soups: batches x0.6"],
         "levers": {"kitchen": {"soups.lentil": {"batch_size": 30}}},
         "validation": {"seeds": [31, 60], "waste_pct": -28.6, "stockout_diff_min": 5.8}},
        {"n": 1, "waste": 100.0, "stockout_min": 10.0, "changes": ["Today's plan"], "levers": {}},
    ],
    "levers": None, "changes": [], "method": "Searched 3 plans",
}
WORLDS = {"n_worlds": 2, "uncertainty": {"crowd_sd": 0.1}, "vs_baseline": {"n_pairs": 2, "metrics": {}},
          "waste": {}, "tornado": None,
          "_per_world": [{"seed": s, "baseline": {"waste": 100.0 + s, "stockout_min": 10.0, "wait_p90": 8.0},
                          "plan": {"waste": 90.0 + s, "stockout_min": 11.0, "wait_p90": 8.0}} for s in (1, 2)]}


def test_trial_rows_mark_the_frontier_and_its_check():
    rows = sl.trial_rows("s1", OPT)
    assert [r["n"] for r in rows] == [1, 2, 3] and {r["menu_date"] for r in rows} == {"2026-11-13"}
    assert [r["on_frontier"] for r in rows] == [True, True, False]
    two = rows[1]
    assert two["validated_waste_pct"] == -28.6 and two["validated_stockout_diff"] == 5.8
    assert two["levers"] == {"kitchen": {"soups.lentil": {"batch_size": 30}}} and two["changes"] == ["Soups: batches x0.6"]
    assert rows[2]["levers"] is None and rows[2]["changes"] == ["Soups: batches x1.2"]
    assert set(rows[0]) == set(sl.TRIAL_COLS)


def test_world_rows_and_search_row():
    rows = sl.world_rows("w1", WORLDS)
    assert len(rows) == 4 and {r["side"] for r in rows} == {"baseline", "plan"} and set(rows[0]) == set(sl.WORLD_COLS)
    r = sl.search_row("worlds", WORLDS, {"search_id": "w1", "date": "2026-11-13"})
    assert r["weekday"] == "Friday" and r["nights"] == 4 and "_per_world" not in r["summary"]
    o = sl.search_row("optimize", OPT, {"search_id": "s1", "date": "2026-11-13", "source": "cli"})
    assert "history" not in o["summary"] and o["trials"] == 3 and o["nights"] == 6 and o["source"] == "cli"


def test_schema_is_in_the_console_script_after_the_hypertables():
    s = script()
    assert "CREATE TABLE IF NOT EXISTS dcsim.plan_searches" in s and "dcsim.search_worlds" in s
    assert "CREATE TABLE IF NOT EXISTS dcsim.dish_vectors" in s
    assert s.index("create_hypertable('dcsim.search_trials'") < s.index("ALTER TABLE dcsim.search_trials SET")
    assert "COMMENT ON TABLE dcsim.search_trials" in s
    assert "jsonb_each_text(t.params)" in sl.RECURRING_CHANGES_SQL and "on_frontier" in sl.RECURRING_CHANGES_SQL


def test_save_is_off_without_tiger_and_routes_say_so(monkeypatch):
    from fastapi.testclient import TestClient

    from dcsim import api

    out = api._save_search("optimize", OPT, {"search_id": "x", "date": "2026-11-13"})
    assert out["saved"] is False and "off" in out["reason"]
    monkeypatch.setenv("TIGER_SERVICE_ID", "test-service")
    monkeypatch.setattr(api, "_tiger_conn", lambda: None)
    assert api._save_search("optimize", OPT, {"search_id": "x", "date": "2026-11-13"})["reason"] == "Tiger isn't reachable"
    monkeypatch.setenv("DCSIM_LEDGER_WRITES", "0")
    assert "off" in api._save_search("optimize", OPT, {"search_id": "x", "date": "2026-11-13"})["reason"]
    c = TestClient(api.app)
    for url in ("/api/searches", "/api/searches/changes", "/api/searches/abc"):
        assert c.get(url).status_code == 503


@pytest.mark.skipif(not os.environ.get("DCSIM_TSDB_TEST_URL"),
                    reason="set DCSIM_TSDB_TEST_URL to a scratch database")
def test_round_trip():
    from dcsim import tsdb

    with tsdb.connect(os.environ["DCSIM_TSDB_TEST_URL"]) as conn:
        sl.init(conn)
        sid = sl.new_id()
        sl.save(conn, "optimize", OPT, {"search_id": sid, "date": "2026-11-13", "source": "test"})
        back = sl.get_search(conn, sid)
        assert back["frontier"][0]["levers"] and [h["n"] for h in back["history"]] == [1, 2, 3]
        assert any(c["lever"] == "batch:soups" and c["lower"] == 1 for c in sl.recurring_changes(conn, "2026-11-13"))
        conn.execute("DELETE FROM dcsim.search_trials WHERE search_id = %s", (sid,))
        conn.execute("DELETE FROM dcsim.plan_searches WHERE search_id = %s", (sid,))
        conn.commit()
