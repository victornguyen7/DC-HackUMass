"""Backend: cache-aware simulate, job streaming, compare, traces, scenarios, levers."""

from __future__ import annotations

import gzip
import json
import time

import pytest
from fastapi.testclient import TestClient

from dcsim.levers import Levers, ScenarioRequest, apply_levers, build_config
from dcsim.data import load_baseline

DATE = "2026-11-13"
N = 3


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("DCSIM_CACHE", str(tmp_path_factory.mktemp("cache")))
    mp.setenv("DCSIM_WORKERS", "1")
    from dcsim.api import app

    with TestClient(app) as c:
        yield c
    mp.undo()


def _wait(client, job_id: str, timeout: float = 120) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.1)
    raise TimeoutError(job_id)


def _simulate(client, body: dict) -> dict:
    r = client.post("/api/simulate", json=body)
    assert r.status_code in (200, 202), r.text
    data = r.json()
    if data["status"] == "running":
        job = _wait(client, data["job_id"])
        assert job["status"] == "done", job["error"]
        return job["result"]
    return data["result"]


def test_reference_endpoints(client):
    assert client.get("/api/health").json()["ok"]
    dates = client.get("/api/dates").json()
    assert len(dates) == 8 and dates[0]["weekday"] == "Friday"
    menu = client.get(f"/api/menu/{DATE}").json()
    assert menu["dishes"] and menu["stations"]
    arr = client.get(f"/api/arrivals/{DATE}").json()
    arr2 = client.get(f"/api/arrivals/{DATE}?crowd_multiplier=1.2").json()
    assert arr2["expected_students"] > arr["expected_students"] > 0
    assert client.get("/api/layout").json()["stations"]["grill"]["pos_m"]
    assert {p["id"] for p in client.get("/api/presets").json()} >= {"smaller_batches", "theme_night"}
    assert client.get("/api/facts/waste").json()["kind"] == "published"
    assert client.get("/api/menu/2030-01-01").status_code == 404


def test_simulate_then_cache_hit(client):
    first = _simulate(client, {"date": DATE, "n_nights": N})
    assert first["n_nights"] == N and "vs_baseline" not in first
    assert first["cook_sheet"] and first["headline"]["students"]["p50"] > 0
    again = client.post("/api/simulate", json={"date": DATE, "n_nights": N})
    assert again.status_code == 200 and again.json()["status"] == "done"
    assert again.json()["result"]["headline"] == first["headline"]
    # Same result from the results endpoint.
    assert client.get(f"/api/results/{first['config_hash']}?n_nights={N}").json()["headline"] == first["headline"]
    # Per-minute series on request (IMPROVEMENTS 1.4): bands for lines, pans and students inside.
    ser = client.get(f"/api/results/{first['config_hash']}?n_nights={N}&include=series").json()["series"]
    n_points = len(ser["inside"]["p50"])
    assert ser["step"] == 1 and ser["n_nights"] == N and n_points > 400
    assert len(ser["queue"]["grill"]["p90"]) == n_points and max(ser["queue"]["grill"]["p50"]) > 5
    assert any(max(b["p50"]) > 0 for b in ser["pans"].values()) and max(ser["inside"]["p50"]) > 300
    assert client.get(f"/api/results/{first['config_hash']}?include=other").status_code == 422


def test_preset_compares_against_baseline_on_paired_seeds(client):
    res = _simulate(client, {"date": DATE, "preset": "theme_night", "n_nights": N})
    vs = res["vs_baseline"]
    assert vs["n_pairs"] == N
    assert res["headline"]["students"]["p50"] > 0
    cmp = client.post("/api/compare", json={"baseline_hash": res["baseline_hash"],
                                            "scenario_hash": res["config_hash"], "n_nights": N}).json()
    assert cmp["metrics"]["waste_total"] == vs["metrics"]["waste_total"]


def test_sse_stream_ends_with_final(client):
    r = client.post("/api/simulate", json={"date": DATE, "preset": "extra_server", "n_nights": 2})
    assert r.status_code == 202
    events = []
    with client.stream("GET", r.json()["stream"]) as s:
        for line in s.iter_lines():
            if line.startswith("event: "):
                events.append(line[7:])
    assert events[0] == "progress" and events[-1] == "final"


def test_levers_and_validation(client):
    res = _simulate(client, {"date": DATE, "n_nights": 2, "levers": {"crowd_multiplier": 1.12, "stations": {"grill": {"servers": 3}}}})
    # readable, not "custom"; shows the snapped crowd value the engine actually ran
    assert res["label"].startswith("Crowd: x1.10") and res["label"].endswith("Grill Station servers: 2 -> 3")
    grill = next(s for s in res["stations"] if s["id"] == "grill")
    assert grill["servers"] == 3
    bad = client.post("/api/simulate", json={"date": DATE, "levers": {"kitchen": {"nope": {"batch_size": 5}}}})
    assert bad.status_code == 422
    assert client.post("/api/simulate", json={"date": DATE, "preset": "nope"}).status_code == 422


def test_crowd_lever_snaps_to_steps():
    a = build_config(ScenarioRequest(date=DATE, levers=Levers(crowd_multiplier=1.11)))
    b = build_config(ScenarioRequest(date=DATE, levers=Levers(crowd_multiplier=1.09)))
    base = load_baseline(DATE).crowd.multiplier  # the date's weekday/event factor (crowd_by_date.json)
    assert a.crowd.multiplier == b.crowd.multiplier == pytest.approx(base * 1.1)


def test_remove_dish_drops_its_plan():
    cfg = load_baseline(DATE)
    dish = next(iter(cfg.kitchen))
    out = apply_levers(cfg, Levers(remove_dishes=[dish]))
    assert dish not in out.kitchen and dish not in {d.id for d in out.menu}


def test_trace_and_scenarios(client):
    res = _simulate(client, {"date": DATE, "n_nights": N})
    r = client.get(f"/api/traces/{res['config_hash']}")
    assert r.status_code == 200
    body = r.content
    trace = json.loads(gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body)
    assert trace["students"] and trace["night_seed"] == int(r.headers["x-night-seed"])

    saved = client.post("/api/scenarios", json={"name": "base", "config_hash": res["config_hash"]})
    assert saved.status_code == 201
    sid = saved.json()["scenario_id"]
    assert client.post(f"/api/scenarios/{sid}/adopt").json()["status"] == "adopted"
    assert any(s["scenario_id"] == sid for s in client.get(f"/api/scenarios?date={DATE}").json())
    assert client.delete(f"/api/scenarios/{sid}").status_code == 204


# ---------------------------------------------------------------- ISSUES A1-A3


def test_admin_token_guards_delete_and_admin_jobs(client, monkeypatch):
    res = _simulate(client, {"date": DATE, "n_nights": N})
    sid = client.post("/api/scenarios", json={"name": "to delete", "config_hash": res["config_hash"]}).json()["scenario_id"]
    monkeypatch.setenv("DCSIM_ADMIN_TOKEN", "s3cret")
    assert client.delete(f"/api/scenarios/{sid}").status_code == 401
    assert client.delete(f"/api/scenarios/{sid}", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.post("/api/warm", json={"dates": [DATE], "presets": False, "n_nights": N}).status_code == 401
    # save and adopt stay open (judges use the Lab)
    assert client.post("/api/scenarios", json={"name": "open", "config_hash": res["config_hash"]}).status_code == 201
    assert client.delete(f"/api/scenarios/{sid}", headers={"Authorization": "Bearer s3cret"}).status_code == 204


def test_ai_routes_are_rate_limited(client):
    from dcsim.api import RateLimit

    old = client.app.state.ai_limit
    client.app.state.ai_limit = RateLimit(2)
    try:
        codes = [client.post("/api/ask", json={"question": "10% busier", "date": DATE}).status_code for _ in range(3)]
        assert codes[:2] == [200, 200] and codes[2] == 429
        r = client.post("/api/explain", json={"config_hash": "x"})
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    finally:
        client.app.state.ai_limit = old
    lim = RateLimit(1)
    assert lim.check("a", now=0) is None and lim.check("a", now=10) == 50.0 and lim.check("a", now=61) is None


def test_finished_jobs_expire(client):
    import time as _t

    from dcsim.api import JOB_KEEP_MAX, JOB_TTL_S

    jobs = client.app.state.jobs
    r = client.post("/api/simulate", json={"date": DATE, "preset": "trim_slow", "n_nights": N})
    job_id = r.json().get("job_id")
    if job_id:
        _wait(client, job_id)
        assert job_id in jobs.by_id
        jobs.prune(now=_t.monotonic() + JOB_TTL_S + 1)
        assert job_id not in jobs.by_id
    assert JOB_KEEP_MAX >= 10


def test_warm_and_backtest_admin_jobs(client):
    r = client.post("/api/warm", json={"dates": [DATE], "presets": False, "n_nights": N})
    assert r.status_code == 202 and r.json()["total"] == 1
    end = time.time() + 120
    while client.get("/api/warm").json()["status"] == "running" and time.time() < end:
        time.sleep(0.1)
    st = client.get("/api/warm").json()
    assert st["status"] == "done" and st["done"] == 1 and st["result"]["configs"] == 1
    assert client.post("/api/warm", json={"dates": ["2030-01-01"]}).status_code == 404
    assert client.post("/api/backtest", json={"plan": "nope"}).status_code == 422
    assert client.get("/api/backtest").json()["status"] in ("idle", "running", "done", "error")


def test_ledger_writes_switch(client, monkeypatch):
    """DCSIM_LEDGER_WRITES=0 keeps test servers from logging forecasts to the append-only Tiger ledger."""
    from fastapi import BackgroundTasks

    from dcsim import api

    assert client.get("/api/health").json()["ledger_writes"] is False  # conftest leaves Tiger unset
    monkeypatch.setenv("TIGER_SERVICE_ID", "test-service")
    assert api._ledger_writes() is True
    monkeypatch.setenv("DCSIM_LEDGER_WRITES", "0")
    assert client.get("/api/health").json()["ledger_writes"] is False
    tasks = BackgroundTasks()
    out = api._queue_forecast({"config_hash": "x", "name": "n", "scenario_id": "s"}, None, tasks)
    assert out["forecast"].startswith("not logged (ledger writes off") and not tasks.tasks


def test_waste_outlook_route(client):
    o = client.get("/api/waste/outlook?n_nights=3").json()
    assert o["kind"] == "waste_outlook" and o["recommended_plan"]
    base = [e for k, e in o["entries"].items() if k.endswith("|baseline|")]
    assert base and all(e["lb"]["p50"] > 0 for e in base)
    r = client.get(f"/api/results/{base[0]['config_hash']}?n_nights=3").json()
    assert r["waste_lb"]["lb"]["p50"] > 0
