"""Calibrate on the first weeks of recorded nights, then score forecasts on held-out weeks.

The back-test (backtest.py) re-simulates every recorded night with the crowd it
actually had, so it can't tell whether the engine's *ranges* are honest. This
module asks the question a manager cares about: before a night, does the
predicted 10-90% range contain what then happens about 80% of the time?

Protocol (observed numbers are read from Tiger Data, dcsim.nights / dcsim.pan_windows):

1. Split by date: train = nights before TEST_FROM (4 weeks), test = the rest (2 weeks).
2. Fit, on train nights only, the two things a kitchen records that the engine doesn't know:
   - crowd forecast error: observed students / the engine's expected students (meal swipes),
     a lognormal; the engine's own night-to-night spread is subtracted so it isn't counted twice;
   - production ratio: how much bigger the kitchen's batches were than the plan's, read off the pans
     in SQL (after the opening stock, the smallest refill a dish gets in one window is one batch).
     Nights that followed an adopted plan are left out of the fit (staff behave differently on them).
3. Forecast every test night two ways, before the night:
   - forecast_naive: plan as written, expected crowd, seeds only (what the app shows today);
   - forecast_calibrated: crowd and production ratio drawn from the fitted distributions.
4. Score both on the test nights: 10-90% coverage (target 80%), bias, MAE, CRPS, PIT.
5. Replay every night (plan as executed, that night's observed crowd and production ratio) with
   pan windows, so observed and simulated pans can be compared window by window in SQL.
6. Recovery check (synthetic data only): compare the fitted values with the generator's hidden
   ground truth. This tests the pipeline, not the engine; on real exports the step is skipped.

All simulated nights, the fitted parameters and the scores are written back to Tiger.
"""

from __future__ import annotations

import datetime as dt
import json
from concurrent.futures import Executor
from pathlib import Path
from statistics import NormalDist
from uuid import uuid4

import numpy as np

from .config import ENGINE_VERSION, ScenarioConfig
from .data import load_baseline
from .metrics import NightMetrics, compute_metrics
from .model import Night
from .presets import PRESETS
from .service import Service
from .synthetic import RECOMMENDED_PLAN, _over_prep, dish_oz, out_dir, pan_windows
from . import tsdb

NAIVE_SEEDS = 20
CAL_SEEDS = 4
CROWD_Q = (0.1, 0.3, 0.5, 0.7, 0.9)  # stratified draws from the fitted crowd error
PROD_Q = (1 / 6, 0.5, 5 / 6)  # and from the training nights' production ratios
REPLAY_SEEDS = 10
METRICS = ("waste_lb", "stockout_min", "students", "cooked_portions")


# ------------------------------------------------------------------ configs

def executed_plan(menu_date: str, adopted: bool, plan: str = RECOMMENDED_PLAN) -> ScenarioConfig:
    base = load_baseline(menu_date)
    return PRESETS[plan](base) if adopted else base


def with_inputs(cfg: ScenarioConfig, crowd: float = 1.0, production: float = 1.0) -> ScenarioConfig:
    """Crowd multiplier and production ratio, snapped to 1% so repeated runs hit the cache."""
    crowd, production = round(crowd, 2), round(production, 2)
    if crowd != 1.0:
        cfg = cfg.updated(**cfg.crowd_scaled(crowd))
    return _over_prep(cfg, production) if production != 1.0 else cfg


def night_values(cfg: ScenarioConfig, m: NightMetrics, oz: dict[str, float] | None = None) -> dict:
    oz = oz or dish_oz(cfg)
    batch = [(k, d) for k, d in m.dishes.items() if not d.to_order]
    return {
        "students": m.students,
        "cooked_portions": sum(d.cooked for _, d in batch),
        "waste_portions": sum(d.waste for _, d in batch),
        "waste_lb": round(sum(d.waste * oz[k] / 16 for k, d in batch), 2),
        "expired_lb": round(sum(d.expired * oz[k] / 16 for k, d in batch), 2),
        "close_lb": round(sum(d.leftover * oz[k] / 16 for k, d in batch), 2),
        "stockout_min": m.stockout_min_total,
    }


# ------------------------------------------------------------------ scoring

def crps(sample, y: float) -> float:
    """Continuous ranked probability score of a sample forecast (lower is better; same unit as y)."""
    x = np.sort(np.asarray(sample, dtype=float))
    n = len(x)
    # E|X - X'| from the sorted sample in O(n): sum_i (2i - n + 1) x_i * 2 / n^2
    spread = 2.0 * np.sum((2 * np.arange(n) - n + 1) * x) / (n * n)
    return float(np.mean(np.abs(x - y)) - 0.5 * spread)


def pit(sample, y: float) -> float:
    x = np.asarray(sample, dtype=float)
    return float((np.sum(x < y) + 0.5 * np.sum(x == y)) / len(x))


def score(samples: list[list[float]], observed: list[float]) -> dict:
    """Interval coverage and proper scores over a set of nights (one sample per night)."""
    inside, err, pits, cr = [], [], [], []
    for s, y in zip(samples, observed):
        p10, p50, p90 = np.percentile(s, [10, 50, 90])
        inside.append(bool(p10 <= y <= p90))
        err.append(y - p50)
        pits.append(round(pit(s, y), 3))
        cr.append(crps(s, y))
    n = len(observed)
    return {
        "nights": n,
        "inside80": int(sum(inside)),
        "coverage80": round(sum(inside) / n, 3) if n else None,
        "bias": round(float(np.mean(err)), 2) if n else None,  # observed minus predicted median
        "mae": round(float(np.mean(np.abs(err))), 2) if n else None,
        "crps": round(float(np.mean(cr)), 2) if n else None,
        "pit": pits,
    }


def binomial_range(n: int, p: float = 0.8, mass: float = 0.95) -> tuple[int, int]:
    """Counts of nights inside the range that a perfectly calibrated 80% interval would give, 95% of the time."""
    from math import comb

    pmf = [comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(n + 1)]
    lo, hi, acc = 0, n, 0.0
    while acc + pmf[lo] < (1 - mass) / 2:
        acc += pmf[lo]
        lo += 1
    acc = 0.0
    while acc + pmf[hi] < (1 - mass) / 2:
        acc += pmf[hi]
        hi -= 1
    return lo, hi


# ------------------------------------------------------------------ replay worker (needs trace=True for pans)

def _replay_night(args: tuple[ScenarioConfig, int, str]) -> tuple[NightMetrics, list[dict]]:
    cfg, seed, night = args
    n = Night(cfg, seed, trace=True).run()
    rows, _ = pan_windows(n, dt.date.fromisoformat(night))
    return compute_metrics(n), rows


# ------------------------------------------------------------------ protocol

def calibrate(svc: Service, conn, pool: Executor | None = None, test_from: dt.date = tsdb.TEST_FROM,
              log=print) -> dict:
    obs = tsdb.observed_nights(conn)
    if not obs:
        raise RuntimeError("no observed nights in Tiger; run `dcsim tsdb load` first")
    for o in obs:
        o["split"] = tsdb.split_of(o["night"], test_from)
    train = [o for o in obs if o["split"] == "train"]
    test = [o for o in obs if o["split"] == "test"]
    if len(train) < 7 or not test:
        raise RuntimeError(f"need >= 7 training and >= 1 test nights (have {len(train)} / {len(test)})")
    log(f"{len(train)} training nights ({train[0]['night']}..{train[-1]['night']}), "
        f"{len(test)} held-out ({test[0]['night']}..{test[-1]['night']})")

    # 1. naive forecast per (menu, plan): also the engine's expected students
    naive: dict[tuple[str, bool], list[dict]] = {}
    for key in sorted({(o["menu_date"], o["adopted"]) for o in obs}):
        cfg = executed_plan(*key)
        naive[key] = [night_values(cfg, m) for m in svc.ensure_nights(cfg, NAIVE_SEEDS)]
    expected = {k: float(np.median([v["students"] for v in vs])) for k, vs in naive.items()}
    seed_sd = float(np.mean([np.std(np.log([v["students"] for v in vs]), ddof=1) for vs in naive.values()]))
    log(f"naive forecast: {len(naive)} menu/plan configs x {NAIVE_SEEDS} seeds")

    # 2a. crowd forecast error (all training nights)
    for o in obs:
        o["crowd_ratio"] = o["students"] / expected[(o["menu_date"], o["adopted"])]
    logr = np.log([o["crowd_ratio"] for o in train])
    mu, sd_total = float(np.mean(logr)), float(np.std(logr, ddof=1))
    sd = float(np.sqrt(max(sd_total**2 - seed_sd**2, 0.0)))
    crowd = {"log_mean": round(mu, 4), "log_sd": round(sd, 4), "log_sd_observed": round(sd_total, 4),
             "log_sd_engine_seeds": round(seed_sd, 4), "nights": len(train)}
    log(f"crowd: observed/expected median {np.exp(mu):.3f}, sd {sd:.3f} (log; engine seeds add {seed_sd:.3f})")

    # 2b. production ratio, read off each night's pans in SQL (tsdb.PRODUCTION_RATIO). Observable
    # after every night; only non-adopted training nights shape the forecast distribution.
    measured = tsdb.production_ratios(conn)
    for o in obs:
        o["production"] = round(measured.get(o["night"], 1.0), 2)
    fit_nights = [o for o in train if not o["adopted"]]
    ratios = np.array([o["production"] for o in fit_nights])
    adopted_ratios = [o["production"] for o in obs if o["adopted"]]
    production = {"mean": round(float(ratios.mean()), 4), "sd": round(float(ratios.std(ddof=1)), 4),
                  "median": round(float(np.median(ratios)), 4),
                  "quantiles": {f"{q:.3f}": round(float(np.quantile(ratios, q)), 4) for q in PROD_Q},
                  "nights": len(fit_nights), "method": "smallest refill / planned batch size, median over dishes",
                  "adopted_nights_observed": adopted_ratios}
    log(f"production ratio (pans vs plan): train median {production['median']:.3f}, sd {production['sd']:.3f}"
        + (f"; adopted nights {', '.join(f'{r:.2f}' for r in adopted_ratios)}" if adopted_ratios else ""))
    prod_draws = [production["quantiles"][f"{q:.3f}"] for q in PROD_Q]
    crowd_draws = [float(np.exp(mu + sd * NormalDist().inv_cdf(q))) for q in CROWD_Q]

    # 3. forecasts for held-out nights
    rows: list[dict] = []
    samples: dict[str, dict[str, list[list[float]]]] = {s: {k: [] for k in METRICS} for s in ("forecast_naive", "forecast_calibrated")}
    adopted_flags = []

    def row(o: dict, scenario: str, draw: int, seed: int, cfg: ScenarioConfig, vals: dict, crowd_m: float, prod: float):
        return {"ts": tsdb.at(o["night"], cfg.open_min), "night": o["night"], "source": "simulated",
                "scenario": scenario, "run_id": f"{scenario}:{o['night']}", "draw": draw, "seed": seed,
                "menu_date": o["menu_date"], "plan": "recommended" if o["adopted"] else "baseline",
                "weekday": o["weekday"], "weather": o["weather"], "event_night": o["event_night"],
                "adopted": o["adopted"], "split": o["split"], "crowd_multiplier": round(crowd_m, 4),
                "over_prep": prod, **vals}

    for o in obs:  # naive rows for every night (cheap: same configs per weekday menu)
        cfg = executed_plan(o["menu_date"], o["adopted"])
        for s, v in enumerate(naive[(o["menu_date"], o["adopted"])], start=1):
            rows.append(row(o, "forecast_naive", 0, s, cfg, v, 1.0, 1.0))
        if o["split"] == "test":
            for k in METRICS:
                samples["forecast_naive"][k].append([v[k] for v in naive[(o["menu_date"], o["adopted"])]])

    for o in test:
        adopted_flags.append(o["adopted"])
        base = executed_plan(o["menu_date"], o["adopted"])
        oz = dish_oz(base)
        draw_vals: list[dict] = []
        d = 0
        for c in crowd_draws:
            for p in prod_draws:
                cfg = with_inputs(base, c, p)
                for m in svc.ensure_nights(cfg, CAL_SEEDS):
                    v = night_values(cfg, m, oz)
                    draw_vals.append(v)
                    rows.append(row(o, "forecast_calibrated", d, m.seed, cfg, v, round(c, 2), round(p, 2)))
                d += 1
        for k in METRICS:
            samples["forecast_calibrated"][k].append([v[k] for v in draw_vals])
    log(f"calibrated forecast: {len(test)} nights x {len(crowd_draws) * len(prod_draws)} draws x {CAL_SEEDS} seeds")

    # 4. scores
    truth = {k: [float(o[k]) for o in test] for k in METRICS}
    scores: dict = {}
    for scen, by_metric in samples.items():
        scores[scen] = {}
        for k in METRICS:
            scores[scen][k] = score(by_metric[k], truth[k])
            for label, keep in (("adopted", True), ("not_adopted", False)):
                idx = [i for i, a in enumerate(adopted_flags) if a == keep]
                if idx:
                    sub = score([by_metric[k][i] for i in idx], [truth[k][i] for i in idx])
                    scores[scen][k][label] = {x: sub[x] for x in ("nights", "inside80", "coverage80", "bias", "crps")}
    scores["expected_inside80_if_calibrated"] = list(binomial_range(len(test)))

    # 5. replay every night with pan windows (hindcast: crowd known)
    replay_args, replay_meta = [], []
    for o in obs:
        cfg = with_inputs(executed_plan(o["menu_date"], o["adopted"]), o["crowd_ratio"], o["production"])
        for s in range(1, REPLAY_SEEDS + 1):
            replay_args.append((cfg, s, o["night"]))
            replay_meta.append(o)
    results = list(pool.map(_replay_night, replay_args, chunksize=4)) if pool else [_replay_night(a) for a in replay_args]
    pan_rows, replay_samples = [], {}
    for (cfg, s, night), o, (m, pans) in zip(replay_args, replay_meta, results):
        v = night_values(cfg, m)
        rows.append(row(o, "replay", 0, s, cfg, v, round(o["crowd_ratio"], 2), o["production"]))
        replay_samples.setdefault(night, []).append(v)
        for p in pans:
            pan_rows.append({"ts": tsdb.at(night, tsdb.hhmm_to_min(p["window_start"])), "night": night,
                             "source": "simulated", "scenario": "replay", "run_id": f"replay:{night}", "seed": s,
                             "station": p["station"], "dish_id": p["dish_id"], "dish": p["dish"],
                             "portions_out": p["portions_out"], "portions_eaten": p["portions_eaten"],
                             "portions_left": p["portions_left"]})
    scores["replay"] = {k: score([[v[k] for v in replay_samples[o["night"]]] for o in test], truth[k])
                        for k in ("waste_lb", "stockout_min", "cooked_portions")}
    log(f"replay: {len(obs)} nights x {REPLAY_SEEDS} seeds, {len(pan_rows)} pan windows")

    # 6. recovery check against the generator's hidden values (synthetic data only)
    recovery = recovery_check(train, fit_nights, crowd)

    # write everything back to Tiger
    for scen in ("forecast_naive", "forecast_calibrated", "replay"):
        tsdb.delete_scenario(conn, scen)
    conn.execute("UPDATE dcsim.nights SET split = CASE WHEN night >= %s THEN 'test' ELSE 'train' END WHERE run_id = 'observed'",
                 (test_from,))
    tsdb.copy_rows(conn, "nights", tsdb.NIGHT_COLS, rows)
    tsdb.copy_rows(conn, "pan_windows", tsdb.PAN_COLS, pan_rows)
    cal_id = f"cal-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}"
    params = {"crowd": crowd, "production": production,
              "draws": {"crowd": [round(c, 4) for c in crowd_draws], "production": prod_draws},
              "seeds": {"naive": NAIVE_SEEDS, "calibrated": CAL_SEEDS, "replay": REPLAY_SEEDS},
              "per_night_production": {o["night"]: o["production"] for o in fit_nights}}
    from psycopg.types.json import Jsonb

    conn.execute(
        "INSERT INTO dcsim.calibrations (calibration_id, train_from, train_to, test_from, test_to, engine_version,"
        " params, scores, recovery) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (cal_id, train[0]["night"], train[-1]["night"], test[0]["night"], test[-1]["night"], ENGINE_VERSION,
         Jsonb(params), Jsonb(scores), Jsonb(recovery)))
    conn.commit()
    tsdb.refresh(conn)

    report = {
        "kind": "calibration_report",
        "_note": ("SYNTHETIC recorded history. Fit on the training weeks only; scores are on held-out nights the fit "
                  "never saw. The recovery check is possible only because the data is synthetic."),
        "calibration_id": cal_id, "engine_version": ENGINE_VERSION,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "split": {"train": [train[0]["night"], train[-1]["night"], len(train)],
                  "test": [test[0]["night"], test[-1]["night"], len(test)]},
        "params": params, "scores": scores, "recovery": recovery,
        "nights": [{"night": o["night"], "weekday": o["weekday"], "weather": o["weather"], "adopted": o["adopted"],
                    "observed": {k: o[k] for k in METRICS},
                    **{s: {k: dict(zip(("p10", "p50", "p90"), np.round(np.percentile(samples[s][k][i], [10, 50, 90]), 1).tolist()))
                           for k in ("waste_lb", "stockout_min", "students")} for s in samples}}
                   for i, o in enumerate(test)],
    }
    report_path().write_text(json.dumps(report, indent=1))
    return report


def recovery_check(train: list[dict], fit_nights: list[dict], crowd: dict) -> dict:
    p = out_dir() / "_ground_truth.json"
    if not p.exists():
        return {"available": False}
    gt = {n["date"]: n for n in json.loads(p.read_text())["nights"]}
    if not all(o["night"] in gt for o in train):
        return {"available": False}
    true_logc = np.log([gt[o["night"]]["crowd_multiplier"] for o in train])
    est = np.array([o["production"] for o in fit_nights])
    true = np.array([gt[o["night"]]["over_prep"] for o in fit_nights])
    return {
        "available": True,
        "_note": "Pipeline self-test: fitted values vs the synthetic generator's hidden ones. Says nothing about real kitchens.",
        "crowd_log_sd": {"fitted": crowd["log_sd"], "true": round(float(np.std(true_logc, ddof=1)), 4)},
        "crowd_log_mean": {"fitted": crowd["log_mean"], "true": round(float(np.mean(true_logc)), 4)},
        "production_mean": {"fitted": round(float(est.mean()), 4), "true": round(float(true.mean()), 4)},
        "production_per_night": {"mae": round(float(np.mean(np.abs(est - true))), 4),
                                 "corr": round(float(np.corrcoef(est, true)[0, 1]), 3), "nights": len(est)},
    }


def report_path() -> Path:
    return out_dir() / "calibration_report.json"


def public_report() -> dict | None:
    """The report as the API and the demo bundle serve it: the recovery check keeps only how well the
    fit matched (error, correlation), never the generator's hidden values themselves."""
    p = report_path()
    if not p.exists():
        return None
    r = json.loads(p.read_text())
    rec = r.get("recovery", {})
    r["recovery"] = {"available": rec.get("available", False), "_note": rec.get("_note"),
                     **({"production_per_night": rec["production_per_night"],
                         "production_mean_error": round(rec["production_mean"]["fitted"] - rec["production_mean"]["true"], 4),
                         "crowd_log_sd_error": round(rec["crowd_log_sd"]["fitted"] - rec["crowd_log_sd"]["true"], 4)}
                        if rec.get("available") else {})}
    return r


# ------------------------------------------------------------------ forecast ledger (before the night)

def _quantiles(vals: list[dict], key: str) -> dict:
    p10, p50, p90 = np.percentile([v[key] for v in vals], [10, 50, 90])
    return {"p10": round(float(p10), 1), "p50": round(float(p50), 1), "p90": round(float(p90), 1)}


def forecast_samples(svc: Service, cfg: ScenarioConfig, params: dict) -> dict[str, list[dict]]:
    """Naive (seeds only) and calibrated (latest fitted crowd and production draws) samples for one night."""
    oz = dish_oz(cfg)
    naive = [night_values(cfg, m, oz) for m in svc.ensure_nights(cfg, params["seeds"]["naive"])]
    cal = [night_values(c, m, oz)
           for crowd in params["draws"]["crowd"] for prod in params["draws"]["production"]
           for c in [with_inputs(cfg, crowd, prod)]
           for m in svc.ensure_nights(c, params["seeds"]["calibrated"])]
    return {"naive": naive, "calibrated": cal}


def issue_forecasts(svc: Service, conn, cfg: ScenarioConfig, label: str | None = None,
                    scenario_id: str | None = None) -> list[dict]:
    """Log a naive and a calibrated forecast for cfg's night in the append-only ledger (dcsim.forecasts)."""
    from .config import config_hash

    cal = tsdb.latest_calibration(conn)
    if cal is None:
        raise RuntimeError("no calibration in Tiger yet; run `dcsim calibrate` first")
    h = config_hash(cfg)
    out = []
    for method, vals in forecast_samples(svc, cfg, cal["params"]).items():
        q = {k: _quantiles(vals, k) for k in METRICS}
        row = {"forecast_id": f"fc-{cfg.date}-{method}-{uuid4().hex[:8]}", "for_night": cfg.date, "method": method,
               "label": label or cfg.label, "config_hash": h, "scenario_id": scenario_id,
               "calibration_id": cal["calibration_id"], "engine_version": ENGINE_VERSION, "n_nights": len(vals),
               **{f"{short}_{p}": q[k][p] for k, short in (("students", "students"), ("waste_lb", "waste"),
                                                            ("stockout_min", "stockout"), ("cooked_portions", "cooked"))
                  for p in ("p10", "p50", "p90")},
               "samples": {k: [v[k] for v in vals] for k in ("waste_lb", "stockout_min", "students")}}
        out.append(tsdb.insert_forecast(conn, row) | {"method": method, "for_night": cfg.date, "waste_lb": q["waste_lb"],
                                                      "stockout_min": q["stockout_min"]})
    conn.commit()
    return out


def observe_night(conn, night: str) -> dict:
    """Generate the SYNTHETIC observation for `night` under the plan its latest forecast assumed, and load it.

    Stands in for tonight's MetaFoodX / LeanPath export so the ledger can be scored in a demo.
    """
    from .config import config_hash
    from .store import Store
    from .synthetic import ground_truth, simulate_night

    day = dt.date.fromisoformat(night)
    base = load_baseline(night)
    h = tsdb.latest_forecast_config(conn, night)
    plan = (Store().get_config(h) if h else None) or base
    adopted = config_hash(plan) != config_hash(base)
    gt = ground_truth(day, adopted)
    result = simulate_night(day, gt, plan)
    label = "baseline" if not adopted else f"cfg:{config_hash(plan)[:12]}"
    return tsdb.load_live_night(conn, result, plan, gt, label) | {"plan": plan.label, "adopted": adopted}
