"""Future waste outlook (outlook.py): simulated nights in the recorded history's units."""

from __future__ import annotations

import numpy as np

from dcsim.levers import ScenarioRequest, build_config
from dcsim.outlook import outlook, waste_summary
from dcsim.service import Service
from dcsim.store import Store
from dcsim.synthetic import RECOMMENDED_PLAN, dish_oz

DATE = "2026-11-13"


def test_waste_summary_matches_dish_weights(tmp_path):
    svc = Service(Store(tmp_path))
    cfg = build_config(ScenarioRequest(date=DATE))
    nights = svc.ensure_nights(cfg, 3)
    w = waste_summary(cfg, nights)
    oz = dish_oz(cfg)
    per_night = [sum(d.waste * oz[k] / 16 for k, d in m.dishes.items() if not d.to_order) for m in nights]
    assert w["n_nights"] == 3 and w["lb"]["p10"] <= w["lb"]["p50"] <= w["lb"]["p90"]
    assert w["lb"]["p50"] == round(float(np.median(per_night)), 1)  # same rule as the back-test
    assert w["portions"]["p50"] > 0 and w["usd"]["p50"] > 0
    assert waste_summary(cfg, []) is None
    # every API result carries it, so plans adopted from the Lab or Sam have their own numbers
    assert svc.result(cfg, nights)["waste_lb"]["lb"] == w["lb"]


def test_outlook_runs_baseline_and_plan_and_lists_missing(tmp_path):
    svc = Service(Store(tmp_path))
    base = build_config(ScenarioRequest(date=DATE))
    rec = build_config(ScenarioRequest(date=DATE, preset=RECOMMENDED_PLAN))
    entries = [
        {"key": f"{DATE}|baseline|", "date": DATE, "preset": None, "lever": "", "label": "baseline", "config_hash": "x"},
        {"key": f"{DATE}|{RECOMMENDED_PLAN}|", "date": DATE, "preset": RECOMMENDED_PLAN, "lever": "", "label": rec.label,
         "config_hash": "y"},
        {"key": f"{DATE}|baseline|crowd_multiplier=1.1", "date": DATE, "preset": None, "lever": "crowd_multiplier=1.1",
         "label": "x", "config_hash": "not-in-store"},
    ]
    o = outlook(svc, n=3, entries=entries)
    assert set(o["entries"]) == {f"{DATE}|baseline|", f"{DATE}|{RECOMMENDED_PLAN}|"}
    assert o["missing"] == [f"{DATE}|baseline|crowd_multiplier=1.1"]  # not cached and can't be rebuilt from the key
    assert o["dates"] == [DATE] and o["recommended_plan"] == RECOMMENDED_PLAN
    assert len(svc.cached_nights(base, 3)) == 3  # the baseline was simulated because it was missing
    e = o["entries"][f"{DATE}|baseline|"]
    assert e["lb"]["p50"] > 0 and e["basis"].startswith("pre-consumer")
