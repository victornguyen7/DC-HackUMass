"""Command line.

    uv run dcsim prepare                          # import real menus + build baseline kitchen plans
    uv run dcsim menu [--date D]                  # show the simulated menu and its plan
    uv run dcsim run [--date D] [--preset NAME] [--nights 30] [--json out.json]
    uv run dcsim compare --preset smaller_batches [--date D]
    uv run dcsim trace [--date D] --out trace.json
    uv run dcsim bench
    uv run dcsim serve [--port 8000] [--warm]       # FastAPI backend (see dcsim.api)
    uv run dcsim cache stats|prune
    uv run dcsim waste-gen                         # SYNTHETIC recorded waste history
    uv run dcsim backtest [--plan smaller_batches]  # "if we had used the plan" on every recorded night
    uv run dcsim bundle [--grid] [--traces featured|all|none]   # static demo bundle -> web/public/cache
"""

from __future__ import annotations

import argparse
import os
import json
import time

from .config import ScenarioConfig, config_hash
from .data import available_dates, default_data_dir, load_baseline, raw_menu_files
from .presets import PRESETS
from .runner import ScenarioResult, compare, median_night, run_night, run_scenario


def fmt_clock(minutes: float | None) -> str:
    if minutes is None:
        return "-"
    h, m = divmod(int(round(minutes)), 60)
    return f"{(h - 1) % 12 + 1}:{m:02d} {'am' if h < 12 else 'pm'}"


def _r(p: dict, fmt: str = "{:.0f}") -> str:
    return f"{fmt.format(p['p50'])} [{fmt.format(p['p10'])}-{fmt.format(p['p90'])}]"


def _config(date: str | None, preset: str | None) -> ScenarioConfig:
    cfg = load_baseline(date)
    return PRESETS[preset](cfg) if preset else cfg


def print_result(cfg: ScenarioConfig, res: ScenarioResult, all_dishes: bool = False) -> None:
    s = res.summary
    h = s["headline"]
    print(f"\n{cfg.label}  ·  {cfg.dc} {cfg.meal} {cfg.date}  ·  hash {res.config_hash}  ·  {s['n_nights']} nights"
          f"  ·  personas: {cfg.population_status}")
    print(f"  students             {_r(h['students'])}")
    print(f"  portions wasted      {_r(h['waste_total'])}")
    print(f"  minutes out of stock {_r(h['stockout_min_total'])}")
    print(f"  never got 1st choice {_r({k: v * 100 for k, v in h['missed_first_choice_rate'].items()}, '{:.1f}')} %")
    print(f"  peak wait (p90 min)  {_r(h['wait_p90'], '{:.1f}')}")
    print(f"  missed meal         {_r(h['missed_meal'])}")

    names = {d.id: d.name for d in cfg.menu}
    rows = sorted(s["dishes"].items(), key=lambda kv: (-kv[1]["stockout_min"]["p50"], -kv[1]["waste"]["p50"]))
    if not all_dishes:
        rows = [kv for kv in rows if kv[1]["stockout_min"]["p50"] > 0 or kv[1]["waste"]["p50"] > 0][:15]
    title = "all dishes" if all_dishes else "dishes with stockouts or waste (top 15; --all for every dish)"
    print(f"\n  {title}")
    print(f"  {'dish':<42}{'served':>16}{'waste':>14}{'out(min)':>14}  runs out")
    for dish_id, d in rows:
        ro = d["runout_min"]
        runs = "to order" if d.get("to_order") else (
            f"{d['runout_share']:.0%} of nights" + (f", {fmt_clock(ro['p10'])}-{fmt_clock(ro['p90'])}" if ro else ""))
        print(f"  {names[dish_id][:41]:<42}{_r(d['served']):>16}{_r(d['waste']):>14}{_r(d['stockout_min']):>14}  {runs}")
    busy = sorted(s["max_queue"].items(), key=lambda kv: -kv[1]["p50"])[:5]
    print("\n  longest queues: " + ", ".join(f"{k} {_r(v)}" for k, v in busy))


def print_compare(cmp: dict) -> None:
    print(f"\n  vs baseline ({cmp['n_pairs']} paired nights)")
    for k, v in cmp["metrics"].items():
        d = v["diff"]
        print(f"    {k:<26} diff {d['p50']:+9.2f} [{d['p10']:+.2f} .. {d['p90']:+.2f}]"
              f"  wins {v['win_rate']:.0%}  -> {v['verdict']}")


def print_menu(cfg: ScenarioConfig) -> None:
    menu = json.loads((default_data_dir() / "menus" / f"{cfg.date}_dinner.json").read_text())
    print(f"\nWorcester dinner {cfg.date}: {len(cfg.menu)} simulated items, {len(menu['addons'])} add-ons (not simulated)")
    for st in cfg.stations:
        tag = " [to order]" if st.to_order else (" [self-serve]" if st.self_serve else "")
        print(f"\n  {st.name}{tag}")
        for d in (x for x in cfg.menu if x.station == st.id):
            plan = cfg.kitchen.get(d.id)
            p = (f"start {plan.starting_batches}x{plan.batch_size}, reorder at {plan.reorder_point}, "
                 f"+{plan.max_batches} batches") if plan else "made to order"
            tags = ",".join(d.diet_tags) or "-"
            print(f"    {d.role:<8} {d.name[:44]:<45} pop {d.popularity:<5.2f} {tags:<26} {p}")
        addons = [a["name"] for a in menu["addons"] if a["station"] == st.id]
        if addons:
            print(f"    add-ons: {'; '.join(addons)}")


def _search(args) -> None:
    """`dcsim worlds` and `dcsim optimize` (IMPROVEMENTS 2.1, 2.2): pooled, cached, with a progress line."""
    import os
    import sys
    import time
    from concurrent.futures import ProcessPoolExecutor
    from pathlib import Path

    from .data import available_dates, load_baseline
    from .service import Service
    from .store import Store

    date = args.date or available_dates()[0]

    def progress(done: int, total: int) -> None:
        print(f"\r  {done}/{total} nights", end="", file=sys.stderr, flush=True)

    t0 = time.time()
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 1) as pool:
        svc = Service(Store(), pool)
        if args.cmd == "worlds":
            from . import worlds

            base = load_baseline(date)
            r = worlds.sweep(svc, base, PRESETS[args.preset](base), n_worlds=args.worlds, on_progress=progress)
        else:
            from .optimize import optimize

            def on_trial(t: dict) -> None:
                best = t["best_waste"]
                print(f"\r  trial {t['n']}/{t['of']}: waste {t['waste']:.0f}{'' if t['feasible'] else ' (breaks a limit)'}"
                      f"  best {'-' if best is None else f'{best:.0f}'}   ", end="", file=sys.stderr, flush=True)

            r = optimize(svc, date, trials=args.trials, seeds=args.seeds, max_stockout_min=args.max_stockout_min,
                         on_trial=on_trial, on_progress=progress)
    print(file=sys.stderr)
    print(_search_text(args.cmd, r, time.time() - t0))
    if getattr(args, "save", False):
        import datetime as dt

        from . import searchlog, tsdb
        from .config import ENGINE_VERSION, config_hash

        base = load_baseline(date)
        meta = {"search_id": searchlog.new_id(), "date": date, "engine_version": ENGINE_VERSION,
                "started_at": dt.datetime.fromtimestamp(t0, dt.timezone.utc).isoformat(),
                "baseline_hash": config_hash(base), "source": getattr(args, "source", "cli")}
        with tsdb.connect() as conn:
            searchlog.init(conn)
            print(f"saved to Tiger: {searchlog.save(conn, args.cmd, r, meta)['search_id']}")
    r = {k: v for k, v in r.items() if not k.startswith("_")}
    if args.json:
        Path(args.json).write_text(json.dumps(r, indent=2))


def _search_text(cmd: str, r: dict, secs: float) -> str:
    def verdicts(vs: dict) -> list[str]:
        return [f"  {k:<26} win {m['win_rate'] * 100:>5.1f}%   median diff {m['diff']['p50']:>+9.2f}   {m['verdict']}"
                for k, m in vs["metrics"].items()]

    if cmd == "worlds":
        out = [f"{r['plan_label']} vs {r['baseline_label']} in {r['n_worlds']} plausible worlds ({secs:.0f} s):"]
        out += verdicts(r["vs_baseline"])
        out += [f"\nwaste spread on today's plan, one guess at a time ({r['tornado']['n_worlds']} worlds each):"]
        out += [f"  {b['label']:<20} {b['p10']:>7.0f} - {b['p90']:<7.0f} (width {b['width']:.0f})   ask: {b['data_request']}"
                for b in r["tornado"]["bars"]]
        return "\n".join(out)
    b, lim = r["baseline"], r["limits"]
    out = [f"searched {r['trials']} plans across {r['nights_searched']} simulated nights ({secs:.0f} s); "
           f"{r['feasible_trials']} within the limits (stockouts <= {lim['stockout_min_total']:.0f} min, "
           f"p90 wait <= {lim['wait_p90']:.1f} min)",
           f"today: waste {b['waste']:.0f}, stockouts {b['stockout_min']:.0f} min\n\ntrade-off frontier (waste vs stockouts):"]
    out += [f"  waste {f['waste']:>6.0f} ({f['waste_pct']:+5.1f}%)  stockouts {f['stockout_min']:>6.1f} min"
            f"{'  within limits' if f['within'] else ''}"
            + (f"\n      unseen seeds: waste {f['validation']['waste_pct']:+.1f}%, stockouts "
               f"{f['validation']['stockout_diff_min']:+.1f} min" if f.get("validation") else "")
            + f"\n      {'; '.join(f['changes'])}" for f in r["frontier"]]
    if not r.get("levers"):
        return "\n".join(out + ["\nno plan meaningfully beat today's within the limits"])
    out.append("")
    out += ["changes: " + "; ".join(r["changes"]), f"\nvalidated on unseen seeds {r['validation']['seeds']}:"]
    out += verdicts(r["validation"]["vs_baseline"])
    if r.get("worlds"):
        out += [f"\nin {r['worlds']['n_worlds']} plausible worlds:"] + verdicts(r["worlds"]["vs_baseline"])
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="dcsim")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "compare"):
        p = sub.add_parser(name)
        p.add_argument("--date")
        p.add_argument("--preset", choices=sorted(PRESETS), required=name == "compare")
        p.add_argument("--nights", type=int, default=30)
        p.add_argument("--workers", type=int, default=None)
        p.add_argument("--all", action="store_true", help="list every dish")
        p.add_argument("--json", help="write the summary to this file")
    t = sub.add_parser("trace")
    t.add_argument("--date")
    t.add_argument("--preset", choices=sorted(PRESETS))
    t.add_argument("--nights", type=int, default=30, help="nights used to pick the median night")
    t.add_argument("--out", default="trace.json")
    m = sub.add_parser("menu")
    m.add_argument("--date")
    sub.add_parser("prepare")
    b = sub.add_parser("bench")
    b.add_argument("--date")
    sv = sub.add_parser("serve", help="start the FastAPI backend")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--reload", action="store_true")
    sv.add_argument("--warm", action="store_true", help="precompute baseline + presets for every date in the background")
    sub.add_parser("waste-gen", help="generate the SYNTHETIC recorded waste history (data/synthetic/worcester)")
    wo = sub.add_parser("worlds", help="a plan vs today's plan in plausible worlds (crowd, rush, popularity drawn), + tornado")
    wo.add_argument("--date")
    wo.add_argument("--preset", choices=sorted(PRESETS), default="smaller_batches")
    wo.add_argument("--worlds", type=int, default=200)
    wo.add_argument("--json", help="write the result to this file")
    wo.add_argument("--save", action="store_true", help="keep the run in Tiger (dcsim.plan_searches, search_worlds)")
    wo.add_argument("--source", default="cli", help="`--save`: the source tag stored with it")
    op = sub.add_parser("optimize", help="search batch plans for the least waste (Optuna), validated on unseen seeds")
    op.add_argument("--date")
    op.add_argument("--trials", type=int, default=80)
    op.add_argument("--seeds", type=int, default=10, help="nights per trial (seeds 1..N)")
    op.add_argument("--max-stockout-min", type=float, default=None, help="default: no more than today's plan")
    op.add_argument("--json", help="write the result to this file")
    op.add_argument("--save", action="store_true", help="keep the run in Tiger (dcsim.plan_searches, search_trials)")
    op.add_argument("--source", default="cli", help="`--save`: the source tag stored with it")
    bt = sub.add_parser("backtest", help="re-simulate every recorded night with the recommended plan")
    bt.add_argument("--plan", default=None, choices=sorted(PRESETS))
    bd = sub.add_parser("bundle", help="export the static demo bundle (web/public/cache)")
    bd.add_argument("--out")
    bd.add_argument("--grid", action="store_true", help="also export one-lever variations (crowd, servers)")
    bd.add_argument("--traces", choices=["featured", "all", "none"], default="featured")
    bd.add_argument("--featured", help="date whose presets all get a 3D trace (default: first date)")
    bd.add_argument("--waste-only", action="store_true",
                    help="only refresh waste/calibration.json and waste/forecasts.json in the existing bundle")
    cc = sub.add_parser("cache", help="result cache housekeeping")
    cc.add_argument("action", choices=["stats", "prune", "llm-prune"],
                    help="llm-prune: drop LLM responses from old prompt versions and all persona responses")
    pe = sub.add_parser("personas", help="LLM persona set: generate -> review -> adopt (ENGINE.md §7.2)")
    pe.add_argument("action", choices=["generate", "adopt"])
    pe.add_argument("-n", type=int, default=20, help="personas to ask for")
    na = sub.add_parser("narrate", help="narrate students on the median night (LLM or template)")
    na.add_argument("--date")
    na.add_argument("--preset", choices=sorted(PRESETS))
    na.add_argument("--student", type=int, action="append", help="student id (repeatable); default: the night's highlights")
    na.add_argument("-k", type=int, default=6, help="highlights to narrate when no --student is given")
    ak = sub.add_parser("ask", help="plain English -> lever settings")
    ak.add_argument("question")
    ak.add_argument("--date")
    ag = sub.add_parser("agent", help="Sam the Minuteman: a goal -> simulate, check, adjust -> plan; or a question answered from Tiger")
    ag.add_argument("goal", help='e.g. "cut waste 20%% without any vegetarian dish running out"')
    ag.add_argument("--date")
    ag.add_argument("--tries", type=int, default=5, choices=range(1, 6), metavar="1-5",
                    help="most simulations Sam may run (at most 5)")
    ag.add_argument("--nights", type=int, default=30, help="nights per try (validation uses the next as many)")
    ag.add_argument("--json", action="store_true", help="print the final result as JSON")
    si = sub.add_parser("similar", help="menu dishes most like a new dish (Gemini embeddings, keyword fallback)")
    si.add_argument("name")
    si.add_argument("--date")
    si.add_argument("--station")
    si.add_argument("--role")
    si.add_argument("-k", type=int, default=3)
    sub.add_parser("ai-status", help="LLM provider, models, backups and load limits")
    ex = sub.add_parser("explain", help="why a preset won or lost vs the baseline")
    ex.add_argument("--date")
    ex.add_argument("--preset", choices=sorted(PRESETS), required=True)
    ex.add_argument("--nights", type=int, default=30)
    tq = sub.add_parser("tsdb", help="Tiger Data (TimescaleDB): observed nights and simulated runs side by side")
    tq.add_argument("action", choices=["init", "load", "query", "compress", "observe", "similar", "bench", "refresh",
                                       "track", "dishes", "searches", "sweep"],
                    help="init: create schema · load: recorded history -> observed rows · query: run a named query or SQL"
                         " · compress: convert older chunks to the columnstore now and report sizes"
                         " · observe: generate + load the SYNTHETIC observation for --date (scores its logged forecasts)"
                         " · similar: past nights most like --date (pgvector) and a held-out check"
                         " · bench: time the same questions on the raw hypertables and on their continuous aggregates"
                         " · refresh: bring every continuous aggregate up to date now"
                         " · track: score every observed night now and show the weekly track record"
                         " · dishes: the dish-popularity check, and look-alikes of --name (pgvector) with what they sold"
                         " · searches: saved plan searches and the changes that keep showing up on the frontier"
                         " · sweep: --nights simulated nights of --date into Tiger, then how long each dish is out"
                         " of stock across them (--dish to pick one; --report-only to skip loading)")
    tq.add_argument("--nights", type=int, default=500, help="`sweep`: how many nights (1-2000)")
    tq.add_argument("--dish", help="`sweep`: part of a dish name, e.g. 'tikka' (default: the longest-empty dishes)")
    tq.add_argument("--report-only", action="store_true", help="`sweep`: don't simulate or load, just query Tiger")
    tq.add_argument("--top", type=int, default=15, help="`sweep`: how many dishes to show")
    tq.add_argument("--name", help="`dishes`: a (new) dish name to find look-alikes for")
    tq.add_argument("--station", help="`dishes`: its station id")
    tq.add_argument("--role", choices=["entree", "starch", "side", "soup", "dessert", "bread"], help="`dishes`: its role")
    tq.add_argument("--repeat", type=int, default=5, help="`bench`: timed runs per query (median is reported)")
    tq.add_argument("--sql", action="store_true", help="`bench`: also print each pair of queries")
    tq.add_argument("--date", help="night for `observe` / `similar`")
    tq.add_argument("--weather", choices=["clear", "rain", "cold", "snow"], help="forecast weather for `similar`")
    tq.add_argument("--event", action="store_true", help="`similar`: the night is an event night")
    tq.add_argument("-k", type=int, default=5, help="`similar`: how many nights")
    tq.add_argument("query", nargs="?", default="status", help="query name (status, situations, weekdays, pans, "
                    "dishes, coverage, nights, calibrations, ledger, production, stack, hypertables, aggregates, "
                    "waste-daily, waste-dishes, pans-hourly) or a SQL string")
    fc = sub.add_parser("forecast", help="log a naive + calibrated forecast for a night in the append-only Tiger ledger")
    fc.add_argument("--date", required=True)
    fc.add_argument("--preset", choices=sorted(PRESETS))
    fc.add_argument("--weather", choices=["clear", "rain", "cold", "snow"],
                    help="the night's weather, known on the day (used if the calibration kept the situational fit)")
    fc.add_argument("--event", action="store_true", help="the night is an event night")
    ca = sub.add_parser("calibrate", help="fit on the early recorded weeks, score forecasts on held-out weeks (uses Tiger)")
    ca.add_argument("--test-from", default=None, help="first held-out date (default 2026-10-29)")
    ca.add_argument("--situations-only", action="store_true",
                    help="refit only the crowd error by weather/event from what is in Tiger (no new simulations) and log "
                         "it as a new calibration")
    args = ap.parse_args(argv)

    if args.cmd in ("tsdb", "calibrate", "forecast"):
        _tiger(args)
        return

    if args.cmd in ("personas", "narrate", "ask", "explain", "similar", "ai-status", "agent"):
        _ai(args)
        return

    if args.cmd == "serve":
        import os

        import uvicorn

        if args.warm:
            os.environ["DCSIM_WARM"] = "1"
        uvicorn.run("dcsim.api:app", host=args.host, port=args.port, reload=args.reload)
        return
    if args.cmd == "bundle" and args.waste_only:
        from pathlib import Path

        from .bundle import refresh_waste

        print("bundle refreshed: " + ", ".join(refresh_waste(Path(args.out) if args.out else None)))
        return
    if args.cmd in ("worlds", "optimize"):
        _search(args)
        return
    if args.cmd in ("backtest", "bundle"):
        import os
        from concurrent.futures import ProcessPoolExecutor

        from .service import Service
        from .store import Store

        with ProcessPoolExecutor(max_workers=os.cpu_count() or 1) as pool:
            svc = Service(Store(), pool)
            if args.cmd == "backtest":
                from .backtest import RECOMMENDED_PLAN, write_backtest

                p = write_backtest(svc, args.plan or RECOMMENDED_PLAN)
                h = json.loads(p.read_text())["headline"]
                r = h["reduction_pct"]
                print(f"back-test over {h['nights']} recorded nights: waste {r['p50']:.1f}% lower "
                      f"[{r['p10']:.1f}-{r['p90']:.1f}], plan wins {h['plan_wins']}/{h['nights']} -> {p}")
            else:
                from pathlib import Path

                from .bundle import build

                build(svc, Path(args.out) if args.out else None, grid=args.grid, traces=args.traces, featured=args.featured)
        return
    if args.cmd == "waste-gen":
        from .synthetic import generate

        print(f"synthetic history -> {generate()}")
        return
    if args.cmd == "cache":
        from .store import Store

        store = Store()
        if args.action == "llm-prune":
            from .ai import ask, dish_prior, explain, narrate
            from .ai.llm import EMBED_VERSION

            current = {"narration": narrate.PROMPT_VERSION, "ask": ask.PROMPT_VERSION,
                       "dish_prior": dish_prior.PROMPT_VERSION, "explain": explain.PROMPT_VERSION,
                       "embed": EMBED_VERSION}
            print(f"deleted {store.prune_llm(current, drop_kinds=('persona',))} cached LLM responses")
        else:
            print(store.stats() if args.action == "stats" else f"pruned {store.prune()} stale configs")
        return

    if args.cmd == "prepare":
        from .menu_import import import_menus
        from .planner import plan_kitchen

        d = default_data_dir()
        for raw in raw_menu_files():
            for meal in ("dinner", "late_night"):
                for path in import_menus(raw, d / "stations.json", d / "overrides.json", d / "menus", meal=meal):
                    print(f"menu    {path.relative_to(d.parent.parent)}")
        for date in available_dates(d):
            print(f"kitchen {plan_kitchen(date, d).relative_to(d.parent.parent)}")

    elif args.cmd == "menu":
        print_menu(load_baseline(args.date))

    elif args.cmd in ("run", "compare"):
        cfg = _config(args.date, args.preset)
        t0 = time.perf_counter()
        res = run_scenario(cfg, args.nights, args.workers)
        print_result(cfg, res, args.all)
        out = {"label": cfg.label, "date": cfg.date, "config_hash": res.config_hash, "summary": res.summary}
        if args.cmd == "compare":
            base = run_scenario(load_baseline(cfg.date), args.nights, args.workers)
            cmp = compare(base, res)
            print_compare(cmp)
            out["compare"] = cmp
        print(f"\n  ({time.perf_counter() - t0:.2f}s)")
        if args.json:
            with open(args.json, "w") as f:
                json.dump(out, f, indent=2)

    elif args.cmd == "trace":
        cfg = _config(args.date, args.preset)
        seed = median_night(run_scenario(cfg, args.nights).nights)
        trace = run_night(cfg, seed, trace=True).trace
        trace["config_hash"] = config_hash(cfg)
        with open(args.out, "w") as f:
            json.dump(trace, f, separators=(",", ":"))
        print(f"median night seed {seed}: {len(trace['students'])} students -> {args.out}")

    elif args.cmd == "bench":
        cfg = load_baseline(args.date)
        t0 = time.perf_counter()
        r = run_night(cfg, 1)
        one = time.perf_counter() - t0
        t0 = time.perf_counter()
        run_scenario(cfg, 30)
        par = time.perf_counter() - t0
        print(f"1 night (serial): {one:.3f}s · {r.metrics.students} students · {len(cfg.menu)} dishes")
        print(f"30 nights (parallel): {par:.2f}s")


def _tiger(args) -> None:
    import datetime as dt

    from . import tsdb

    with tsdb.connect() as conn:
        if args.cmd == "calibrate" or getattr(args, "action", None) in ("init", "load", "track"):
            ts = tsdb.init_schema(conn)
            print(f"schema dcsim ready ({'TimescaleDB hypertables + continuous aggregate' if ts else 'plain PostgreSQL'})")
        if args.cmd == "tsdb" and args.action in ("init", "refresh"):
            tsdb.refresh(conn)  # new aggregates are created empty; fill them over the whole history
            print("continuous aggregates refreshed: dish_nightly, " + ", ".join(tsdb.DASH_CAGGS))
        elif args.cmd == "tsdb" and args.action == "bench":
            rows = tsdb.bench(conn, max(1, args.repeat))
            print(f"Same question, raw hypertable vs continuous aggregate (server-side ms from EXPLAIN ANALYZE, "
                  f"median of {max(1, args.repeat)} runs)\n")
            cols = ["#", "raw table", "rows", "raw ms", "aggregate", "rows ", "agg ms", "aggregate vs raw", "same answer"]
            verdict = lambda x: (f"{x:g}x faster" if x >= 1.5 else f"{round(1 / x, 1):g}x slower" if x <= 0.67  # noqa: E731
                                 else "about the same")
            print(tsdb.format_table(cols, [(i, b["raw_table"], f"{b['raw_rows']:,}", b["raw"]["total_ms"],
                                            b["aggregate"], f"{b['aggregate_rows']:,}", b["agg"]["total_ms"],
                                            verdict(b["speedup"]), "yes" if b["same_answer"] else "NO")
                                           for i, b in enumerate(rows, 1)]))
            print()
            for i, b in enumerate(rows, 1):
                print(f"{i}. {b['title']}\n   {b['why']}")
            if args.sql:
                for b in rows:
                    print(f"\n-- {b['title']}\n-- raw ({b['raw']['total_ms']} ms)\n{b['raw_sql']};\n"
                          f"-- aggregate ({b['agg']['total_ms']} ms)\n{b['agg_sql']};")
        elif args.cmd == "tsdb" and args.action == "load":
            n = tsdb.load_observed(conn)
            print("observed history loaded: " + ", ".join(f"{v:,} {k}" for k, v in n.items()))
        elif args.cmd == "forecast":
            from .calibration import issue_forecasts
            from .service import Service
            from .store import Store

            cfg = _config(args.date, args.preset)
            situation = ({"weather": args.weather, "event_night": args.event} if args.weather or args.event else None)
            for f in issue_forecasts(Service(Store()), conn, cfg, situation=situation):
                w, so = f["waste_lb"], f["stockout_min"]
                print(f"{f['forecast_id']}  issued {f['issued_at']}  {f['method']:<10} waste {w['p50']:.0f} lb "
                      f"[{w['p10']:.0f}-{w['p90']:.0f}]  stockout {so['p50']:.0f} min [{so['p10']:.0f}-{so['p90']:.0f}]")
        elif args.cmd == "tsdb" and args.action == "observe":
            from .calibration import observe_night

            if not args.date:
                raise SystemExit("--date is required for observe")
            r = observe_night(conn, args.date)
            print(f"SYNTHETIC observation for {r['night']} ({r['plan']}): {r['students']} students, "
                  f"{r['waste_lb']} lb waste, {r['stockout_min']} stockout min")
            cols, rows = tsdb.run_query(conn, "ledger")
            print(tsdb.format_table(cols, [x for x in rows if str(x[0]) == args.date]))
        elif args.cmd == "tsdb" and args.action == "similar":
            from . import nightsearch as ns

            if not args.date:
                raise SystemExit("--date is required for similar")
            index = ns.init(conn)
            n = ns.build(conn)
            vec, q = ns.query_vector(args.date, args.weather, args.event)
            print(f"{n} observed nights embedded ({ns.DIM} dims, index: {index}). Query: {q['weekday']} {q['night']}, "
                  f"{q['n_dishes']} dinner dishes, weather {q['weather'] or 'not given (ignored)'}"
                  f"{', event night' if q['event_night'] else ''}\n")
            near = ns.similar(conn, vec, k=args.k, exclude=q["night"])
            cols = ["night", "weekday", "weather", "event_night", "distance", "students", "waste_lb", "stockout_min"]
            print(tsdb.format_table(cols, [[r[c] if c != "distance" else round(r[c], 3) for c in cols] for r in near]))
            if near:
                avg = {m: sum(float(r[m]) for r in near) / len(near) for m in ns.OUTCOMES}
                print(f"\naverage of these {len(near)}: {avg['students']:.0f} students, {avg['waste_lb']:.0f} lb waste, "
                      f"{avg['stockout_min']:.0f} stockout min")
            bt = ns.backtest(conn, tsdb.TEST_FROM, k=args.k)
            if bt.get("nights"):
                print(f"\nheld-out check ({bt['nights']} nights from {tsdb.TEST_FROM}, neighbours from the "
                      f"{bt['train_nights']} training nights only), mean absolute error:")
                for m in ns.OUTCOMES:
                    e = bt[m]
                    print(f"  {m:<13} similar nights {e['knn']:>6}   training mean {e['train_mean']:>6}"
                          f"   same-weekday mean {e['weekday_mean']:>6}")
        elif args.cmd == "tsdb" and args.action == "dishes":
            from . import dishsearch as ds

            index = ds.init(conn)
            n = ds.build(conn)
            print(f"{n} dishes embedded ({ds.DIM} dims, index: {index}).")
            summ = ds.summarize(ds.evidence_rows(conn))
            chk = ds.popularity_check({d: x["log_ratio"] for d, x in summ.items()})
            print(f"\npopularity check (observed vs replay, {chk['dishes']} dishes, {chk.get('nights', 0)} dish-nights): "
                  f"tau {chk['tau']}, noise sd {chk['sigma']}, detected {chk['detected']}\n  {chk['note']}")
            if args.name:
                r = ds.evidence(conn, args.name, args.station, args.role, k=args.k)
                print(f"\nlook-alikes of {args.name!r}:")
                cols = ["name", "station", "role", "distance", "nights", "eaten_per_night", "per_student"]
                print(tsdb.format_table(cols, [[x[c] for c in cols] for x in r["neighbors"]]))
                print(f"\nnew-dish popularity spread in plausible worlds: ±{r['new_dish_sd']:.0%} "
                      f"(default ±{r['default_sd']:.0%}, look-alikes differ by ±{(r['neighbor_spread'] or 0):.0%})")
        elif args.cmd == "tsdb" and args.action == "searches":
            from . import searchlog

            searchlog.init(conn)
            conn.commit()
            t = searchlog.totals(conn)
            print(f"{t['searches']} saved searches ({t['optimize_runs']} optimize, {t['worlds_runs']} worlds), "
                  f"{t['trials']} trials, {t['worlds']} world rows\n")
            rows = searchlog.list_searches(conn, args.date, limit=args.k * 4)
            cols = ["search_id", "kind", "menu_date", "weekday", "trials", "best_validated_waste_pct",
                    "best_validated_stockout_diff", "source"]
            print(tsdb.format_table(cols, [[x[c] for c in cols] for x in rows]))
            ch = searchlog.recurring_changes(conn, args.date)
            if ch:
                print("\nchanges that keep showing up on the frontier:")
                cols = ["label", "searches", "frontier_plans", "lower", "higher", "avg_multiplier", "waste_pct",
                        "stockout_diff_min"]
                print(tsdb.format_table(cols, [[x[c] for c in cols] for x in ch]))
        elif args.cmd == "tsdb" and args.action == "compress":
            tsdb.init_schema(conn)
            r = tsdb.compress_now(conn)
            mb = lambda b: f"{b / 1e6:.1f} MB"  # noqa: E731
            print(f"  {'table':<13} {'row data':>9} {'columnstore':>12} {'ratio':>6}   {'on disk now':>11}")
            for t in r["after"]:
                b, a = r["rowstore"].get(t, 0), r["columnstore"].get(t, 0)
                print(f"  {t:<13} {mb(b):>9} {mb(a):>12} {b / a if a else 0:>5.1f}x   {mb(r['after'][t]):>11}")
            b, a = sum(r["rowstore"].values()), sum(r["columnstore"].values())
            print(f"  {'total':<13} {mb(b):>9} {mb(a):>12} {b / a if a else 0:>5.1f}x   {mb(sum(r['after'].values())):>11}"
                  f"   ({r['chunks']} chunks; the newest week stays row-store until it is a day old)")
        elif args.cmd == "tsdb" and args.action == "track":
            from .calibration import binomial_range

            tsdb.score_forecasts(conn)
            conn.commit()
            cols, rows = tsdb.run_query(conn, "track")
            print(f"{tsdb.QUERIES['track'][0]}\n")
            out = []
            for r in rows:
                rec = dict(zip(cols, r))
                lo, hi = binomial_range(rec["nights"])
                k = rec["waste_inside"]
                verdict = "too narrow" if k < lo else "too wide" if k > hi else "as calibrated"
                out.append([*r, f"{verdict} ({lo}-{hi} expected)"])
            print(tsdb.format_table([*cols, "waste range"], out))
            print("\nweeks start Thursday; backtest weeks 2026-10-29 and 2026-11-05 are the held-out split "
                  "(calibrated is scored only there); ledger = live forecasts issued before the night")
        elif args.cmd == "tsdb" and args.action == "sweep":
            import os
            from concurrent.futures import ProcessPoolExecutor

            from . import nightsweep
            from .service import Service
            from .store import Store

            if not args.date:
                raise SystemExit("--date is required for sweep (a menu date, e.g. 2026-11-13)")
            if not args.report_only:
                workers = int(os.environ.get("DCSIM_WORKERS") or os.cpu_count() or 1)
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    nightsweep.load(conn, Service(Store(), pool), args.date, args.nights)
            cols, rows = nightsweep.dish_report(conn, args.date, args.dish, args.top)
            what = f"dishes matching '{args.dish}'" if args.dish else "the dishes out of stock longest"
            n = rows[0][cols.index("nights")] if rows else 0
            print(f"\n{args.date}: {what}, across {n} simulated nights in Tiger (one SQL statement: nightsweep.DISH_SQL)"
                  f"\n\n{tsdb.format_table(cols, rows)}" if rows else f"no sweep rows for {args.date}"
                  + (f" matching '{args.dish}'" if args.dish else "") + " in Tiger")
        elif args.cmd == "tsdb" and args.action == "query":
            title = tsdb.QUERIES.get(args.query, ("SQL", ""))[0]
            cols, rows = tsdb.run_query(conn, args.query)
            print(f"{title}\n\n{tsdb.format_table(cols, rows)}" if cols else "ok")
        elif args.cmd == "calibrate" and args.situations_only:
            from .calibration import refit_situations

            test_from = dt.date.fromisoformat(args.test_from) if args.test_from else tsdb.TEST_FROM
            r = refit_situations(conn, test_from)
            print(f"-> dcsim.calibrations ({r['calibration_id']}, from {r['derived_from']})")
        elif args.cmd == "calibrate":
            import os
            from concurrent.futures import ProcessPoolExecutor

            from .calibration import calibrate, report_path
            from .service import Service
            from .store import Store

            test_from = dt.date.fromisoformat(args.test_from) if args.test_from else tsdb.TEST_FROM
            with ProcessPoolExecutor(max_workers=os.cpu_count() or 1) as pool:
                rep = calibrate(Service(Store(), pool), conn, pool, test_from)
            s = rep["scores"]
            lo, hi = s["expected_inside80_if_calibrated"]
            print(f"\nheld-out nights inside the 10-90% range (a calibrated forecast gets {lo}-{hi} of {rep['split']['test'][2]}):")
            for k in ("waste_lb", "stockout_min", "students"):
                nv, cv = s["forecast_naive"][k], s["forecast_calibrated"][k]
                print(f"  {k:<13} naive {nv['inside80']:>2}/{nv['nights']} (bias {nv['bias']:+.1f}, CRPS {nv['crps']:.1f})"
                      f"   calibrated {cv['inside80']:>2}/{cv['nights']} (bias {cv['bias']:+.1f}, CRPS {cv['crps']:.1f})")
            r = rep["recovery"]
            if r.get("available"):
                print(f"recovery (synthetic self-test): production mean {r['production_mean']['fitted']:.3f} vs true "
                      f"{r['production_mean']['true']:.3f}, per-night corr {r['production_per_night']['corr']:.2f}; "
                      f"crowd sd {r['crowd_log_sd']['fitted']:.3f} vs true {r['crowd_log_sd']['true']:.3f}")
            print(f"-> {report_path()} and dcsim.calibrations ({rep['calibration_id']})")


def _ai(args) -> None:
    from .ai import LLM
    from .service import Service
    from .store import Store

    store = Store()
    llm = LLM(store)
    print(f"[llm: {llm.provider} / {llm.model}]")
    if args.cmd == "personas":
        from .ai import personas

        if not os.environ.get("DCSIM_PERSONAS_LLM"):
            print("LLM persona generation is paused (see LLM.md, 'Personas'). The engine uses the hand-written "
                  "data/worcester/personas.json. Set DCSIM_PERSONAS_LLM=1 to run it anyway.")
            return
        if args.action == "generate":
            data = personas.generate(llm, args.n)
            path = personas.write_raw(data)
            for p in data["personas"]:
                print(f"  {p['share']:6.1%}  {p['id']:<24} {p['diet']:<10} {p['label']}")
            for d in data["review"]["dropped"]:
                print(f"  dropped {d['id']}: {'; '.join(d['problems'])}")
            for f in data["review"]["failed_batches"]:
                print(f"  failed {f}")
            if data["status"] == "incomplete":
                print(f"  INCOMPLETE: no valid persona for {data['review']['missing_diets']}; adopt will refuse")
            print(f"{len(data['personas'])} personas ({data['source']}) -> {path}\n"
                  "Review it (stereotypes, variety, numbers), then `dcsim personas adopt`.")
        else:
            print(f"adopted -> {personas.adopt()}  (cached results for the old personas no longer match)")
        return
    if args.cmd == "ai-status":
        print(json.dumps(llm.status(), indent=2))
        return

    if args.cmd == "similar":
        from .ai.similar import similar_dishes

        print(json.dumps(similar_dishes(llm, args.date or available_dates()[0], args.name, args.station, args.role, args.k),
                         indent=2))
        return

    if args.cmd == "agent":
        _agent(llm, args)
        return

    if args.cmd == "ask":
        from .ai.ask import ask

        r = ask(llm, args.question, args.date or available_dates()[0])
        print(json.dumps({k: r[k] for k in r if k not in ("model",)}, indent=2))
        return

    svc = Service(Store())
    cfg = _config(args.date, args.preset)
    nights = svc.ensure_nights(cfg, getattr(args, "nights", 30))
    h = config_hash(cfg)
    if args.cmd == "explain":
        from .ai.explain import explain

        base = load_baseline(cfg.date)
        svc.ensure_nights(base, args.nights)
        r = explain(llm, svc, config_hash(base), h, args.nights)
        print("\n".join(f"  - {c}" for c in r["facts"]["changes"]))
        print(f"\n{r['text']}\n  [{r['source']}{': ' + r['reason'] if r.get('reason') else ''}]")
        return

    from .ai.narrate import TRACES, Narrator, highlights

    seed = median_night(nights)
    narrator = Narrator(svc, llm)
    ids = args.student or [x["student_id"] for x in highlights(cfg, TRACES.get(svc, h, seed), args.k)]
    for sid in ids:
        _, c = narrator.load(h, seed, sid)
        out = narrator.narrate_sync(h, seed, sid)
        print(f"\n{c['title']}  (night {seed})")
        for e in c["events"]:
            print(f"  {e['clock']:>8}  {e['text']}")
        print(f"  > {out['text']}\n    [{out['source']}{': ' + out['reason'] if out.get('reason') else ''}]")


if __name__ == "__main__":
    main()


def _agent(llm, args) -> None:
    from concurrent.futures import ProcessPoolExecutor

    import uuid

    from .ai.agent import AGENT_NAME, history_entry, run_agent
    from .service import Service
    from .store import Store

    def emit(kind: str, d: dict) -> None:
        if kind == "goal":
            g = d["goal"]
            print(f"Goal: {g['summary']}  [{d['source']}; {len(d['protected'])} protected dishes]")
        elif kind == "try_start":
            print(f"\n> Try {d['id']}: {d['note']}")
            for m in d["moves"]:
                print(f"    {m}")
        elif kind == "try":
            for c in d["check"]["checks"]:
                print(f"    {'PASS' if c['ok'] else 'FAIL'}  {c['text']}")
        elif kind in ("notice", "thought"):
            print(f"  ({d['text']})")
        elif kind == "tiger":
            what = f"{d.get('row_count')} rows" if not d.get("error") else f"error: {d['error']}"
            print(f"  [Tiger] {d['why']} -> {what}")
        elif kind == "validate_start":
            print(f"\nChecking try {d['try_id']} on unseen nights {d['seeds'][0]}-{d['seeds'][1]}...")

    workers = int(os.environ.get("DCSIM_WORKERS", os.cpu_count() or 1))
    pool = ProcessPoolExecutor(workers) if workers > 1 else None
    store = Store()
    date = args.date or available_dates()[0]
    try:
        print(f"{AGENT_NAME} is on it.")
        r = run_agent(Service(store, pool), llm, args.goal, date, n_nights=args.nights, max_tries=args.tries, emit=emit)
    finally:
        if pool:
            pool.shutdown()
    entry = history_entry(r)
    store.put_agent_history(uuid.uuid4().hex[:12], entry.get("kind", "plan"), args.goal, date, entry)
    if args.json:
        print(json.dumps(r, indent=2))
        return
    if r.get("kind") == "question":
        print(f"\n{r['answer']}")
        if r.get("caveats"):
            print(f"Note: {r['caveats']}")
        print(f"\n{len(r['tiger_lookups'])} Tiger lookups [{r['source']}{' / ' + r['model'] if r.get('model') else ''}]")
        return
    h = r["holdout"]
    print(f"\nPlan: try {r['chosen']} ({r['chosen_by']}) - goal {'MET' if r['check']['met'] else 'NOT met'}; "
          f"on unseen nights: {'MET' if h['met'] else 'NOT met'} (waste {h['waste_pct']:+.1f}%)")
    for c in h["checks"]:
        if not c["ok"]:
            print(f"    unseen nights FAIL  {c['text']}")
    print(f"\n{r['reasoning']}")
    if r["caveats"]:
        print(f"Watch: {r['caveats']}")
    print(f"\n{len(r['changed_dishes'])} dishes changed; {r['nights_simulated']} nights simulated; "
          f"config {r['config_hash'][:12]} [{r['source']}{' / ' + r['model'] if r['model'] else ''}]")
