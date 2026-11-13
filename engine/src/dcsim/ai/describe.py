"""Plain-English list of what a scenario changes vs its baseline (shown next to /ask and explain output)."""

from __future__ import annotations

from ..config import ScenarioConfig
from ..reports import clock

_PLAN_FIELDS = {"batch_size": "batch size", "starting_batches": "opening pans", "reorder_point": "reorder point",
                "max_batches": "max extra batches", "last_batch_min": "last batch", "cook_min": "cook time",
                "hold_min": "hold time"}


def _fmt(field: str, v) -> str:
    if field == "last_batch_min":
        return clock(v) if v is not None else "close"
    return f"{v:g}" if isinstance(v, float) else str(v)


def config_changes(base: ScenarioConfig, cfg: ScenarioConfig) -> list[str]:
    out = []
    bc, cc = base.crowd, cfg.crowd
    if abs(cc.multiplier - bc.multiplier) > 1e-9:
        out.append(f"Crowd: x{cc.multiplier / bc.multiplier:.2f} (about {round(cc.expected_students * cc.multiplier):,} students)")
    if cc.shift_min != bc.shift_min:
        out.append(f"Arrival waves shifted {cc.shift_min - bc.shift_min:+g} min")
    bs = {s.id: s for s in base.stations}
    for s in cfg.stations:
        b = bs.get(s.id)
        if b and s.servers != b.servers:
            out.append(f"{s.name} servers: {b.servers} -> {s.servers}")
        if b and s.service_median_min != b.service_median_min:
            out.append(f"{s.name} time per item: {b.service_median_min:g} -> {s.service_median_min:g} min")
    bm, cm = {d.id: d for d in base.menu}, {d.id: d for d in cfg.menu}
    names = {**{k: d.name for k, d in bm.items()}, **{k: d.name for k, d in cm.items()}}
    for k in bm.keys() - cm.keys():
        out.append(f"Removed: {names[k]}")
    for k in cm.keys() - bm.keys():
        out.append(f"Added: {names[k]} at {cfg.station(cm[k].station).name}" + (" (AI estimate)" if cm[k].estimate else ""))
    for k in cm.keys() & bm.keys():
        if abs(cm[k].popularity / bm[k].popularity - 1) > 1e-6:
            out.append(f"{names[k]} popularity: x{cm[k].popularity / bm[k].popularity:.1f}")
    for k in cfg.kitchen.keys() & base.kitchen.keys():
        a, b = base.kitchen[k].model_dump(), cfg.kitchen[k].model_dump()
        diffs = [f"{label} {_fmt(f, a[f])} -> {_fmt(f, b[f])}" for f, label in _PLAN_FIELDS.items() if a[f] != b[f]]
        if diffs:
            out.append(f"{names[k]}: " + ", ".join(diffs))
    return sorted(out)
