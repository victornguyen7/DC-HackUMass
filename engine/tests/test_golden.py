"""Golden baseline (IMPROVEMENTS 0.4): Nov 13 headline numbers are pinned.

The engine is deterministic for a config and seed, so any change that moves these numbers
must be intentional. After an intended change, rewrite the file and commit it with the reason:

    DCSIM_UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from dcsim import load_baseline
from dcsim.runner import run_nights

GOLDEN = Path(__file__).parent / "golden" / "nov13.json"
DATE = "2026-11-13"
SEEDS = list(range(1, 11))
# Scalar NightMetrics fields pinned as medians over SEEDS. Fields added later join the file on update.
FIELDS = (
    "students", "late_night_students", "waste_total", "stockout_min_total", "missed_first_choice_rate",
    "missed_meal", "wait_p50", "wait_p90", "balks", "empty_seen", "in_hall_before_close",
    "batch_wait_min", "cook_busy_share", "empty_wait_min", "seat_wait_min", "peak_seated",
    "swipe_wait_min", "swipe_queue_max", "group_students", "allergy_students", "allergy_no_meal",
)


def snapshot() -> dict:
    cfg = load_baseline(DATE)
    nights = run_nights(cfg, SEEDS)
    out: dict = {"date": DATE, "seeds": SEEDS, "engine_version": cfg.engine_version}
    for f in FIELDS:
        if hasattr(nights[0], f):
            out[f] = round(float(np.median([getattr(m, f) for m in nights])), 4)
    out["max_queue"] = {sid: float(np.median([m.max_queue[sid] for m in nights])) for sid in nights[0].max_queue}
    return out


def test_golden_baseline():
    now = snapshot()
    if os.environ.get("DCSIM_UPDATE_GOLDEN") == "1" or not GOLDEN.exists():
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(now, indent=1) + "\n")
        pytest.skip(f"golden baseline written to {GOLDEN.name}")
    pinned = json.loads(GOLDEN.read_text())
    moved = []
    for k, v in pinned.items():
        if k in ("date", "seeds", "engine_version"):
            continue
        if isinstance(v, dict):
            for sid, q in v.items():
                if now[k].get(sid) != pytest.approx(q, abs=1e-6):
                    moved.append(f"{k}.{sid}: {q} -> {now[k].get(sid)}")
        elif now.get(k) != pytest.approx(v, rel=1e-6, abs=1e-6):
            moved.append(f"{k}: {v} -> {now.get(k)}")
    new = sorted(set(now) - set(pinned))
    assert not moved and not new, (
        "Nov 13 baseline moved (if intended: DCSIM_UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py):\n  "
        + "\n  ".join(moved + [f"new field {k}" for k in new])
    )
