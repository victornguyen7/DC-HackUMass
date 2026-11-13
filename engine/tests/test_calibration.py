import os
from math import comb

import numpy as np
import pytest

from dcsim.calibration import binomial_range, crps, pit, public_report, score, with_inputs
from dcsim.data import load_baseline


def test_crps_matches_brute_force_and_is_zero_for_a_perfect_point_forecast():
    rng = np.random.default_rng(0)
    x, y = rng.normal(10, 3, 40), 12.5
    brute = np.mean(np.abs(x - y)) - 0.5 * np.mean(np.abs(x[:, None] - x[None, :]))
    assert crps(x, y) == pytest.approx(brute)
    assert crps([5.0] * 10, 5.0) == pytest.approx(0.0)
    assert crps(x, 10) < crps(x, 25)  # further from the forecast scores worse


def test_score_counts_nights_inside_the_10_90_range():
    s = score([list(range(101))] * 3, [50, 5, 95])  # p10 = 10, p90 = 90
    assert (s["nights"], s["inside80"]) == (3, 1)
    assert s["pit"][0] == pytest.approx(0.5, abs=0.01)
    assert pit([1, 2, 3], 0) == 0 and pit([1, 2, 3], 9) == 1


def test_binomial_range_is_the_central_95_percent_of_a_calibrated_80_percent_interval():
    n = 14
    lo, hi = binomial_range(n)
    pmf = [comb(n, k) * 0.8**k * 0.2 ** (n - k) for k in range(n + 1)]
    assert sum(pmf[lo : hi + 1]) >= 0.95
    assert sum(pmf[:lo]) < 0.025 and sum(pmf[hi + 1 :]) < 0.025


def test_inputs_snap_to_one_percent_and_scale_batches():
    base = load_baseline()
    cfg = with_inputs(base, 1.034, 1.104)
    assert cfg.crowd.multiplier == pytest.approx(base.crowd.multiplier * 1.03)
    k, p = next(iter(base.kitchen.items()))
    assert cfg.kitchen[k].batch_size == max(1, round(p.batch_size * 1.10))
    assert with_inputs(base) == base


def test_served_report_never_carries_hidden_generator_values():
    r = public_report()
    if r is None:
        pytest.skip("run `dcsim calibrate` first")
    rec = r["recovery"]
    assert "production_mean" not in rec and "crowd_log_sd" not in rec
    assert "true" not in str(rec)


@pytest.mark.skipif(not os.environ.get("DCSIM_TSDB_TEST_URL"),
                    reason="set DCSIM_TSDB_TEST_URL to a scratch database (the test rewrites schema dcsim)")
def test_tsdb_round_trip_and_production_ratio_recovers_batch_scaling():
    from dcsim import tsdb
    from dcsim.synthetic import out_dir

    if not (out_dir() / "nights.csv").exists():
        pytest.skip("run `dcsim waste-gen` first")
    with tsdb.connect(os.environ["DCSIM_TSDB_TEST_URL"]) as conn:
        tsdb.init_schema(conn)
        n = tsdb.load_observed(conn)
        assert n["nights"] == len(tsdb.observed_nights(conn)) > 0
        ratios = tsdb.production_ratios(conn)
        assert len(ratios) == n["nights"]
        assert all(0.95 <= r <= 1.25 for r in ratios.values())  # generator over-prep is 1.00-1.20


def test_adopting_without_tiger_still_works_and_says_so(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DCSIM_CACHE", str(tmp_path))
    monkeypatch.setenv("DCSIM_WORKERS", "1")
    from dcsim import tsdb
    from dcsim.api import app

    assert not tsdb.configured()
    with TestClient(app) as c:
        from test_api import _simulate

        res = _simulate(c, {"n_nights": 2})
        s = c.post("/api/scenarios", json={"name": "x", "config_hash": res["config_hash"], "status": "adopted"}).json()
        assert s["status"] == "adopted" and "not configured" in s["forecast"]
        assert c.get("/api/forecasts").status_code == 503
