"""Calibration over time (forecast_results, the nightly scoring job, forecast_weekly) and the crowd
error by weather/event (docs/TIGER.md #4, #5). No database: SQL is checked as text."""

from __future__ import annotations

import numpy as np

from dcsim import calibration as cal
from dcsim import tsdb
from dcsim.tiger_sql import script


def _nights(n: int, rng, effects: dict[str, float], start: int = 0) -> list[dict]:
    """Nights with a known log crowd effect per situation and small noise; expected students = 3000."""
    out = []
    kinds = ["clear", "clear", "rain", "cold", "event"]
    for i in range(n):
        k = kinds[(i + start) % len(kinds)]
        weather = "clear" if k == "event" else k
        logr = effects.get(k, 0.0) + rng.normal(0, 0.03)
        out.append({"night": f"n{start + i}", "menu_date": "m", "adopted": False, "weather": weather,
                    "event_night": k == "event", "crowd_ratio": float(np.exp(logr)),
                    "students": 3000 * float(np.exp(logr))})
    return out


def test_situational_fit_recovers_effects_and_is_kept_when_it_wins_on_held_out_nights():
    rng = np.random.default_rng(1)
    true = {"rain": -0.08, "cold": -0.04, "event": 0.14}
    train, test = _nights(40, rng, true), _nights(15, rng, true, start=40)
    sit = cal.fit_situations(train, seed_sd=0.0)
    # shrunk towards 0 by about prior / (nights + prior): 8 nights per situation here, prior 4
    for s in ("rain", "event"):
        assert 0.5 * abs(true[s]) < abs(sit["effects"][s]) < 1.05 * abs(true[s]) and np.sign(sit["effects"][s]) == np.sign(true[s])
    assert sit["effects"]["snow"] == 0.0 and sit["train_nights"]["snow"] == 0   # no nights: no effect
    pooled_logr = np.log([o["crowd_ratio"] for o in train])
    crowd = {"log_mean": float(pooled_logr.mean()), "log_sd": float(pooled_logr.std(ddof=1))}
    out = cal.with_situations(crowd, train, test, {("m", False): [3000.0] * 20}, seed_sd=0.0)["situational"]
    assert out["kept"] and out["held_out"]["situational"]["crps"] < out["held_out"]["pooled"]["crps"]


def test_shrinkage_keeps_a_situation_seen_on_few_nights_near_zero():
    rng = np.random.default_rng(2)
    train = _nights(30, rng, {})
    train[0] |= {"weather": "snow", "crowd_ratio": 0.7}  # one odd snowy night
    sit = cal.fit_situations(train, seed_sd=0.0)
    assert -0.1 < sit["effects"]["snow"] < 0 and abs(sit["effects"]["snow"]) < abs(np.log(0.7)) / 3


def test_situational_draws_only_when_kept_and_a_situation_is_given():
    sit = {"intercept": 0.0, "effects": {"rain": np.log(0.9), "cold": 0.0, "snow": 0.0, "event": 0.0},
           "log_sd": 0.05, "kept": True}
    params = {"crowd": {"situational": sit}}
    rain = cal.situational_draws(params, {"weather": "rain", "event_night": False})
    clear = cal.situational_draws(params, {"weather": "clear", "event_night": False})
    assert len(rain) == len(cal.CROWD_Q) and np.median(rain) == np.float64(round(0.9, 4))
    assert all(r < c for r, c in zip(rain, clear))
    assert cal.situational_draws(params, None) is None
    assert cal.situational_draws({"crowd": {"situational": sit | {"kept": False}}}, {"weather": "rain"}) is None
    assert cal.situational_draws({"crowd": {}}, {"weather": "rain"}) is None


def test_track_record_sql_scores_backtest_and_ledger_and_rolls_up_by_week():
    proc = tsdb.SCORE_PROC
    assert "dcsim.forecast_check" in proc and "dcsim.forecast_scorecard" in proc
    assert "issued_before_open" in proc and "'awaiting'" in proc           # only forecasts made before the night
    assert "DELETE FROM dcsim.forecast_results WHERE origin = 'backtest'" in proc
    assert "ON CONFLICT (night, origin, method)" in proc
    assert "by_range('night'" in tsdb.TRACK_HYPERTABLE
    cagg = tsdb.TRACK_CAGG
    assert "timescaledb.continuous" in cagg and "timescaledb.materialized_only = false" in cagg
    assert f"DATE '{tsdb.TRACK_WEEK_ORIGIN}'" in cagg and "add_continuous_aggregate_policy('dcsim.forecast_weekly'" in cagg
    assert "add_job('dcsim.score_forecasts'" in cagg and "proc_name = 'score_forecasts'" in cagg  # added once
    assert "time_bucket" not in tsdb.TRACK_PLAIN_VIEW and "dcsim.forecast_weekly" in tsdb.TRACK_PLAIN_VIEW
    assert "forecast_weekly" in tsdb.QUERIES["track"][1]


def test_forecasts_record_the_situation_they_assumed():
    assert {"weather", "event_night"} <= set(tsdb.FORECAST_COLS)
    assert ("weather", "text") in tsdb.NEW_COLUMNS["forecasts"]


def test_console_script_orders_track_record_after_what_it_reads_and_comments_last():
    sql = script()
    assert sql.index("CREATE TABLE IF NOT EXISTS dcsim.forecast_results") < sql.index("by_range('night'")
    assert sql.index("VIEW dcsim.forecast_scorecard") < sql.index("PROCEDURE dcsim.score_forecasts")
    assert sql.index("VIEW IF NOT EXISTS dcsim.forecast_weekly") < sql.index("COMMENT ON VIEW dcsim.forecast_weekly")
    for t in ("nights", "pan_windows", "waste_log", "forecasts", "forecast_results"):
        assert f"COMMENT ON TABLE dcsim.{t} IS" in sql, t
