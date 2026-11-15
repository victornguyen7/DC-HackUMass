// What-If Lab: "Optimize plan" (frontier of kitchen plans) and "Plausible worlds" (win rate + tornado).
import { useEffect, useReducer, useRef, useState } from "react";
import type { ApiLevers } from "../lib/data";
import { fmt1, fmtInt, signed } from "../lib/format";
import {
  frontierChart, leverText, loadSearch, pastSearches, runSearch, SEARCH_IDLE, searchReducer, winText,
  type FrontierPlan, type OptimizeResult, type RecurringChange, type SavedSearch, type SearchState, type TigerSaved,
  type TornadoBar, type WorldsResult,
} from "../lib/planSearch";

function useSearch<R>(kind: "optimize" | "worlds") {
  const [state, dispatch] = useReducer(searchReducer<R>, SEARCH_IDLE as SearchState<R>);
  const stop = useRef<(() => void) | null>(null);
  useEffect(() => () => stop.current?.(), []);
  const start = async (body: unknown) => {
    stop.current?.();
    dispatch({ event: "trial", data: { n: 0 } }); // marks it running; trial 0 is dropped by the first real trial
    try {
      stop.current = await runSearch(kind, body, dispatch);
    } catch (e) {
      dispatch({ event: "error", data: { message: (e as Error).message } });
    }
  };
  return { state: { ...state, trials: state.trials.filter((t) => t.n > 0) }, start };
}

function Progress({ label, done, total }: { label: string; done: number; total: number }) {
  return (
    <div style={{ margin: "8px 0" }} role="status">
      <div className="row hint" style={{ justifyContent: "space-between" }}><span>{label}</span><span className="num">{done}/{total}</span></div>
      <div style={{ height: 6, background: "var(--bg-muted)", borderRadius: 3 }}>
        <div style={{ height: 6, width: `${total ? (100 * done) / total : 0}%`, background: "var(--maroon)", borderRadius: 3, transition: "width .3s" }} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- optimize

export function OptimizePanel({ date, apiUp, onLoad }: { date: string; apiUp: boolean; onLoad: (levers: ApiLevers) => void }) {
  const { state, start } = useSearch<OptimizeResult>("optimize");
  const last = state.trials[state.trials.length - 1];
  const [opened, setOpened] = useState<OptimizeResult | null>(null);
  const r = opened ?? state.result;
  return (
    <div>
      <p className="hint" style={{ marginTop: 0 }}>
        Searches 80 kitchen plans (batch size and reorder point by station, plus the 5 most-wasted dishes) on 10 nights each,
        scoring waste and stockouts together. About a minute.
      </p>
      <button className="btn primary" disabled={!apiUp || state.status === "running"}
        title={apiUp ? "" : "Needs the API (make server)"} onClick={() => { setOpened(null); start({ date }); }}>
        {state.status === "running" ? "Searching…" : state.result ? "Search again" : "Optimize plan"}
      </button>
      {!apiUp && <div className="hint" style={{ marginTop: 6 }}>Needs the API. Start it with <code>make server</code>.</div>}
      {state.status === "running" && last && !state.progress && (
        <Progress label={`Plan ${last.n}: ${fmtInt(last.waste)} wasted${last.best_waste != null ? ` · best within limits ${fmtInt(last.best_waste)}` : ""}`} done={last.n} total={last.of} />
      )}
      {state.status === "running" && state.progress && <Progress label="Re-checking the best plans on nights the search never saw" {...state.progress} />}
      {state.status === "error" && <div className="alert warn" role="alert" style={{ marginTop: 8 }}><b>Search failed</b>{state.error}</div>}
      {opened && (
        <div className="row hint" style={{ marginTop: 10, justifyContent: "space-between" }}>
          <span>Saved search from {when(opened.finished_at)}, read back from Tiger</span>
          <button className="btn ghost" onClick={() => setOpened(null)}>Hide saved search</button>
        </div>
      )}
      {r && state.status !== "running" && <OptimizeResultView r={r} onLoad={onLoad} />}
      {apiUp && state.status !== "running" && (
        <PastSearches date={date} refresh={state.result?.tiger?.search_id ?? ""} current={r?.search_id ?? r?.tiger?.search_id}
          onOpen={setOpened} />
      )}
    </div>
  );
}

const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");

/** Past optimizer runs for this date and the levers that keep showing up on the frontier, from Tiger.
 *  Shows nothing when Tiger isn't reachable or nothing was saved yet. */
function PastSearches({ date, refresh, current, onOpen }: {
  date: string; refresh: string; current?: string; onOpen: (r: OptimizeResult) => void;
}) {
  const [data, setData] = useState<{ searches: SavedSearch[]; changes: RecurringChange[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    pastSearches(date).then((d) => { if (live) setData(d); });
    return () => { live = false; };
  }, [date, refresh]);
  if (!data || (data.searches.length === 0 && data.changes.length === 0)) return null;
  const open = (id: string) => loadSearch(id).then((r) => { setError(null); onOpen(r); }, (e) => setError((e as Error).message));
  return (
    <div style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
      {data.changes.length > 0 && (
        <>
          <h3 style={{ margin: "0 0 4px" }}>What keeps showing up on the frontier</h3>
          <div className="hint" style={{ marginBottom: 6 }}>Across every saved search, from <code>dcsim.search_trials</code> in Tiger.</div>
          <ul style={{ margin: "0 0 10px", paddingLeft: 18, fontSize: 12 }}>
            {data.changes.slice(0, 5).map((c) => (
              <li key={c.lever}><b>{c.label}</b>: {leverText(c)} (avg ×{c.avg_multiplier.toFixed(2)})</li>
            ))}
          </ul>
        </>
      )}
      {data.searches.length > 0 && (
        <>
          <h3 style={{ margin: "0 0 4px" }}>Past searches for this date</h3>
          <div className="stack" style={{ gap: 4 }}>
            {data.searches.slice(0, 5).map((s) => (
              <button key={s.search_id} className="btn ghost" style={{ justifyContent: "space-between", textAlign: "left", width: "100%" }}
                disabled={s.search_id === current} onClick={() => open(s.search_id)}>
                <span>{when(s.finished_at)} · {s.trials} plans</span>
                <span className="num">{s.best_validated_waste_pct != null
                  ? `${signed(s.best_validated_waste_pct, fmt1)}% waste · ${signed(s.best_validated_stockout_diff ?? 0, fmt1)} min`
                  : "no frontier plan"}</span>
              </button>
            ))}
          </div>
          {error && <div className="hint" role="alert">Couldn't open it: {error}</div>}
        </>
      )}
    </div>
  );
}

/** "Saved to Tiger" after a search, or why it wasn't. */
function SavedChip({ t }: { t?: TigerSaved }) {
  if (!t) return null;
  return t.saved
    ? <span className="chip good" title={`dcsim.plan_searches ${t.search_id}`}>Saved to Tiger ✓</span>
    : <span className="hint" title={t.reason}>Not saved to Tiger{t.reason?.includes("off") ? " (writes off)" : ""}</span>;
}

export function OptimizeResultView({ r, onLoad }: { r: OptimizeResult; onLoad: (levers: ApiLevers) => void }) {
  // smallest stockout cost first: the trade-off a manager is most likely to take
  const plans = r.frontier.filter((f) => Object.keys(f.levers.kitchen ?? {}).length > 0).sort((a, b) => a.stockout_min - b.stockout_min);
  return (
    <div style={{ marginTop: 12 }}>
      <FrontierChart r={r} />
      <div className="hint" style={{ margin: "4px 0 10px" }}>
        Each dot is a plan ({r.trials} searched, {fmtInt(r.nights_searched)} nights). The line joins the plans no other plan
        beats on both waste and stockouts. Today: {fmtInt(r.baseline.waste)} portions wasted, {fmt1(r.baseline.stockout_min)} min out of stock.
      </div>
      {plans.length === 0 && <div className="alert info"><b>Today's plan is already on the frontier</b>No searched plan wasted less without more stockouts.</div>}
      {r.levers === null && plans.length > 0 && (
        <div className="alert info" style={{ marginBottom: 8 }}>
          <b>Less waste costs stockouts here</b>No plan cut waste within {fmt1(r.limits.stockout_min_total)} stockout minutes. Smaller batches need
          more batches, and the cooks fall behind. Pick the trade-off you can live with.
        </div>
      )}
      <div className="stack" style={{ gap: 8 }}>
        {plans.map((f) => <FrontierCard key={f.n} f={f} baseStockout={r.baseline.stockout_min} onLoad={onLoad} />)}
      </div>
      <div className="hint" style={{ marginTop: 8 }}>{r.method}</div>
      <div style={{ marginTop: 6 }}><SavedChip t={r.tiger} /></div>
    </div>
  );
}

function FrontierCard({ f, baseStockout, onLoad }: { f: FrontierPlan; baseStockout: number; onLoad: (levers: ApiLevers) => void }) {
  const [all, setAll] = useState(false);
  const v = f.validation;
  return (
    <div className="card" style={{ boxShadow: "none", padding: 10 }}>
      <div className="row" style={{ alignItems: "baseline" }}>
        <b style={{ flex: 1 }}>{signed(v?.waste_pct ?? f.waste_pct, fmt1)}% waste · {signed(v?.stockout_diff_min ?? f.stockout_min - baseStockout, fmt1)} stockout min</b>
        {f.within && <span className="chip good">within limits</span>}
      </div>
      <div className="hint">
        {v ? `Median change on ${v.seeds[1] - v.seeds[0] + 1} nights the search never saw` : "On the search nights (not re-checked on unseen nights)"}
        {" "}· on the search nights: {fmtInt(f.waste)} wasted, {fmt1(f.stockout_min)} min out of stock
      </div>
      <ul style={{ margin: "6px 0", paddingLeft: 18, fontSize: 12 }}>
        {(all ? f.changes : f.changes.slice(0, 3)).map((c) => <li key={c}>{c}</li>)}
      </ul>
      <div className="row">
        {f.changes.length > 3 && <button className="btn ghost" onClick={() => setAll(!all)}>{all ? "Fewer" : `All ${f.changes.length} changes`}</button>}
        <button className="btn" onClick={() => onLoad(f.levers)}>Load into builder</button>
      </div>
    </div>
  );
}

/** Waste (y) vs stockout minutes (x): every trial, the frontier line, today's plan and the limit. */
function FrontierChart({ r }: { r: OptimizeResult }) {
  const c = frontierChart(r);
  const W = 760, H = 260, L = 52, B = 30, T = 8, R = 12;
  const x = (v: number) => L + ((W - L - R) * v) / c.xMax;
  const y = (v: number) => T + (H - T - B) * (1 - (v - c.yMin) / (c.yMax - c.yMin));
  const front = [...r.frontier].sort((a, b) => a.stockout_min - b.stockout_min);
  const ticks = [0, 0.5, 1].map((f) => c.yMin + f * (c.yMax - c.yMin));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img"
      aria-label={`Trade-off: ${r.frontier.length} frontier plans; today's plan wastes ${fmtInt(r.baseline.waste)} portions with ${fmt1(r.baseline.stockout_min)} stockout minutes`}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="var(--border)" />
          <text x={L - 6} y={y(t) + 4} textAnchor="end" fontSize="10" fill="var(--text-3)">{fmtInt(t)}</text>
        </g>
      ))}
      {[0, 0.5, 1].map((f) => (
        <text key={f} x={x(f * c.xMax)} y={H - 12} textAnchor="middle" fontSize="10" fill="var(--text-3)">{fmtInt(f * c.xMax)}</text>
      ))}
      <text x={(W + L) / 2} y={H - 1} textAnchor="middle" fontSize="10" fill="var(--text-3)">minutes out of stock →</text>
      <text x={10} y={T + 4} fontSize="10" fill="var(--text-3)" transform={`rotate(-90 10 ${T + 4})`} textAnchor="end">portions wasted →</text>
      <line x1={x(r.limits.stockout_min_total)} x2={x(r.limits.stockout_min_total)} y1={T} y2={H - B} stroke="var(--text-3)" strokeDasharray="4 3" />
      <text x={x(r.limits.stockout_min_total) + 4} y={H - B - 4} fontSize="10" fill="var(--text-3)">stockout limit</text>
      {c.pts.map((p, i) => <circle key={i} cx={x(p.x)} cy={y(p.y)} r={2.5} fill="var(--text-3)" opacity={0.35} />)}
      <polyline fill="none" stroke="var(--maroon)" strokeWidth={2} points={front.map((f) => `${x(f.stockout_min)},${y(f.waste)}`).join(" ")} />
      {front.map((f) => <circle key={f.n} cx={x(f.stockout_min)} cy={y(f.waste)} r={4.5} fill="var(--maroon)"><title>{`${signed(f.waste_pct, fmt1)}% waste, ${fmt1(f.stockout_min)} stockout min`}</title></circle>)}
      <circle cx={x(r.baseline.stockout_min)} cy={y(r.baseline.waste)} r={6} fill="none" stroke="var(--text)" strokeWidth={2} />
      <text x={x(r.baseline.stockout_min) + 9} y={y(r.baseline.waste) + 4} fontSize="11" fill="var(--text-2)">today</text>
    </svg>
  );
}

// ---------------------------------------------------------------- plausible worlds

const METRICS = [["waste_total", "less waste"], ["stockout_min_total", "fewer stockout minutes"], ["wait_p90", "shorter peak wait"]] as const;

export function WorldsPanel({ request, apiUp }: { request: unknown | null; apiUp: boolean }) {
  const { state, start } = useSearch<WorldsResult>("worlds");
  const r = state.result;
  const key = JSON.stringify(request);
  const [ranFor, setRanFor] = useState<string | null>(null);
  const stale = r && ranFor !== key;
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Plausible worlds</h2>
          <div className="hint">What if our guesses are off? Each world draws the crowd size, the rush timing and every dish's popularity.</div>
        </div>
        <button className="btn" disabled={!apiUp || !request || state.status === "running"}
          title={!apiUp ? "Needs the API (make server)" : !request ? "Open this plan from the builder to test it" : ""}
          onClick={() => { setRanFor(key); start({ request, n_worlds: 100, tornado_worlds: 50 }); }}>
          {state.status === "running" ? "Testing…" : r && !stale ? "Test again" : "Test in 100 worlds"}
        </button>
      </div>
      {state.status === "running" && state.progress && <Progress label="Simulating plausible worlds" {...state.progress} />}
      {state.status === "error" && <div className="alert warn" role="alert"><b>Couldn't test it</b>{state.error}</div>}
      {r && !stale && <WorldsResultView r={r} />}
      {!r && state.status === "idle" && (
        <div className="hint">Runs this plan and today's in the same 100 worlds (about 30 s), then shows which guess moves waste the most.</div>
      )}
    </section>
  );
}

export function WorldsResultView({ r }: { r: WorldsResult }) {
  const u = r.uncertainty;
  return (
    <div>
      <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", marginBottom: 10 }}>
        {METRICS.map(([k, what]) => {
          const m = r.vs_baseline.metrics[k];
          if (!m) return null;
          return (
            <div key={k} className="card kpi" style={{ boxShadow: "none" }}>
              <div className="label">{what}</div>
              <div className="value" style={{ fontSize: 26 }}>{winText(r.vs_baseline, k)}</div>
              <div className="range">of {r.n_worlds} worlds · median {signed(m.diff.p50, k === "wait_p90" ? fmt1 : fmtInt)}</div>
              <span className={`chip ${m.verdict === "better" ? "good" : m.verdict === "worse" ? "bad" : ""}`}>
                {m.verdict === "same" ? "no real difference" : m.verdict}
              </span>
            </div>
          );
        })}
      </div>
      <div className="hint" style={{ marginBottom: 10 }}>
        Spread used: crowd ±{Math.round(u.crowd_sd * 100)}%, rush ±{u.peak_shift_sd_min} min, dish popularity ±{Math.round(u.popularity_sd * 100)}% (one standard deviation)
        {u.new_dish_popularity_sd != null && r.new_dishes?.length ? (
          <>, new dish{r.new_dishes.length > 1 ? "es" : ""} ±{Math.round(u.new_dish_popularity_sd * 100)}%
            {r.new_dish_sd_source === "tiger" ? " (from how similar dishes sold, in Tiger)" : ""}</>
        ) : null}.
        {" "}Both plans face the same worlds. The spreads are judgement, not data.
      </div>
      {r.tornado && <Tornado bars={r.tornado.bars} n={r.tornado.n_worlds} />}
      <div style={{ marginTop: 6 }}><SavedChip t={r.tiger} /></div>
    </div>
  );
}

/** Waste range (p10-p90) on today's plan when only one guess varies, widest first. */
function Tornado({ bars, n }: { bars: TornadoBar[]; n: number }) {
  const lo = Math.min(...bars.map((b) => b.p10)), hi = Math.max(...bars.map((b) => b.p90));
  const pad = (hi - lo) * 0.05 || 1;
  const x = (v: number) => `${(100 * (v - lo + pad)) / (hi - lo + 2 * pad)}%`;
  return (
    <div>
      <h3 style={{ margin: "4px 0" }}>Which guess matters most for waste</h3>
      <div className="hint" style={{ marginBottom: 6 }}>Waste on today's plan, 10th–90th percentile of {n} worlds, with only that guess varying.</div>
      {bars.map((b) => (
        <div key={b.input} style={{ display: "grid", gridTemplateColumns: "minmax(110px, 160px) 1fr", gap: 10, alignItems: "center", padding: "4px 0", fontSize: 12 }}>
          <div>
            <div>{b.label}</div>
            <div className="faint" style={{ fontSize: 11 }}>{b.input === "seed" ? "always there" : `ask for: ${b.data_request}`}</div>
          </div>
          <div style={{ position: "relative", height: 18, background: "var(--bg-muted)", borderRadius: 4 }}
            title={`${fmtInt(b.p10)}–${fmtInt(b.p90)} portions (median ${fmtInt(b.p50)})`}>
            <div style={{ position: "absolute", left: x(b.p10), width: `calc(${x(b.p90)} - ${x(b.p10)})`, top: 3, height: 12, borderRadius: 3,
              background: b.input === "seed" ? "var(--text-3)" : "var(--maroon)", opacity: 0.85 }} />
            <div style={{ position: "absolute", left: x(b.p50), top: 0, width: 2, height: 18, background: "var(--text)" }} />
          </div>
        </div>
      ))}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(110px, 160px) 1fr", gap: 10 }}>
        <span />
        <div className="row hint" style={{ justifyContent: "space-between" }}><span>{fmtInt(lo)}</span><span>portions wasted</span><span>{fmtInt(hi)}</span></div>
      </div>
    </div>
  );
}
