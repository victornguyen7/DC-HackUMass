"""Plausible worlds (IMPROVEMENTS 2.2): what if the inputs we guessed are off?

The crowd size, the arrival peak and each dish's popularity are guesses (data/worcester/ASSUMPTIONS.md).
With `ScenarioConfig.uncertainty`, every seed is also one plausible world: those inputs are drawn per
seed from their own random stream (Stream.WORLD), keyed by the seed and the dish id. Two configs with
the same uncertainty therefore face the same worlds, so the paired comparison stays fair, and the
results cache works as usual (one config + N seeds = N worlds).

    sweep(svc, baseline, plan, DEFAULT)   # "the plan beats today's plan in 82% of plausible worlds"
    tornado(svc, baseline, DEFAULT)       # which guess moves waste the most -> which data to ask for
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np

from .config import ScenarioConfig, Uncertainty
from .rng import Stream, std_normal

DEFAULT = Uncertainty(crowd_sd=0.10, peak_shift_sd_min=10.0, popularity_sd=0.3)
Z_CLAMP = 2.5  # no world further out than 2.5 sd (keeps the crowd under the arrival cap)

# Each input -> what it means and which data request would narrow it (data/sources/audit/data_requests.json).
INPUTS = {
    "crowd_sd": ("Crowd size", "R01 attendance export"),
    "peak_shift_sd_min": ("Rush timing", "R01 time-binned admissions"),
    "popularity_sd": ("Dish popularity", "R04 consumption by dish"),
}
SEED_ONLY = ("Night-to-night only", "nothing: this is the randomness that stays")


def _z(seed: int, *keys: object) -> float:
    return float(np.clip(std_normal(seed, Stream.WORLD, *keys), -Z_CLAMP, Z_CLAMP))


def draws(u: Uncertainty, seed: int, dish_ids) -> dict:
    """This seed's world: crowd factor, peak shift (min) and a popularity factor per dish.
    Each draw has its own key, so changing the menu never moves the crowd or another dish."""
    return {
        "crowd": math.exp(u.crowd_sd * _z(seed, "crowd")) if u.crowd_sd else 1.0,
        "shift": u.peak_shift_sd_min * _z(seed, "peak_shift") if u.peak_shift_sd_min else 0.0,
        # mean-one lognormal: exp(sd z - sd^2/2)
        "popularity": {d: math.exp(u.popularity_sd * _z(seed, "popularity", d) - u.popularity_sd ** 2 / 2)
                       for d in dish_ids} if u.popularity_sd else {},
    }


def world_of(cfg: ScenarioConfig, seed: int) -> ScenarioConfig:
    """`cfg` with this seed's inputs drawn. The kitchen plan is untouched: it doesn't know the world."""
    u = cfg.uncertainty
    if u is None:
        return cfg
    w = draws(u, seed, [d.id for d in cfg.menu])
    changes: dict = {"uncertainty": None}
    if w["crowd"] != 1.0:
        changes |= cfg.crowd_scaled(w["crowd"])
    if w["shift"]:
        crowd = changes.get("crowd", cfg.crowd.model_dump())
        changes["crowd"] = crowd | {"shift_min": cfg.crowd.shift_min + w["shift"]}
    if w["popularity"]:
        changes["menu"] = [d.model_dump() | {"popularity": d.popularity * w["popularity"][d.id]} for d in cfg.menu]
    return cfg.updated(**changes)


# ---------------------------------------------------------------- runs

Progress = Callable[[int, int], None]


def _run_all(svc, jobs: list[tuple[ScenarioConfig, int]], on_progress: Progress | None) -> list[list]:
    """ensure_nights for several configs, with one progress count across all of them."""
    todo = [(cfg, svc.batches(cfg, n)) for cfg, n in jobs]
    total, done = sum(len(b) for _, bs in todo for b in bs), 0
    for cfg, batches in todo:
        for b in batches:
            svc.run_batch(cfg, b)
            done += len(b)
            if on_progress:
                on_progress(done, total)
    return [svc.cached_nights(cfg, n) for cfg, n in jobs]


def _band(values) -> dict:
    p10, p50, p90 = np.percentile(np.asarray(values, float), [10, 50, 90])
    return {"p10": round(float(p10), 1), "p50": round(float(p50), 1), "p90": round(float(p90), 1)}


def tornado_jobs(baseline: ScenarioConfig, u: Uncertainty) -> list[tuple[str, ScenarioConfig]]:
    """One config per input (only that input uncertain), plus the plain baseline (seed noise only)."""
    out = [("seed", baseline.updated(uncertainty=None))]
    for field in INPUTS:
        if getattr(u, field):
            out.append((field, baseline.updated(uncertainty=Uncertainty(**{field: getattr(u, field)}))))
    return out


def tornado_bars(named_nights: list[tuple[str, list]], metric: str = "waste_total") -> list[dict]:
    bars = []
    for name, nights in named_nights:
        label, ask = SEED_ONLY if name == "seed" else INPUTS[name]
        b = _band([getattr(m, metric) for m in nights])
        bars.append({"input": name, "label": label, "data_request": ask, **b, "width": round(b["p90"] - b["p10"], 1)})
    return sorted(bars, key=lambda x: -x["width"])


def sweep(svc, baseline: ScenarioConfig, plan: ScenarioConfig, u: Uncertainty = DEFAULT, n_worlds: int = 200,
          tornado_worlds: int = 100, on_progress: Progress | None = None) -> dict:
    """Plan vs baseline in the same `n_worlds` plausible worlds, plus a waste tornado for the baseline
    (`tornado_worlds` = 0 leaves the tornado out)."""
    from .config import config_hash
    from .runner import ScenarioResult, compare

    b_u, p_u = baseline.updated(uncertainty=u), plan.updated(uncertainty=u)
    t_jobs = tornado_jobs(baseline, u) if tornado_worlds else []
    nights = _run_all(svc, [(b_u, n_worlds), (p_u, n_worlds)] + [(c, tornado_worlds) for _, c in t_jobs], on_progress)
    b_n, p_n, t_n = nights[0], nights[1], nights[2:]
    vs = compare(ScenarioResult(config_hash(b_u), baseline.label, b_n), ScenarioResult(config_hash(p_u), plan.label, p_n))
    return {
        "n_worlds": n_worlds,
        "uncertainty": u.model_dump(),
        "baseline_label": baseline.label,
        "plan_label": plan.label,
        "vs_baseline": vs,
        "waste": {"baseline": _band([m.waste_total for m in b_n]), "plan": _band([m.waste_total for m in p_n])},
        "tornado": {"metric": "waste_total", "n_worlds": tornado_worlds,
                    "bars": tornado_bars([(name, ns) for (name, _), ns in zip(t_jobs, t_n)])} if t_jobs else None,
        "note": ("Each world draws the crowd size, the rush timing and every dish's popularity from the stated spread; "
                 "the kitchen plan stays as planned. Both plans face the same worlds. The spreads are judgement, "
                 "not data: the tornado says which data request would narrow them most."),
    }
