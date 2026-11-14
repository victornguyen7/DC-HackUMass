import { useEffect, useReducer, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { TopBar } from "../components/Shell";
import { SamAvatar } from "../components/SamAvatar";
import {
  AGENT_IDLE, agentReducer, deleteHistory, listHistory, openHistory, rememberLocal, runAgent,
  type AgentFinal, type AgentState, type AgentTry, type Check, type HistoryItem, type TigerLookup,
} from "../lib/agent";
import { loadApiResult, saveScenario } from "../lib/data";
import { dateLabel, fmt1, fmtInt, stationName } from "../lib/format";
import { useApiUp, useAsync } from "../lib/hooks";
import { useApp } from "../lib/store";

const EXAMPLES = [
  "Cut waste 20% without any vegetarian dish running out",
  "Cut waste 15% and keep every halal dish stocked",
  "Cut waste 10% without longer lines or missed meals",
  "Which dishes ran out on rainy Fridays?",
];

const pct = (n: number) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${fmt1(Math.abs(n))}%`;

/** Sam the Minuteman: give a goal, the agent runs the simulator, checks, adjusts, and hands back a plan. */
export default function Sam() {
  const { date } = useApp();
  const apiOn = useApiUp();
  const [goal, setGoal] = useState(EXAMPLES[0]);
  const [s, dispatch] = useReducer(agentReducer, AGENT_IDLE);
  const stop = useRef<(() => void) | null>(null);
  useEffect(() => () => stop.current?.(), []);
  const [viewing, setViewing] = useState<AgentFinal | null>(null);
  const [tick, setTick] = useState(0);
  const history = useAsync(() => listHistory(apiOn), [apiOn, tick]);
  useEffect(() => {
    if (s.final) { rememberLocal(s.final); setTick((t) => t + 1); }
  }, [s.final]);

  const go = async (text = goal) => {
    if (text.trim().length < 5) return;
    stop.current?.();
    setViewing(null);
    dispatch({ event: "start", data: { goal_text: text } });
    dispatch({ event: "progress", data: { what: "starting", done: 0, total: 1 } });
    try {
      stop.current = await runAgent(text.trim(), date, dispatch);
    } catch (e) {
      dispatch({ event: "error", data: { message: (e as Error).message } });
    }
  };
  const running = s.status === "running";

  return (
    <>
      <TopBar title="Ask Sam" sub="A planning agent that tests its own plans on simulated nights before you try them" showBadges={false} />
      <div className="content sam-page">
       <div className="sam-layout">
        <div className="stack" style={{ minWidth: 0 }}>
        <section className="card sam-hero" aria-label="Give Sam a goal">
          <SamAvatar size={72} busy={running} />
          <div className="sam-hero-body">
            <div className="overline">Planning agent · {dateLabel(date)}</div>
            <h2>Sam the Minuteman</h2>
            <p className="muted">Tell Sam what tonight's kitchen should achieve. Sam checks the recorded history in Tiger Data,
              proposes batch-plan changes, runs each one on simulated nights (at most 5 tries), adjusts, and hands you a production
              plan and its reasons. Or ask a question about past nights and Sam answers it from Tiger.</p>
            <form className="sam-ask" onSubmit={(e) => { e.preventDefault(); go(); }}>
              <label htmlFor="sam-goal" className="sr-only">Goal</label>
              <input id="sam-goal" className="btn" value={goal} onChange={(e) => setGoal(e.target.value)} disabled={!apiOn || running}
                placeholder="e.g. cut waste 20% without any vegetarian dish running out" />
              <button className="btn primary" type="submit" disabled={!apiOn || running || goal.trim().length < 5}>
                {running ? "Sam is working…" : "Ask Sam"}
              </button>
            </form>
            <div className="row" style={{ marginTop: 8 }}>
              {EXAMPLES.map((x) => (
                <button key={x} className="pill" disabled={!apiOn || running} aria-pressed={goal === x} onClick={() => { setGoal(x); go(x); }}>{x}</button>
              ))}
            </div>
            {!apiOn && <div className="alert warn" role="status" style={{ marginTop: 10 }}><b>Sam needs the API</b>Start it with <code>make serve</code>. Sam runs real simulations, so there is no offline version.</div>}
          </div>
        </section>

        {viewing ? (
          <div className="stack">
            <div className="alert muted sam-viewing" role="status">
              <span>From history: <span className="sam-viewing-q">{viewing.goal_text}</span> · for {dateLabel(viewing.date)}{viewing.created_at ? ` · asked ${askedLabel(viewing.created_at)}` : ""}</span>
              <button className="btn ghost" onClick={() => setViewing(null)}>{s.status === "idle" ? "Close" : "Back to the current run"}</button>
            </div>
            {viewing.kind === "question" ? <AnswerCard f={viewing} /> : <PlanCard f={viewing} />}
            {(viewing.tiger_lookups?.length ?? 0) > 0 && <TigerCard lookups={viewing.tiger_lookups!} />}
          </div>
        ) : s.status !== "idle" && <Run s={s} />}
        </div>
        <HistoryPanel items={history.data ?? []} loading={history.loading} activeId={viewing?.history_id}
          onOpen={async (id) => {
            try { setViewing({ ...(await openHistory(id, apiOn)), history_id: id }); } catch (e) { alert((e as Error).message); }
          }}
          onDelete={async (id) => {
            const msg = await deleteHistory(id, apiOn);
            if (viewing?.history_id === id) setViewing(null);
            setTick((t) => t + 1);
            if (msg) alert(msg);
          }} />
       </div>
      </div>
    </>
  );
}

function Run({ s }: { s: AgentState }) {
  const running = s.status === "running";
  return (
    <div className="stack">
      {s.goal && <GoalCard s={s} />}
      {s.notices.map((n) => <div key={n} className="alert info" role="status">{n}</div>)}
      {s.tiger.length > 0 && <TigerCard lookups={s.tiger} live={running} />}
      <ol className="sam-tries" aria-label="Sam's tries">
        {s.tries.map((t) => <TryCard key={t.id} t={t} s={s} chosen={s.final?.chosen === t.id} />)}
      </ol>
      {running && <Working s={s} />}
      {s.status === "error" && <div className="alert bad" role="alert"><b>Sam couldn't finish</b>{s.error}</div>}
      {s.final && (s.final.kind === "question" ? <AnswerCard f={s.final} /> : <PlanCard f={s.final} />)}
    </div>
  );
}

function Working({ s }: { s: AgentState }) {
  const p = s.progress;
  const question = s.goal?.kind === "question";
  const what = question ? "Sam is asking Tiger Data" : s.validating ? `Checking try ${s.validating.try_id} on nights Sam never saw (${s.validating.seeds[0]}–${s.validating.seeds[1]})`
    : !s.goal ? "Reading the goal and simulating tonight's baseline"
    : p?.what.startsWith("try") ? `Simulating ${p.what}`
    : s.tries.length ? "Sam is reading the results and deciding what to try next"
    : s.tiger.length ? "Sam is reading the history and planning the first try" : "Sam is planning the first try";
  return (
    <div className="sam-working" role="status" aria-live="polite">
      <SamAvatar size={28} busy />
      <span>{what}…</span>
      {p && p.total > 1 && <progress max={p.total} value={p.done} aria-label="Nights simulated" />}
    </div>
  );
}

function GoalCard({ s }: { s: AgentState }) {
  const g = s.goal!;
  if (g.kind === "question") {
    return (
      <section className="card" aria-label="Question">
        <div className="overline">Question · answered from the recorded history in Tiger Data</div>
        <h3>{s.goalText}</h3>
      </section>
    );
  }
  const chips: string[] = [];
  if (g.waste_cut_pct) chips.push(`Waste −${g.waste_cut_pct}%`);
  if (g.protect_diets.length || g.protect_dishes.length)
    chips.push(`Protect ${[...g.protect_diets, ...(g.protect_dishes.length ? [`${g.protect_dishes.length} named dishes`] : [])].join(", ")} · ${s.protectedDishes?.length ?? 0} dishes`);
  if (g.keep_missed_meals) chips.push("Missed meals no worse");
  if (g.keep_wait) chips.push("Long wait no worse");
  return (
    <section className="card" aria-label="Goal">
      <div className="card-head">
        <div>
          <div className="overline">Goal</div>
          <h3>{g.summary || s.goalText}</h3>
        </div>
        <span className="chip">{s.goalSource === "mock" ? "read by keywords" : `read by ${s.model ?? "Gemini"}`}</span>
      </div>
      <div className="row">{chips.map((c) => <span key={c} className="chip maroon">{c}</span>)}</div>
      {s.baseline && (
        <div className="hint" style={{ marginTop: 8 }}>
          Tonight's plan, median of {s.nNights ?? 30} nights: {fmtInt(s.baseline.waste_total)} portions wasted · {fmtInt(s.baseline.missed_meal)} students
          missed a meal · 90% wait under {fmt1(s.baseline.wait_p90)} min. Protected means a dish may not run out more often than it does today.
          Budget: at most {s.maxTries ?? 5} tries.
        </div>
      )}
      {g.unsupported.length > 0 && <div className="hint">Sam can't plan for: {g.unsupported.join("; ")}</div>}
    </section>
  );
}

function CheckChip({ c }: { c: Check }) {
  return <span className={`chip ${c.ok ? "good" : "bad"}`}>{c.ok ? "✓" : "✗"} {c.text}</span>;
}

function TryCard({ t, s, chosen }: { t: AgentState["tries"][number]; s: AgentState; chosen: boolean }) {
  const done = !!t.check;
  return (
    <li className={`card sam-try${chosen ? " chosen" : ""}`}>
      <div className="sam-try-num" aria-hidden>{t.id}</div>
      <div className="sam-try-body">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3>Try {t.id}{chosen ? " · the plan" : ""}</h3>
          {done ? <span className={`chip ${t.check!.met ? "good" : "warn"}`}>{t.check!.met ? "Meets the goal" : "Misses the goal"}</span>
            : <span className="chip info">simulating {s.nNights ?? 30} nights…</span>}
        </div>
        {t.note && <blockquote className="sam-says"><SamAvatar size={20} /><span>{t.note}</span></blockquote>}
        <ul className="sam-moves">{t.moves.map((m) => <li key={m}>{m}</li>)}</ul>
        {done && (
          <>
            <div className="sam-metrics">
              <Metric label="Waste" h={t.waste!} unit="portions" pct={t.check!.waste_pct} />
              <Metric label="Missed meals" h={t.missed_meal!} unit="students" />
              <Metric label="90% wait" h={t.wait_p90!} unit="min" dec />
            </div>
            <div className="row" style={{ marginTop: 8 }}>{t.check!.checks.map((c) => <CheckChip key={c.name} c={c} />)}</div>
            {t.check!.protected_worse.length > 0 && (
              <div className="hint" style={{ marginTop: 6 }}>
                Runs out more: {t.check!.protected_worse.slice(0, 6).map((w) => `${w.name} (${stationName(w.station)}, ${Math.round(w.runout_share[0] * 100)}% → ${Math.round(w.runout_share[1] * 100)}% of nights)`).join(" · ")}
                {t.check!.protected_worse.length > 6 ? ` · +${t.check!.protected_worse.length - 6} more` : ""}
              </div>
            )}
            {t.settings && t.settings.length > 0 && (
              <details className="sam-settings"><summary>{t.settings.length} settings changed</summary>
                <ul>{t.settings.map((x) => <li key={x}>{x}</li>)}</ul>
              </details>
            )}
          </>
        )}
      </div>
    </li>
  );
}

function Metric({ label, h, unit, pct: p, dec }: { label: string; h: AgentTry["waste"]; unit: string; pct?: number; dec?: boolean }) {
  const f = dec ? fmt1 : fmtInt;
  const bad = h.verdict === "worse", good = h.verdict === "better";
  return (
    <div className="sam-metric">
      <div className="overline">{label}</div>
      <div className="sam-metric-v">{f(h.now)} <span className="faint">{unit}</span></div>
      <div className={good ? "good-t" : bad ? "bad-t" : "faint"}>
        {p !== undefined ? pct(p) : h.median_change !== undefined ? `${h.median_change > 0 ? "+" : ""}${dec ? fmt1(h.median_change) : fmtInt(h.median_change)}` : ""} vs {f(h.baseline)}{h.verdict ? ` · ${h.verdict}` : ""}
      </div>
    </div>
  );
}

function PlanCard({ f }: { f: AgentFinal }) {
  const { adopt, adopted } = useApp();
  const nav = useNavigate();
  const [note, setNote] = useState<string | null>(null);
  const hold = f.holdout;
  const isAdopted = adopted[f.date]?.hash === f.config_hash;
  const scenario = { preset: null, lever: "api", hash: f.config_hash, label: f.label };
  const onAdopt = async () => {
    adopt(f.date, scenario);
    loadApiResult(f.config_hash).catch(() => undefined); // keeps a copy, so the waste outlook shows this plan offline too
    try {
      const sv = await saveScenario(f.label, f.config_hash, "adopted");
      setNote(`Adopted for ${dateLabel(f.date)} and saved on the API${sv.forecast ? ` (forecast ${sv.forecast})` : ""}. The Kitchen plan page now shows it.`);
    } catch (e) {
      setNote(`Adopted in this browser. The API didn't record it: ${(e as Error).message}.`);
    }
  };
  const by = f.source === "mock" ? "rule-based search (no AI key)" : f.source === "cache" ? `${f.model}, replayed from cache` : f.source === "mixed" ? `${f.model}, finished by the rule-based search` : f.model;
  return (
    <section className="card sam-plan" aria-label="Sam's plan">
      <div className="card-head">
        <div className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
          <SamAvatar size={44} />
          <div>
            <div className="overline">Sam's plan · try {f.chosen}{f.chosen_by === "goal check" ? " (picked by the goal check)" : ""}</div>
            <h2>{f.check.met ? "This plan meets your goal" : "Closest plan: the goal isn't fully met"}</h2>
          </div>
        </div>
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <span className={`chip ${f.check.met ? "good" : "warn"}`}>Waste {pct(f.check.waste_pct)} on Sam's nights</span>
          <span className={`chip ${hold.met ? "good" : "warn"}`} title={hold.checks.filter((c) => !c.ok).map((c) => c.text).join("; ")}>
            {hold.met ? "Holds" : "Doesn't fully hold"} on {hold.seeds[1] - hold.seeds[0] + 1} unseen nights · waste {pct(hold.waste_pct)}
          </span>
        </div>
      </div>
      <blockquote className="sam-says big"><span>{f.reasoning}</span></blockquote>
      {f.caveats && <div className="hint"><b>Watch tonight:</b> {f.caveats}</div>}
      {!hold.met && (
        <div className="alert warn" style={{ marginTop: 10 }}>
          <b>On nights Sam never saw ({hold.seeds[0]}–{hold.seeds[1]})</b>
          {hold.checks.filter((c) => !c.ok).map((c) => c.text).join(" · ")}
        </div>
      )}
      <div className="row" style={{ margin: "12px 0" }}>
        <button className="btn primary" onClick={onAdopt} disabled={isAdopted}>{isAdopted ? "Adopted for tonight" : "Adopt this plan"}</button>
        <button className="btn" onClick={() => nav("/lab", { state: { agentRequest: f.request } })}>Open in the Lab</button>
        <button className="btn ghost" onClick={() => { useApp.getState().setScenario(scenario); nav("/hall"); }}>Watch a night</button>
      </div>
      {note && <div className="alert info" role="status">{note}</div>}
      <h3 style={{ marginTop: 8 }}>Cook sheet changes ({f.changed_dishes.length} dishes)</h3>
      <div className="sam-table">
        <table className="t">
          <thead><tr><th>Dish</th><th>Station</th><th className="n">Batch</th><th className="n">Opening pans</th><th className="n">Reorder at</th><th className="n">Max extra</th><th className="n">Waste</th><th className="n">Runs out</th></tr></thead>
          <tbody>
            {f.changed_dishes.map((r) => (
              <tr key={r.dish_id}>
                <td>{r.name}{r.period === "late_night" ? <span className="faint"> · late</span> : null}{r.protected ? <span className="chip good" style={{ marginLeft: 6 }}>protected</span> : null}</td>
                <td>{stationName(r.station)}</td>
                {(["batch_size", "starting_batches", "reorder_point", "max_batches"] as const).map((k) => (
                  <td key={k} className="n">{r.before[k] === r.after[k] ? r.after[k] : <><span className="faint">{r.before[k]} →</span> <b>{r.after[k]}</b></>}</td>
                ))}
                <td className="n">{fmtInt(r.waste[0])} → {fmtInt(r.waste[1])}</td>
                <td className="n">{Math.round(r.runout_share[0] * 100)}% → {Math.round(r.runout_share[1] * 100)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="faint" style={{ marginTop: 8, fontSize: 11 }}>
        Planned by {by} · {f.tries_run ?? f.tries?.length ?? 0} tries · {f.tiger_lookups?.length ?? 0} Tiger lookups · {fmtInt(f.nights_simulated)} simulated nights · "Runs out" is the share of nights a dish ran out at least once.
      </div>
    </section>
  );
}

function askedLabel(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** What Sam looked up in Tiger Data (through the Tiger MCP server), with the SQL and the first rows. */
function TigerCard({ lookups, live = false }: { lookups: TigerLookup[]; live?: boolean }) {
  return (
    <section className="card" aria-label="Tiger Data lookups">
      <div className="card-head">
        <div>
          <div className="overline">Tiger Data · via the Tiger MCP server</div>
          <h3>Sam checked the recorded history ({lookups.length} {lookups.length === 1 ? "lookup" : "lookups"})</h3>
        </div>
        <span className="chip">read-only · SYNTHETIC history</span>
      </div>
      <ol className="sam-tiger">
        {lookups.map((x, i) => (
          <li key={i}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span>{x.why || "Query"}</span>
              {x.error ? <span className="chip bad">blocked / failed</span>
                : x.sql ? <span className="chip info">{x.row_count ?? 0} rows</span> : <span className="chip">schema</span>}
            </div>
            {x.error && <div className="hint">{x.error}</div>}
            {x.sql && (
              <details>
                <summary className="hint">SQL{x.rows?.length ? " and first rows" : ""}</summary>
                <pre className="sam-sql">{x.sql}</pre>
                {x.columns && x.rows && x.rows.length > 0 && (
                  <div className="sam-table">
                    <table className="t">
                      <thead><tr>{x.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                      <tbody>{x.rows.map((r, j) => <tr key={j}>{r.map((v, k) => <td key={k}>{v ?? "—"}</td>)}</tr>)}</tbody>
                    </table>
                  </div>
                )}
              </details>
            )}
          </li>
        ))}
      </ol>
      {live && <div className="faint" style={{ fontSize: 11 }}>Lookups don't use simulation tries.</div>}
    </section>
  );
}

function AnswerCard({ f }: { f: AgentFinal }) {
  const by = f.source === "mock" ? "no AI key" : f.source === "cache" ? `${f.model}, replayed from cache` : f.model;
  return (
    <section className="card sam-plan" aria-label="Sam's answer">
      <div className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap", marginBottom: 8 }}>
        <SamAvatar size={44} />
        <div>
          <div className="overline">Sam's answer · from Tiger Data</div>
          <h2>{f.goal_text}</h2>
        </div>
      </div>
      <blockquote className="sam-says big"><span style={{ whiteSpace: "pre-wrap" }}>{(f.answer ?? "").replace(/\*\*/g, "")}</span></blockquote>
      {f.caveats && <div className="hint"><b>Note:</b> {f.caveats}</div>}
      <div className="faint" style={{ marginTop: 8, fontSize: 11 }}>Answered by {by} · {f.tiger_lookups?.length ?? 0} read-only Tiger lookups · no simulations</div>
    </section>
  );
}

function HistoryPanel({ items, loading, activeId, onOpen, onDelete }: {
  items: HistoryItem[]; loading: boolean; activeId?: string; onOpen: (id: string) => void; onDelete: (id: string) => void;
}) {
  return (
    <aside className="card sam-history" aria-label="Sam's history">
      <h3>History</h3>
      <div className="hint" style={{ marginBottom: 8 }}>Sam's final analyses, newest first.</div>
      {loading && items.length === 0 && <div className="hint">Loading…</div>}
      {!loading && items.length === 0 && <div className="hint">Nothing yet. Each finished plan or answer is kept here.</div>}
      <ul>
        {items.map((h) => (
          <li key={h.run_id} className={activeId === h.run_id ? "active" : ""}>
            <button className="sam-history-open" onClick={() => onOpen(h.run_id)}>
              <span className="sam-history-meta">
                {h.kind === "question" ? "Question" : "Plan"} for {dateLabel(h.for_date)} · {askedLabel(h.created_at)}
                {h.local ? " · this browser" : ""}
              </span>
              <b>{h.prompt}</b>
              <span className={h.met === true ? "good-t" : h.met === false ? "warn-t" : "faint"}>{h.headline}</span>
            </button>
            <button className="sam-history-del" aria-label={`Delete "${h.prompt}"`} title="Delete" onClick={() => onDelete(h.run_id)}>×</button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
