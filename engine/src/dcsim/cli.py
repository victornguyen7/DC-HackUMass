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
    tq.add_argument("action", choices=["init", "load", "query", "compress", "observe"],
                    help="init: create schema · load: recorded history -> observed rows · query: run a named query or SQL"
                         " · compress: convert older chunks to the columnstore now and report sizes"
                         " · observe: generate + load the SYNTHETIC observation for --date (scores its logged forecasts)")
    tq.add_argument("--date", help="night for `observe`")
    tq.add_argument("query", nargs="?", default="status", help="query name (status, situations, weekdays, pans, "
                    "dishes, coverage, nights, calibrations) or a SQL string")
    fc = sub.add_parser("forecast", help="log a naive + calibrated forecast for a night in the append-only Tiger ledger")
    fc.add_argument("--date", required=True)
    fc.add_argument("--preset", choices=sorted(PRESETS))
    ca = sub.add_parser("calibrate", help="fit on the early recorded weeks, score forecasts on held-out weeks (uses Tiger)")
    ca.add_argument("--test-from", default=None, help="first held-out date (default 2026-10-29)")
    args = ap.parse_args(argv)

    if args.cmd in ("tsdb", "calibrate", "forecast"):
        _tiger(args)
        return

    if args.cmd in ("personas", "narrate", "ask", "explain", "similar", "ai-status"):
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
            for path in import_menus(raw, d / "stations.json", d / "overrides.json", d / "menus"):
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
        if args.cmd == "calibrate" or getattr(args, "action", None) in ("init", "load"):
            ts = tsdb.init_schema(conn)
            print(f"schema dcsim ready ({'TimescaleDB hypertables + continuous aggregate' if ts else 'plain PostgreSQL'})")
        if args.cmd == "tsdb" and args.action == "load":
            n = tsdb.load_observed(conn)
            print("observed history loaded: " + ", ".join(f"{v:,} {k}" for k, v in n.items()))
        elif args.cmd == "forecast":
            from .calibration import issue_forecasts
            from .service import Service
            from .store import Store

            cfg = _config(args.date, args.preset)
            for f in issue_forecasts(Service(Store()), conn, cfg):
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
        elif args.cmd == "tsdb" and args.action == "compress":
            tsdb.init_schema(conn)
            r = tsdb.compress_now(conn)
            mb = lambda b: f"{b / 1e6:.1f} MB"  # noqa: E731
            print(f"  {'table':<12} {'row data':>9} {'columnstore':>12} {'ratio':>6}   {'on disk now':>11}")
            for t in r["after"]:
                b, a = r["rowstore"].get(t, 0), r["columnstore"].get(t, 0)
                print(f"  {t:<12} {mb(b):>9} {mb(a):>12} {b / a if a else 0:>5.1f}x   {mb(r['after'][t]):>11}")
            b, a = sum(r["rowstore"].values()), sum(r["columnstore"].values())
            print(f"  {'total':<12} {mb(b):>9} {mb(a):>12} {b / a if a else 0:>5.1f}x   {mb(sum(r['after'].values())):>11}"
                  f"   ({r['chunks']} chunks; the newest week stays row-store until it is a day old)")
        elif args.cmd == "tsdb" and args.action == "query":
            title = tsdb.QUERIES.get(args.query, ("SQL", ""))[0]
            cols, rows = tsdb.run_query(conn, args.query)
            print(f"{title}\n\n{tsdb.format_table(cols, rows)}" if cols else "ok")
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
