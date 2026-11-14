"""Static demo bundle (ENGINE.md §6.6): everything the pages need, with no API.

    uv run dcsim bundle [--out web/public/cache] [--grid] [--traces featured|all|none]

Writes:
    manifest.json                 index; look results up by `key`
    results/<config_hash>.json    same shape as POST /api/simulate -> result
    traces/<config_hash>_s<seed>.json.gz   median-night 3D trace (gzip; use DecompressionStream)
    narration/<config_hash>_s<seed>.json   highlight students on that night: card + narration (every traced entry)
    ref/dates.json, ref/presets.json, ref/layout.json, ref/menu/<date>.json, ref/arrivals/<date>.json
    waste/history.json, waste/backtest.json, waste/calibration.json, waste/forecasts.json, waste/facts.json

Result key (build the same string in the frontend):
    "<date>|<preset or baseline>|<lever>"   lever is "" or one of
    "crowd_multiplier=<x>"              x in 0.75..1.25, step 0.05, written like 1.1 (no trailing zeros)
    "stations.<id>.servers=<n>"         baseline servers +/- 1
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import shutil
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

from .api import arrivals as api_arrivals
from .api import dates as api_dates
from .api import layout as api_layout
from .api import menu as api_menu
from .api import presets as api_presets
from .api import waste_facts as api_waste_facts
from .backtest import backtest_path, waste_history
from .config import ENGINE_VERSION, config_hash
from .data import available_dates, repo_root
from .levers import Levers, ScenarioRequest, StationLever, build_config
from .presets import PRESETS
from .runner import median_night
from .service import Service, baseline_of

_say = partial(print, flush=True)  # progress shows up live even when output goes to a file or pipe

N_NIGHTS = 30
CROWD_STEPS = [0.75, 0.8, 0.85, 0.9, 0.95, 1.05, 1.1, 1.15, 1.2, 1.25]


def default_out() -> Path:
    return repo_root() / "web" / "public" / "cache"


def _num(x: float) -> str:
    return f"{x:g}"


def key(date: str, preset: str | None, lever: str = "") -> str:
    return f"{date}|{preset or 'baseline'}|{lever}"


def _write(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()
    path.write_bytes(data)
    return len(data)


def requests_for(date: str, grid: bool) -> list[tuple[str, ScenarioRequest]]:
    reqs = [(key(date, None), ScenarioRequest(date=date))]
    reqs += [(key(date, p), ScenarioRequest(date=date, preset=p)) for p in PRESETS]
    if grid:
        reqs += [(key(date, None, f"crowd_multiplier={_num(m)}"), ScenarioRequest(date=date, levers=Levers(crowd_multiplier=m)))
                 for m in CROWD_STEPS]
        base = build_config(ScenarioRequest(date=date))
        for s in base.stations:
            if s.self_serve:
                continue
            for n in (s.servers - 1, s.servers + 1):
                if n >= 1:
                    reqs.append((key(date, None, f"stations.{s.id}.servers={n}"),
                                 ScenarioRequest(date=date, levers=Levers(stations={s.id: StationLever(servers=n)}))))
    return reqs


def _narrations(narrator, cfg, h: str, seed: int, k: int, log: Callable[[str], None] = _say,
                tag: str = "") -> dict:
    from .ai.narrate import TRACES, highlights

    picks = highlights(cfg, TRACES.get(narrator.svc, h, seed), k)
    out = {"config_hash": h, "seed": seed, "highlights": picks, "students": {}}
    for i, p in enumerate(picks, 1):
        _, c = narrator.load(h, seed, p["student_id"])
        t0 = time.monotonic()
        n = narrator.narrate_sync(h, seed, p["student_id"])
        why = f" - {n['reason']}" if n.get("reason") else ""
        log(f"  {tag} student {i}/{len(picks)}: {n['source']} ({time.monotonic() - t0:.1f}s){why}")
        out["students"][str(p["student_id"])] = {"card": c, "text": n["text"], "source": n["source"]}
    return out


def _count_traces(dates: list[str], grid: bool, traces: str, featured: str) -> int:
    """How many entries `build` will export a trace for (for progress lines); same rule as the loop below."""
    if traces == "none":
        return 0
    n = 0
    for date in dates:
        for k, req in requests_for(date, grid):
            if k.split("|", 2)[2]:
                continue
            cfg = build_config(req)
            if traces == "all" or date == featured or config_hash(baseline_of(cfg)) == config_hash(cfg):
                n += 1
    return n


def build(svc: Service, out: Path | None = None, *, grid: bool = False, traces: str = "featured",
          featured: str | None = None, dates: list[str] | None = None, n_nights: int = N_NIGHTS,
          narrate: int = 12, log: Callable[[str], None] = _say) -> Path:
    """`narrate`: highlight students narrated per exported trace (0 = none). Uses LLM_PROVIDER.

    Every exported trace gets a narration file, so the Hall has highlight students on every date. The LLM
    pace is held under the free-tier limit (the narrator waits instead of falling back to the template)."""
    from .ai import LLM
    from .ai.narrate import Narrator

    narrator = Narrator(svc, LLM(svc.store), wait_when_busy=True, log=log)
    if narrate:
        llm = narrator.llm
        log(f"narration: {llm.provider} / {llm.model}, {narrate} students per trace, "
            f"timeout {llm.timeout_s:.0f}s (LLM_PROVIDER=mock skips the LLM)")
    out = out or default_out()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    dates = dates or available_dates()
    featured = featured or dates[0]
    entries, size = [], 0
    n_traces, done_traces = _count_traces(dates, grid, traces, featured), 0

    for date in dates:
        reqs = requests_for(date, grid)
        for k, req in reqs:
            cfg = build_config(req)
            nights = svc.ensure_nights(cfg, n_nights)
            base = baseline_of(cfg)
            is_base = config_hash(base) == config_hash(cfg)
            res = svc.result(cfg, nights, None if is_base else svc.ensure_nights(base, n_nights))
            h = res["config_hash"]
            size += _write(out / "results" / f"{h}.json", res)
            entry = {"key": k, "date": date, "preset": req.preset, "lever": k.split("|", 2)[2], "label": cfg.label,
                     "config_hash": h, "file": f"results/{h}.json", "trace": None, "narration": None}
            # featured: every preset on the featured date + each date's baseline; all: every preset everywhere
            if traces != "none" and not entry["lever"] and (traces == "all" or date == featured or is_base):
                seed = median_night(nights)
                data = svc.trace_bytes(h, seed)
                (out / "traces").mkdir(exist_ok=True)
                (out / "traces" / f"{h}_s{seed}.json.gz").write_bytes(data)
                size += len(data)
                entry["trace"] = f"traces/{h}_s{seed}.json.gz"
                done_traces += 1
                if narrate:
                    tag = f"[trace {done_traces}/{n_traces} {date} {cfg.label or 'baseline'}]"
                    size += _write(out / "narration" / f"{h}_s{seed}.json",
                                   _narrations(narrator, cfg, h, seed, narrate, log, tag))
                    entry["narration"] = f"narration/{h}_s{seed}.json"
            entries.append(entry)
        log(f"{date}: {len(reqs)} results")

        size += _write(out / "ref" / "menu" / f"{date}.json", api_menu(date))
        size += _write(out / "ref" / "arrivals" / f"{date}.json", api_arrivals(date, 1.0, 10))

    size += _write(out / "ref" / "dates.json", api_dates())
    size += _write(out / "ref" / "presets.json", api_presets())
    size += _write(out / "ref" / "layout.json", api_layout())
    size += _write(out / "waste" / "facts.json", api_waste_facts())
    hist = waste_history()
    if hist["nights"]:
        size += _write(out / "waste" / "history.json", hist)
    if backtest_path().exists():
        size += _write(out / "waste" / "backtest.json", json.loads(backtest_path().read_text()))
    from .calibration import public_report

    calib = public_report()
    if calib:
        size += _write(out / "waste" / "calibration.json", calib)
    ledger = forecast_snapshot()
    if ledger is not None:
        size += _write(out / "waste" / "forecasts.json", ledger)

    manifest = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "engine_version": ENGINE_VERSION,
        "n_nights": n_nights,
        "llm": narrator.llm.info(),
        "featured_date": featured,
        "key_format": "<date>|<preset or baseline>|<lever>; lever is '' or 'crowd_multiplier=1.1' or 'stations.grill.servers=3'",
        "dates": dates,
        "presets": list(PRESETS),
        "entries": entries,
        "ref": {"dates": "ref/dates.json", "presets": "ref/presets.json", "layout": "ref/layout.json",
                "menu": "ref/menu/{date}.json", "arrivals": "ref/arrivals/{date}.json"},
        "waste": {"facts": "waste/facts.json",
                  "history": "waste/history.json" if hist["nights"] else None,
                  "backtest": "waste/backtest.json" if backtest_path().exists() else None,
                  "calibration": "waste/calibration.json" if calib else None,
                  "forecasts": "waste/forecasts.json" if ledger is not None else None},
    }
    size += _write(out / "manifest.json", manifest)
    log(f"bundle: {len(entries)} results, {sum(1 for e in entries if e['trace'])} traces, {size / 1e6:.1f} MB -> {out}")
    return out


def forecast_snapshot() -> dict | None:
    """The Tiger forecast ledger as of now (offline copy of GET /api/forecasts); None without Tiger."""
    from . import tsdb

    if not tsdb.configured():
        return None
    try:
        with tsdb.connect() as conn:
            rows = tsdb.scorecard(conn)
    except Exception as e:
        print(f"bundle: forecast ledger skipped ({str(e)[:120]})")
        return None
    return {"kind": "forecast_ledger", "snapshot_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "_note": "Copy of the append-only Tiger ledger. Observations are SYNTHETIC.", "forecasts": rows}


def refresh_waste(out: Path | None = None) -> list[str]:
    """Rewrite only the calibration report and ledger snapshot in an existing bundle (seconds, not minutes)."""
    from .calibration import public_report

    out = out or default_out()
    manifest_path = out / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no bundle at {out}; run `dcsim bundle` first")
    manifest = json.loads(manifest_path.read_text())
    written = []
    for name, data in (("calibration", public_report()), ("forecasts", forecast_snapshot())):
        if data is not None:
            _write(out / "waste" / f"{name}.json", data)
            manifest.setdefault("waste", {})[name] = f"waste/{name}.json"
            written.append(f"waste/{name}.json")
    _write(manifest_path, manifest)
    return written
