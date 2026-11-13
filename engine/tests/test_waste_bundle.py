"""Synthetic waste history, back-test output and the static demo bundle."""

from __future__ import annotations

import datetime as dt
import gzip
import json
from collections import Counter

import pytest

from dcsim.data import load_baseline
from dcsim.metrics import compute_metrics
from dcsim.model import Night
from dcsim.service import Service
from dcsim.store import Store
from dcsim.synthetic import actual_config, dish_oz, ground_truth, menu_date_for, simulate_night

DAY = dt.date(2026, 10, 15)


@pytest.fixture(scope="module")
def night():
    gt = ground_truth(DAY, adopted=False)
    return gt, simulate_night(DAY, gt)


def test_past_nights_reuse_the_same_weekday_menu():
    for i in range(7):
        d = DAY + dt.timedelta(days=i)
        assert dt.date.fromisoformat(menu_date_for(d)).weekday() == d.weekday()


def test_records_balance_with_the_simulated_night(night):
    """MetaFoodX-style takes and LeanPath-style weigh-ins add up to the engine's own counts."""
    gt, rec = night
    cfg = actual_config(DAY, gt)
    m = compute_metrics(Night(cfg, gt["seed"]).run())
    taken = Counter()
    for r in rec["consumption"]:
        taken[r["dish_id"]] += r["portions_eaten"]
    by_reason = {"expired": Counter(), "close": Counter()}
    for w in rec["waste"]:
        if w["reason"] in by_reason:
            by_reason[w["reason"]][w["dish_id"]] += w["portions"]
    for dish_id, d in m.dishes.items():
        if d.to_order:
            continue
        assert taken[dish_id] == d.served, dish_id
        assert by_reason["expired"][dish_id] == d.expired, dish_id
        assert by_reason["close"][dish_id] == d.leftover, dish_id
    assert rec["summary"]["waste_portions"] == sum(d.waste for d in m.dishes.values() if not d.to_order)


def test_weights_are_positive_and_use_serving_sizes():
    cfg = load_baseline("2026-11-13")
    oz = dish_oz(cfg)
    assert all(v > 0 for v in oz.values())
    chowder = next(d for d in cfg.menu if d.serving_size == "5 2/3 OZL")
    assert oz[chowder.id] == pytest.approx(5 + 2 / 3)


def test_over_prep_is_proportional():
    gt = ground_truth(DAY, adopted=False) | {"over_prep": 1.03, "crowd_multiplier": 1.0}
    base, cfg = load_baseline(menu_date_for(DAY)), actual_config(DAY, gt)
    for k, p in base.kitchen.items():
        assert cfg.kitchen[k].starting_batches == p.starting_batches  # never 1 -> 2 pans
        assert cfg.kitchen[k].batch_size == max(1, round(p.batch_size * 1.03))


def test_waste_api_hides_ground_truth(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DCSIM_CACHE", str(tmp_path))
    monkeypatch.setenv("DCSIM_WORKERS", "1")
    from dcsim.api import app

    with TestClient(app) as c:
        r = c.get("/api/waste/history")
        if r.status_code == 404:
            pytest.skip("run `dcsim waste-gen` first")
        body = r.text
        assert "over_prep" not in body and "plate_waste_share" not in body and "crowd_multiplier" not in body
        assert r.json()["kind"] == "recorded_synthetic"
        bt = c.get("/api/waste/backtest")
        if bt.status_code == 200:
            h = bt.json()["headline"]
            assert h["nights"] > 0 and "stockout_min_per_night" in h


def test_bundle_is_self_contained(tmp_path):
    svc = Service(Store(tmp_path / "cache"))
    out = tmp_path / "bundle"
    from dcsim.bundle import build, key

    build(svc, out, dates=["2026-11-13"], n_nights=2, traces="featured", narrate=3, log=lambda _: None)
    m = json.loads((out / "manifest.json").read_text())
    keys = {e["key"]: e for e in m["entries"]}
    assert key("2026-11-13", None) in keys and key("2026-11-13", "theme_night") in keys
    for e in m["entries"]:
        res = json.loads((out / e["file"]).read_text())
        assert res["config_hash"] == e["config_hash"] and res["n_nights"] == 2
        assert ("vs_baseline" in res) == (e["preset"] is not None)
        assert e["trace"], "featured date: every preset gets a trace"
        trace = json.loads(gzip.decompress((out / e["trace"]).read_bytes()))
        assert trace["students"]
        nar = json.loads((out / e["narration"]).read_text())
        assert nar["highlights"] and all(str(x["student_id"]) in nar["students"] for x in nar["highlights"])
    for ref in ("ref/dates.json", "ref/layout.json", "ref/presets.json", "ref/menu/2026-11-13.json",
                "ref/arrivals/2026-11-13.json", "waste/facts.json"):
        assert (out / ref).exists(), ref
