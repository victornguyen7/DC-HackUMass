"""The team's observations (data/worcester/calibration_targets.json) as tests."""

from __future__ import annotations

import json

import numpy as np
import pytest

from dcsim import load_baseline
from dcsim.arrivals import expected_arrivals
from dcsim.data import default_data_dir
from dcsim.metrics import compute_metrics
from dcsim.model import Night

T = json.loads((default_data_dir() / "calibration_targets.json").read_text())


def _min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


@pytest.fixture(scope="module", params=T["dates"])
def nights(request):
    cfg = load_baseline(request.param)
    out = []
    for seed in T["seeds"]:
        n = Night(cfg, seed).run()
        out.append((n, compute_metrics(n)))
    return cfg, out


def test_dinner_peak_line(nights):
    cfg, runs = nights
    peaks = [max(max((q for t, q in n.rec.queue_lens[i] if t < cfg.close_min), default=0) for i in range(len(cfg.stations)))
             for n, _ in runs]
    t = T["dinner_peak_line"]
    assert t["min"] <= float(np.median(peaks)) <= t["max"], peaks


def test_busiest_arrivals(nights):
    cfg, _ = nights
    windows = [(s, e) for s, e in expected_arrivals(cfg) if s < cfg.close_min]
    busiest = max(windows, key=lambda w: w[1])[0]
    t = T["busiest_arrivals"]
    assert _min(t["from"]) <= busiest < _min(t["to"]), busiest


def test_rarely_runs_out_late(nights):
    # 0.4.2: 23-49% of dinner dishes ran out after 8:30 pm (ISSUES E3); 0.5.0: 12-21%; 0.6.1 (last
    # dinner batch 15 min before close instead of 20): 5-15% on all 8 dates.
    cfg, runs = nights
    t = T["late_runouts"]
    dinner = [d.id for d in cfg.menu if d.period == "dinner" and d.id in cfg.kitchen]
    shares = [sum(1 for k in dinner if (r := m.dishes[k].runout_min) is not None and r >= _min(t["after"])) / len(dinner)
              for _, m in runs]
    assert float(np.median(shares)) <= t["max_share_of_dinner_dishes"], shares


def test_hall_nearly_empty_at_1130(nights):
    _, runs = nights
    assert float(np.mean([m.in_hall_before_close for _, m in runs])) < T["in_hall_at_1130"]["max"]
