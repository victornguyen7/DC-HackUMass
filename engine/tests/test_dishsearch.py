"""Dish search and the dish-popularity check (docs/TIGER.md section 9). No database: the vectors and the
estimator are pure functions; the Tiger path is covered by `dcsim tsdb dishes` against a live service."""

from __future__ import annotations

import numpy as np
import pytest

from dcsim import dishsearch as ds
from dcsim import worlds
from dcsim.config import Uncertainty, canonical_json, config_hash
from dcsim.data import load_baseline

DATE = "2026-11-13"


def _cos(a, b) -> float:
    return 1 - float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def test_vector_size_and_blocks():
    v = ds.embed("Butter Chicken", "tandoor", "entree", "dinner", ("halal",), ("Milk",), True, "plate")
    assert v.shape == (ds.DIM,)
    assert np.linalg.norm(v) == pytest.approx(np.sqrt(sum(w * w for w in ds.WEIGHTS.values())))
    # unknown fields are zero blocks: a name-only query still has a direction
    assert np.linalg.norm(ds.embed("Butter Chicken", None, None, None)) == pytest.approx(ds.WEIGHTS["name"])
    assert np.allclose(v, ds.embed("butter  CHICKEN", "tandoor", "entree", "dinner", ("halal",), ("milk",), True, "plate"))


def test_same_station_and_role_is_closer():
    q = ds.embed("Chicken Tikka", "tandoor", "entree")
    same = ds.embed("Tandoori Chicken", "tandoor", "entree")
    other = ds.embed("Tandoori Chicken", "soups", "soup")
    assert _cos(q, q) == pytest.approx(0, abs=1e-9)
    assert _cos(q, same) < _cos(q, other)


def _fixture(tau: float, sigma: float = 0.1, dishes: int = 60, nights: int = 8, seed: int = 7):
    rng = np.random.default_rng(seed)
    truth = {f"d{i}": float(rng.normal(0, tau)) for i in range(dishes)}
    lr = {d: list(t + rng.normal(0, sigma, nights)) for d, t in truth.items()}
    return truth, lr


def test_popularity_check_recovers_a_known_spread():
    truth, lr = _fixture(tau=0.25)
    r = ds.popularity_check(lr)
    assert r["detected"] and abs(r["tau"] - 0.25) <= 0.07
    assert r["sigma"] == pytest.approx(0.1, abs=0.02)
    est = [np.log(r["factors"][d]["factor"]) for d in truth]
    assert np.corrcoef(est, list(truth.values()))[0, 1] > 0.8
    # shrinkage: estimates sit closer to 1 than the raw means
    assert all(abs(np.log(f["factor"])) <= abs(np.log(f["raw"])) + 1e-9 for f in r["factors"].values())


def test_popularity_check_finds_nothing_when_there_is_nothing():
    _, lr = _fixture(tau=0.0)
    r = ds.popularity_check(lr)
    assert not r["detected"] and r["tau"] < ds.DETECT_TAU
    assert "synthetic" in r["note"]
    assert all(f["factor"] == pytest.approx(1, abs=0.02) for f in r["factors"].values())
    assert ds.popularity_check({"a": [0.1]})["detected"] is False  # too little history


def test_summarize_skips_thin_replays():
    rows = [("a", "2026-10-01", 50, 40, 1000), ("a", "2026-10-02", 3, 5, 1000), ("b", "2026-10-01", 20, None, None)]
    s = ds.summarize(rows)
    assert s["a"]["nights"] == 2 and len(s["a"]["log_ratio"]) == 1  # replay < MIN_REPLAY_EATEN dropped
    assert s["a"]["per_student"] == [0.05, 0.003] and s["b"]["per_student"] == [] and s["b"]["log_ratio"] == []


def test_new_dish_sd_grows_with_look_alike_spread():
    assert ds.new_dish_sd([0.05, 0.05], 0.3) == (0.3, None)  # too few look-alikes: default
    tight, s1 = ds.new_dish_sd([0.05, 0.051, 0.049], 0.3)
    wide, s2 = ds.new_dish_sd([0.02, 0.05, 0.12], 0.3)
    assert 0.3 < tight < wide and s1 < s2


def test_new_dish_spread_keeps_hashes_and_only_moves_new_dishes():
    base = load_baseline(DATE)
    u = Uncertainty(crowd_sd=0.1, peak_shift_sd_min=10, popularity_sd=0.3)
    assert "new_dish_popularity_sd" not in canonical_json(base.updated(uncertainty=u))
    assert config_hash(base.updated(uncertainty=u)) == config_hash(
        base.updated(uncertainty=u.model_copy(update={"new_dish_popularity_sd": None})))
    ids = [d.id for d in base.menu]
    a = worlds.draws(u, 3, ids, new_dishes=[ids[0]])["popularity"]  # sd not set: new dishes use popularity_sd
    b = worlds.draws(u.model_copy(update={"new_dish_popularity_sd": 0.9}), 3, ids, new_dishes=[ids[0]])["popularity"]
    assert a[ids[0]] != b[ids[0]] and all(a[d] == b[d] for d in ids[1:])
    menu = [d.model_dump() | ({"estimate": True} if d.id == ids[0] else {}) for d in base.menu]
    cfg = base.updated(menu=menu, uncertainty=u.model_copy(update={"new_dish_popularity_sd": 0.9}))
    w = worlds.world_of(cfg, 3)
    assert w.uncertainty is None and len(w.menu) == len(base.menu)


def test_dish_evidence_route_without_tiger():
    from fastapi.testclient import TestClient

    from dcsim.api import app

    c = TestClient(app)
    r = c.post("/api/dish-evidence", json={"name": "Butter Chicken", "role": "entree"})
    assert r.status_code == 503 and "Tiger" in r.json()["detail"]
    assert c.post("/api/dish-evidence", json={"name": ""}).status_code == 422
