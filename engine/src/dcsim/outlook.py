"""Future waste: the coming menu dates in the same units as the recorded history.

The waste page compares the recorded past with the simulated future. Recorded waste is
pre-consumer (expired + left at close, in lb; plate waste is outside the plan's reach), so
simulated nights are converted the same way as in the back-test: each batch-cooked dish's
wasted portions x its serving weight (`synthetic.dish_oz`) and cost (`synthetic.dish_cost`).

    waste_summary(cfg, nights)  -> lb / usd / portions / stockout ranges for any simulated config
    outlook(svc)                -> every scenario in the demo bundle on the coming dates, keyed like the
                                   bundle manifest ("<date>|<preset or baseline>|<lever>"), so the page
                                   can draw whichever plan is adopted for each night

`Service.result` adds `waste_lb` to every result, so plans run on the API (the Lab, Ask Sam) carry
their own numbers; the bundle's `waste/outlook.json` covers the bundled scenarios offline.
"""

from __future__ import annotations

import datetime as dt
import json

import numpy as np

from .config import ScenarioConfig
from .metrics import NightMetrics

N_NIGHTS = 30


def _pct(xs) -> dict:
    p10, p50, p90 = np.percentile(np.asarray(xs, dtype=float), [10, 50, 90])
    return {"p10": round(float(p10), 1), "p50": round(float(p50), 1), "p90": round(float(p90), 1)}


def waste_summary(cfg: ScenarioConfig, nights: list[NightMetrics]) -> dict | None:
    """Pre-consumer waste per night (lb, usd, portions) and stockout minutes, as 10/50/90% ranges."""
    if not nights:
        return None
    from .synthetic import dish_cost, dish_oz

    oz, cost = dish_oz(cfg), dish_cost(cfg)
    lb, usd, portions = [], [], []
    for m in nights:
        batch = [(k, d) for k, d in m.dishes.items() if not d.to_order and k in oz]
        portions.append(sum(d.waste for _, d in batch))
        lb.append(sum(d.waste * oz[k] / 16 for k, d in batch))
        usd.append(sum(d.waste * cost[k] for k, d in batch))
    return {"lb": _pct(lb), "usd": _pct(usd), "portions": _pct(portions),
            "stockout_min": _pct([m.stockout_min_total for m in nights]), "n_nights": len(nights),
            "basis": "pre-consumer (expired + left at close), like the recorded history"}


def _entries(svc) -> list[dict]:
    """The bundle's scenarios (manifest), or baseline + every preset per date when there is no bundle."""
    from .bundle import default_out
    from .config import config_hash
    from .data import available_dates
    from .levers import ScenarioRequest, build_config
    from .presets import PRESETS

    manifest = default_out() / "manifest.json"
    if manifest.exists():
        return [{k: e[k] for k in ("key", "date", "preset", "lever", "label", "config_hash")}
                for e in json.loads(manifest.read_text())["entries"]]
    out = []
    for date in available_dates():
        for preset in [None, *PRESETS]:
            cfg = build_config(ScenarioRequest(date=date, preset=preset))
            svc.store.put_config(cfg)
            out.append({"key": f"{date}|{preset or 'baseline'}|", "date": date, "preset": preset, "lever": "",
                        "label": cfg.label, "config_hash": config_hash(cfg)})
    return out


def outlook(svc, n: int = N_NIGHTS, entries: list[dict] | None = None) -> dict:
    """Simulated waste for every bundled scenario on the coming dates.

    Uses cached nights. The baseline and the recommended plan are simulated when missing (16 configs);
    other scenarios without cached nights are listed under `missing` (the API's /api/results covers them).
    """
    from .levers import ScenarioRequest, build_config
    from .synthetic import RECOMMENDED_PLAN

    rows, missing = {}, []
    for e in entries if entries is not None else _entries(svc):
        cfg = svc.store.get_config(e["config_hash"])
        must = not e["lever"] and e["preset"] in (None, RECOMMENDED_PLAN)
        if cfg is None and not e["lever"]:
            cfg = build_config(ScenarioRequest(date=e["date"], preset=e["preset"]))
        if cfg is None:
            missing.append(e["key"])
            continue
        nights = svc.ensure_nights(cfg, n) if must else svc.cached_nights(cfg, n)
        if len(nights) < n:
            missing.append(e["key"])
            continue
        rows[e["key"]] = {k: e[k] for k in ("date", "preset", "lever", "label", "config_hash")} | waste_summary(cfg, nights)
    dates = sorted({r["date"] for r in rows.values()})
    return {
        "kind": "waste_outlook",
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "n_nights": n, "dates": dates, "recommended_plan": RECOMMENDED_PLAN,
        "_note": "Simulated, not recorded: each scenario's 30 nights on that date's menu, in the recorded "
                 "history's units (pre-consumer lb). Keys match the bundle manifest.",
        "entries": rows, "missing": missing,
    }


__all__ = ["waste_summary", "outlook", "N_NIGHTS"]
