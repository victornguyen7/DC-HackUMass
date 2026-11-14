"""Optimize plan (IMPROVEMENTS 2.1): search space, levers, frontier and the API job."""

from __future__ import annotations

import time

import pytest

from dcsim import optimize as opt
from dcsim.data import load_baseline
from dcsim.levers import ScenarioRequest, build_config
from dcsim.service import Service
from dcsim.store import Store

DATE = "2026-11-13"


@pytest.fixture(scope="module")
def base():
    return load_baseline(DATE)


def test_levers_scale_batches_and_keep_capacity(base):
    sid = opt.batch_stations(base)[0]
    lv = opt.levers_for(base, {f"batch:{sid}": 0.5, f"reorder:{sid}": 1.2})
    assert lv.kitchen
    for dish, k in lv.kitchen.items():
        p = base.kitchen[dish]
        assert k.batch_size == max(1, round(p.batch_size * 0.5))
        total_before = p.batch_size * p.max_batches
        assert k.batch_size * (k.max_batches or p.max_batches) >= total_before  # never less food available
    cfg = build_config(ScenarioRequest(date=DATE, levers=lv))  # a plain Lab plan
    assert cfg.kitchen != base.kitchen
    assert opt.levers_for(base, {}).kitchen == {}


def test_describe_groups_uniform_changes(base):
    st = opt.batch_stations(base)
    assert opt.describe(base, {f"batch:{s}": 0.8 for s in st}) == ["All stations: batches x0.8"]
    assert opt.describe(base, {}) == []


def test_frontier_drops_dominated_and_slow_plans():
    rows = [{"n": 1, "waste": 10, "stockout_min": 5, "wait_ok": True}, {"n": 2, "waste": 8, "stockout_min": 9, "wait_ok": True},
            {"n": 3, "waste": 11, "stockout_min": 9, "wait_ok": True}, {"n": 4, "waste": 1, "stockout_min": 1, "wait_ok": False},
            {"n": 5, "waste": 8, "stockout_min": 9, "wait_ok": True}]
    assert [r["n"] for r in opt._frontier(rows)] == [2, 1]


def test_tiny_search(tmp_path):
    svc = Service(Store(tmp_path))
    trials = []
    r = opt.optimize(svc, DATE, trials=4, seeds=2, worlds_n=0, validate=(3, 4), on_trial=trials.append)
    assert [t["n"] for t in trials] == [1, 2, 3, 4] and r["nights_searched"] == 8
    assert r["history"][0]["waste"] == r["baseline"]["waste"]  # trial 1 is today's plan
    assert r["frontier"] and r["limits"]["wait_p90"] >= opt.WAIT_LIMIT_MIN
    for f in r["frontier"]:
        assert f["changes"] and "levers" in f
        if f["levers"].get("kitchen"):
            assert f["validation"]["seeds"] == [3, 4]
            build_config(ScenarioRequest(date=DATE, levers=f["levers"]))


def test_api_jobs(tmp_path_factory):
    from fastapi.testclient import TestClient

    mp = pytest.MonkeyPatch()
    mp.setenv("DCSIM_CACHE", str(tmp_path_factory.mktemp("cache")))
    mp.setenv("DCSIM_WORKERS", "1")
    mp.setattr(opt, "VALIDATE", (3, 4))
    from dcsim.api import app

    def wait(c, kind, jid):
        end = time.time() + 300
        while (s := c.get(f"/api/{kind}/{jid}").json())["status"] == "running" and time.time() < end:
            time.sleep(0.2)
        return s

    try:
        with TestClient(app) as c:
            assert c.post("/api/optimize", json={"date": "1999-01-01"}).status_code == 422
            assert c.post("/api/optimize", json={"trials": 1}).status_code == 422
            j = c.post("/api/optimize", json={"date": DATE, "trials": 4, "seeds": 2, "worlds": 0}).json()
            s = wait(c, "optimize", j["job_id"])
            assert s["status"] == "done", s
            body = c.get(j["stream"]).text
            assert body.index("event: trial") < body.index("event: final")
            assert s["result"]["frontier"]
            assert c.get(f"/api/worlds/{j['job_id']}").status_code == 404  # jobs are per kind

            j = c.post("/api/worlds", json={"request": {"date": DATE, "preset": "smaller_batches"},
                                            "n_worlds": 4, "tornado_worlds": 0}).json()
            s = wait(c, "worlds", j["job_id"])
            assert s["status"] == "done", s
            assert s["result"]["vs_baseline"]["n_pairs"] == 4 and s["result"]["tornado"] is None
            assert "event: final" in c.get(j["stream"]).text
            assert c.post("/api/worlds", json={"request": {"date": DATE, "preset": "nope"}}).status_code in (400, 422)
    finally:
        mp.undo()
