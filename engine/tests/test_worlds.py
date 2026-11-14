"""Plausible worlds (IMPROVEMENTS 2.2): per-seed input draws, cache-safe hashing, sweep and tornado."""

from __future__ import annotations

import numpy as np
import pytest

from dcsim import worlds
from dcsim.config import Uncertainty, config_hash
from dcsim.data import load_baseline
from dcsim.presets import PRESETS
from dcsim.runner import run_night
from dcsim.service import Service
from dcsim.store import Store

DATE = "2026-11-13"
U = Uncertainty(crowd_sd=0.1, peak_shift_sd_min=10, popularity_sd=0.3)


@pytest.fixture(scope="module")
def base():
    return load_baseline(DATE)


def test_no_uncertainty_keeps_every_cached_hash(base):
    # Added after configs were cached: an absent field must not change the hash (or the bundle goes stale).
    assert config_hash(base) == config_hash(base.updated(uncertainty=None))
    assert config_hash(base) != config_hash(base.updated(uncertainty=U))
    assert "uncertainty" not in __import__("dcsim.config", fromlist=["x"]).canonical_json(base)


def test_world_draws_are_deterministic_and_vary_by_seed(base):
    a, b = worlds.world_of(base.updated(uncertainty=U), 1), worlds.world_of(base.updated(uncertainty=U), 1)
    assert a == b and a.uncertainty is None
    c = worlds.world_of(base.updated(uncertainty=U), 2)
    assert a.crowd.multiplier != c.crowd.multiplier and a.crowd.shift_min != c.crowd.shift_min
    assert a.kitchen == base.kitchen  # the plan doesn't know the world


def test_plan_and_baseline_face_the_same_world(base):
    plan = PRESETS["smaller_batches"](base)
    wb, wp = worlds.world_of(base.updated(uncertainty=U), 7), worlds.world_of(plan.updated(uncertainty=U), 7)
    assert wb.crowd == wp.crowd and [d.popularity for d in wb.menu] == [d.popularity for d in wp.menu]


def test_a_new_dish_leaves_the_other_draws_alone(base):
    ids = [d.id for d in base.menu]
    one = worlds.draws(U, 3, ids)
    two = worlds.draws(U, 3, ids + ["new_dish"])
    assert one["crowd"] == two["crowd"] and all(one["popularity"][d] == two["popularity"][d] for d in ids)


def test_draws_are_clamped_and_mean_one():
    z = [worlds.draws(Uncertainty(popularity_sd=0.3), s, ["x"])["popularity"]["x"] for s in range(1, 4001)]
    assert abs(np.mean(z) - 1) < 0.03
    crowd = [worlds.draws(Uncertainty(crowd_sd=0.5), s, [])["crowd"] for s in range(1, 2001)]
    assert max(crowd) <= np.exp(0.5 * worlds.Z_CLAMP) + 1e-9


def test_uncertain_night_runs_and_differs(base):
    m0 = run_night(base, 1).metrics
    m1 = run_night(base.updated(uncertainty=U), 1).metrics
    assert m0.students != m1.students


def test_sweep_and_tornado(tmp_path, base):
    svc = Service(Store(tmp_path))
    seen = []
    r = worlds.sweep(svc, base, PRESETS["smaller_batches"](base), U, n_worlds=4, tornado_worlds=3,
                     on_progress=lambda d, t: seen.append((d, t)))
    assert r["n_worlds"] == 4 and r["vs_baseline"]["n_pairs"] == 4
    assert set(r["vs_baseline"]["metrics"]) >= {"waste_total", "stockout_min_total"}
    bars = r["tornado"]["bars"]
    assert {b["input"] for b in bars} == {"seed", "crowd_sd", "peak_shift_sd_min", "popularity_sd"}
    assert [b["width"] for b in bars] == sorted((b["width"] for b in bars), reverse=True)
    assert seen and seen[-1][0] == seen[-1][1] == 4 + 4 + 4 * 3
    assert worlds.sweep(svc, base, base, U, n_worlds=4, tornado_worlds=0)["tornado"] is None
