import { useEffect, useReducer, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { TopBar } from "../components/Shell";
import { SamAvatar } from "../components/SamAvatar";
import { DetailDialog } from "../components/DetailDialog";
import {
  AGENT_IDLE, agentReducer, deleteHistory, listHistory, openHistory, rememberLocal, runAgent,
  type AgentFinal, type AgentState, type AgentTry, type Check, type HistoryItem, type TigerLookup,
} from "../lib/agent";
import { loadApiResult, saveScenario } from "../lib/data";
import { dateLabel, fmt1, fmtInt, stationName } from "../lib/format";
import { useApiUp, useAsync } from "../lib/hooks";
import { useApp } from "../lib/store";

const EXAMPLES = [
  { title: "Waste a little less", detail: "Keep vegetarian dishes available", icon: "leaf", prompt: "Cut waste 20% without any vegetarian dish running out" },
  { title: "Try a what-if", detail: "See what an extra server changes", icon: "spark", prompt: "What if we add a Grill server and it's 10% busier?" },
  { title: "Learn from past nights", detail: "Find patterns in the dining hall", icon: "history", prompt: "Which dishes ran out on rainy Fridays?" },
] as const;
const MORE_EXAMPLES = ["Cut waste 15% and keep every halal dish stocked", "Cut waste 10% without longer lines or missed meals"];
const pct = (n: number) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${fmt1(Math.abs(n))}%`;
const plain = (text: string) => text.replace(/\b(batch_size|reorder_point|max_batches|starting_batches|wait_p90)\b/g,
  (word) => ({ batch_size: "batch size", reorder_point: "refill threshold", max_batches: "extra batches", starting_batches: "opening pans", wait_p90: "long waits" })[word] ?? word);

function SamIcon({ name }: { name: "leaf" | "spark" | "history" | "arrow" }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === "leaf" ? <><path d="M20 4c-9-2-16 2-14 9s14 7 14-9Z" /><path d="m4 20 10-10" /></>
      : name === "spark" ? <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z" /></>
      : name === "history" ? <><path d="M3 10a9 9 0 1 1 2 8M3 4v6h6" /><path d="M12 7v5l3 2" /></>
      : <path d="M5 12h14m-6-6 6 6-6 6" />}
  </svg>;
}

/** Sam's conversation stays brief; the evidence is available on demand. */
export default function Sam() {
  const { date } = useApp();
  const apiOn = useApiUp();
  const [goal, setGoal] = useState("");
  const [runDate, setRunDate] = useState(date);
  const input = useRef<HTMLTextAreaElement>(null);
  const [s, dispatch] = useReducer(agentReducer, AGENT_IDLE);
  const stop = useRef<(() => void) | null>(null);
  useEffect(() => () => stop.current?.(), []);
  const [viewing, setViewing] = useState<AgentFinal | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const history = useAsync(() => listHistory(apiOn), [apiOn, tick]);
  useEffect(() => {
    if (s.final) { rememberLocal(s.final); setTick((t) => t + 1); }
  }, [s.final]);
  useEffect(() => {
    if (s.status === "error") setGoal((draft) => draft || s.goalText || "");
  }, [s.status, s.goalText]);

  const go = async () => {
    const text = goal.trim();
    if (!apiOn || s.status === "running" || text.length < 5) return;
    stop.current?.();
    setViewing(null);
    setRunDate(date);
    setGoal("");
    dispatch({ event: "start", data: { goal_text: text } });
    dispatch({ event: "progress", data: { what: "starting", done: 0, total: 1 } });
    try {
      stop.current = await runAgent(text, date, dispatch);
    } catch (e) {
      dispatch({ event: "error", data: { message: (e as Error).message } });
    }
  };
  const running = s.status === "running";
  const started = !!viewing || s.status !== "idle";
  const choosePrompt = (text: string) => { setGoal(text); input.current?.focus(); };

  return <>
    <TopBar title="Ask Sam" sub="A little help planning a better night." showBadges={false} />
    <div className="content sam-page">
      <div className="sam-toolbar">
        <span className="sam-presence"><i className={apiOn ? "online" : ""} aria-hidden />{running ? "Sam is working on your request" : apiOn ? "Your kitchen planning partner" : "Saved chats are available offline"}</span>
        <button className="btn sam-history-button" onClick={() => { setHistoryError(null); setHistoryOpen(true); }}><SamIcon name="history" />Recent chats{(history.data?.length ?? 0) > 0 && <span>{history.data!.length}</span>}</button>
      </div>
      <section className={`card sam-hero${started ? " has-conversation" : ""}`} aria-label="Give Sam a goal">
        <div className="sam-welcome">
          <div className="sam-welcome-art"><SamAvatar size={112} busy={running} /><span aria-hidden>Hi!</span></div>
          <div>
            <p className="sam-eyebrow">SAM THE MINUTEMAN</p>
            <h2>{started ? "What else is on your mind?" : <>Let’s plan a better dinner.</>}</h2>
            <p className="sam-intro">Tell me what you’d like to improve. I’ll test a few ideas, then help you choose.</p>
          </div>
        </div>
        <form className="sam-ask" onSubmit={(e) => { e.preventDefault(); void go(); }}>
          <label htmlFor="sam-goal" className="sr-only">Message Sam</label>
          <textarea ref={input} id="sam-goal" value={goal} onChange={(e) => setGoal(e.target.value)} disabled={running} rows={2}
            placeholder="How can we cut waste and keep everyone well fed?" />
          <div className="sam-composer-bottom"><span>{running ? "Your current request is in progress" : "A goal, a what-if, or just a question."}</span>
            <button className="btn primary" type="submit" disabled={!apiOn || running || goal.trim().length < 5}>{running ? "Working…" : "Ask Sam"}<SamIcon name="arrow" /></button>
          </div>
        </form>
        {!started && <div className="sam-starters" aria-label="Ideas to get started">
          <p className="sam-eyebrow">A FEW IDEAS TO GET STARTED</p>
          <div className="sam-starter-grid">{EXAMPLES.map((x) => <button key={x.title} className="sam-starter" onClick={() => choosePrompt(x.prompt)}>
            <span className={`sam-starter-icon ${x.icon}`}><SamIcon name={x.icon} /></span><span><b>{x.title}</b><small>{x.detail}</small></span><span className="sam-starter-arrow" aria-hidden>↗</span>
          </button>)}</div>
          <details className="sam-more-ideas"><summary>More ideas</summary><div>{MORE_EXAMPLES.map((x) => <button key={x} className="link" onClick={() => choosePrompt(x)}>{x}</button>)}</div></details>
        </div>}
        {!apiOn && <div className="sam-offline" role="status"><b>Sam is offline right now.</b> You can still open your recent chats.<details><summary>Connection details</summary>Start the planning service with <code>make server</code>, then refresh this page.</details></div>}
      </section>
      {viewing ? <div className="sam-thread">
        <div className="sam-viewing" role="status"><span>Saved chat · {dateLabel(viewing.date)}{viewing.created_at ? ` · ${askedLabel(viewing.created_at)}` : ""}</span><button className="btn ghost" onClick={() => setViewing(null)}>{s.status === "idle" ? "Close chat" : "Back to the current run"}</button></div>
        <div className="sam-user-message"><span>You asked</span><p>{viewing.goal_text}</p></div>
        <FinalCard key={viewing.history_id ?? viewing.config_hash} f={viewing} />
        {!!viewing.tiger_lookups?.length && <TigerCard lookups={viewing.tiger_lookups} />}
      </div> : s.status !== "idle" ? <Run s={s} date={runDate} /> : <p className="sam-trust-note">You choose what to adopt. Sam’s suggestions are tested in simulation.</p>}
      {historyOpen && <DetailDialog title="Recent chats" context="Ask Sam" onClose={() => setHistoryOpen(false)}>
        {historyError && <div className="alert warn" role="alert">{historyError}</div>}
        <HistoryPanel items={history.data ?? []} loading={history.loading} activeId={viewing?.history_id}
          onOpen={async (id) => {
            try { setViewing({ ...(await openHistory(id, apiOn)), history_id: id }); setHistoryOpen(false); }
            catch (e) { setHistoryError((e as Error).message); }
          }}
          onDelete={async (id) => {
            try {
              const msg = await deleteHistory(id, apiOn);
              if (viewing?.history_id === id) setViewing(null);
              setTick((t) => t + 1);
              setHistoryError(msg);
            } catch (e) { setHistoryError((e as Error).message); }
          }} />
      </DetailDialog>}
    </div>
  </>;
}

function FinalCard({ f }: { f: AgentFinal }) {
  return f.kind === "question" ? <AnswerCard f={f} /> : f.kind === "whatif" ? <WhatIfCard f={f} /> : <PlanCard f={f} />;
}

function Run({ s, date }: { s: AgentState; date: string }) {
  const running = s.status === "running";
  const attempts = <ol className="sam-tries" aria-label="Sam's tries">{s.tries.map((t) => <TryCard key={t.id} t={t} s={s} chosen={s.final?.chosen === t.id} />)}</ol>;
  return <div className="sam-thread">
    <GoalCard s={s} date={date} />
    {running && <Working s={s} />}
    {s.status === "error" && <div className="alert bad" role="alert"><b>Sam couldn’t finish this request</b>{s.error}<p>You can edit your message above and try again.</p></div>}
    {s.final && <FinalCard key={s.final.history_id ?? s.final.config_hash} f={s.final} />}
    {s.goal?.kind !== "whatif" && s.tries.length > 0 && (s.final
      ? <details className="card sam-disclosure"><summary><span>How Sam tested this</span><small>{s.tries.length} attempts</small></summary><div className="sam-disclosure-body">{attempts}</div></details>
      : <section className="sam-attempts" aria-label="Plans being tested"><div className="sam-section-heading"><h3>Finding a plan that fits</h3><span>{s.tries.length} of {s.maxTries ?? 5} attempts</span></div>{attempts}</section>)}
    {s.goal?.kind === "whatif" && s.whatif && !s.final && <WhatIfSettings w={s.whatif} />}
    {s.tiger.length > 0 && <TigerCard lookups={s.tiger} live={running} />}
    {s.notices.length > 0 && <details className="card sam-disclosure"><summary><span>Run notes</span><small>{s.notices.length}</small></summary><div className="sam-disclosure-body">{s.notices.map((n, i) => <p key={`${i}:${n}`}>{n}</p>)}</div></details>}
  </div>;
}

function Working({ s }: { s: AgentState }) {
  const p = s.progress;
  const question = s.goal?.kind === "question";
  const whatif = s.goal?.kind === "whatif";
  const what = question ? "Looking through past nights"
    : whatif ? (s.whatif ? `Testing your what-if on ${s.nNights ?? 30} nights` : "Understanding your what-if")
    : s.validating ? "Checking the plan on a fresh set of nights"
    : !s.goal ? "Understanding your goal and checking the current plan"
    : p?.what.startsWith("try") ? `Testing attempt ${s.tries.at(-1)?.id ?? 1}`
    : s.tries.length ? "Comparing the results and refining the plan"
    : "Looking for a good place to start";
  return <div className="sam-working" role="status" aria-live="polite">
    <SamAvatar size={40} busy /><div><b>{what}…</b><span>{question ? "Checking the recorded history" : "I’ll bring back the results when they’re ready."}</span></div>
    {p && p.total > 1 && <div className="sam-progress"><span>{p.done} / {p.total} nights</span><progress max={p.total} value={p.done} aria-label="Nights simulated" /></div>}
  </div>;
}

function GoalCard({ s, date }: { s: AgentState; date: string }) {
  const g = s.goal;
  return <div className="sam-user-message">
    <span>You asked · {dateLabel(date)}</span><p>{s.goalText}</p>
    {g && g.kind !== "question" && g.kind !== "whatif" && <details className="sam-goal-details"><summary>Your goals & starting point</summary>
      <ul>
        {g.waste_cut_pct > 0 && <li>Reduce waste by {g.waste_cut_pct}%.</li>}
        {(g.protect_diets.length > 0 || g.protect_dishes.length > 0) && <li>Keep {g.protect_diets.join(", ") || "selected"} dishes at least as available as in the current plan ({s.protectedDishes?.length ?? 0} dishes).</li>}
        {g.keep_missed_meals && <li>No increase in missed meals.</li>}{g.keep_wait && <li>No longer waits.</li>}
      </ul>
      {s.baseline && <p>Current plan, median of {s.nNights ?? 30} nights: {fmtInt(s.baseline.waste_total)} portions wasted, {fmtInt(s.baseline.missed_meal)} missed meals, and 90% of waits under {fmt1(s.baseline.wait_p90)} minutes.</p>}
      <p>Sam tests up to {s.maxTries ?? 5} ideas. Protected dishes should run out no more often than in the current plan.</p>
    </details>}
    {!!g?.unsupported.length && <p className="sam-caution">Outside this model: {g.unsupported.join("; ")}</p>}
  </div>;
}

function CheckList({ checks }: { checks: Check[] }) {
  return <ul className="sam-checks">{checks.map((c) => <li key={c.name}><span className={c.ok ? "good-t" : "warn-t"} aria-label={c.ok ? "Passed" : "Needs attention"}>{c.ok ? "✓" : "!"}</span><span>{plain(c.text)}</span></li>)}</ul>;
}

function TryCard({ t, s, chosen }: { t: AgentState["tries"][number]; s: AgentState; chosen: boolean }) {
  const failed = t.check?.checks.filter((c) => !c.ok).length ?? 0;
  const waste = t.check ? `Waste ${t.check.waste_pct < 0 ? "down" : t.check.waste_pct > 0 ? "up" : "unchanged"}${t.check.waste_pct ? ` ${fmt1(Math.abs(t.check.waste_pct))}%` : ""}` : "Testing against the current plan";
  return <li className={`sam-try${chosen ? " chosen" : ""}`}><details>
    <summary className="sam-try-summary"><span className="sam-try-num" aria-hidden>{t.id}</span><span className="sam-try-heading"><b>Attempt {t.id}{chosen ? " · selected plan" : ""}</b><small>{waste}{failed > 0 ? ` · ${failed} ${failed === 1 ? "check needs" : "checks need"} attention` : ""}</small></span>
      <span className={`chip ${t.check ? t.check.met ? "good" : "warn" : "info"}`}>{t.check ? t.check.met ? "Goal met" : "Needs adjusting" : "Testing…"}</span><span className="sam-chevron" aria-hidden>⌄</span>
    </summary>
    <div className="sam-try-body">
      {t.note && <p className="sam-attempt-note">{plain(t.note)}</p>}
      {t.check && <>
        <p className="sam-data-caption">Median results across {s.nNights ?? 30} simulated nights</p>
        <div className="sam-metrics">
          {t.waste && <Metric label="Food waste" h={t.waste} unit="portions" pct={t.check.waste_pct} />}
          {t.missed_meal && <Metric label="Missed meals" h={t.missed_meal} unit="students" />}
          {t.wait_p90 && <Metric label="Long waits (90%)" h={t.wait_p90} unit="min" dec />}
        </div><CheckList checks={t.check.checks} />
        {t.check.protected_worse.length > 0 && <details className="sam-settings"><summary>Dishes that run out more ({t.check.protected_worse.length})</summary><div className="sam-table"><table className="t"><thead><tr><th>Dish</th><th>Station</th><th>Before → after</th></tr></thead><tbody>{t.check.protected_worse.map((w, i) => <tr key={`${i}:${w.dish_id}`}><td>{w.name}</td><td>{stationName(w.station)}</td><td>{Math.round(w.runout_share[0] * 100)}% → {Math.round(w.runout_share[1] * 100)}% of nights</td></tr>)}</tbody></table></div></details>}
      </>}
      {(t.moves.length > 0 || !!t.settings?.length) && <details className="sam-settings"><summary>Changes tested{t.settings?.length ? ` · ${t.settings.length} settings` : ""}</summary><ul className="sam-moves">{t.moves.map((m, i) => <li key={`${i}:${m}`}>{plain(m)}</li>)}</ul>{!!t.settings?.length && <ul className="sam-setting-values">{t.settings.map((x, i) => <li key={`${i}:${x}`}>{plain(x)}</li>)}</ul>}</details>}
    </div>
  </details></li>;
}

function Metric({ label, h, unit, pct: p, dec }: { label: string; h: AgentTry["waste"]; unit: string; pct?: number; dec?: boolean }) {
  const f = dec ? fmt1 : fmtInt;
  return <div className="sam-metric"><span className="sam-data-caption">{label}</span><div className="sam-metric-v">{f(h.now)} <small>{unit}</small></div>
    <div className={`sam-metric-change ${h.verdict === "better" ? "good-t" : h.verdict === "worse" ? "bad-t" : "muted"}`}>{p !== undefined ? pct(p) : h.median_change !== undefined ? `${h.median_change > 0 ? "+" : ""}${f(h.median_change)}` : ""}{h.verdict ? ` · ${h.verdict === "same" ? "similar" : h.verdict}` : ""}</div>
    <small className="sam-baseline">Current plan: {f(h.baseline)} {unit}</small>
  </div>;
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
  const selected = f.tries?.find((t) => t.id === f.chosen);
  const failures = f.check.checks.filter((c) => !c.ok);
  const extraNights = hold.seeds[1] - hold.seeds[0] + 1;
  return <section className="card sam-plan" aria-label="Sam's plan">
    <div className="sam-response-heading"><SamAvatar size={48} /><div><p className="sam-eyebrow">SAM’S RECOMMENDATION</p><h2>{f.check.met ? "A plan that meets your goal" : "Here’s the closest plan"}</h2></div><span className={`chip ${f.check.met ? "good" : "warn"}`}>{f.check.met ? "Goal met" : "Needs review"}</span></div>
    <Explanation text={f.reasoning} />
    {selected ? <><p className="sam-data-caption">Compared with the current plan · simulated results</p><div className="sam-metrics">
      <Metric label="Food waste" h={selected.waste} unit="portions" pct={f.check.waste_pct} />
      <Metric label="Missed meals" h={selected.missed_meal} unit="students" />
      <Metric label="Long waits (90%)" h={selected.wait_p90} unit="min" dec />
    </div></> : <div className="sam-saved-metric"><strong>{pct(f.check.waste_pct)}</strong><span>change in food waste<br /><small>compared with the current plan</small></span></div>}
    {failures.length > 0 && <div className="sam-caution"><b>Still needs attention</b><CheckList checks={failures} /></div>}
    {f.caveats && <p className="sam-caveat"><b>Keep in mind:</b> {plain(f.caveats)}</p>}
    <div className={`sam-validation ${hold.met ? "passed" : "review"}`}>
      <span className="sam-validation-icon" aria-hidden>{hold.met ? "✓" : "!"}</span><div><b>{hold.met ? `Also passed on ${extraNights} extra nights` : `Some goals didn’t hold on ${extraNights} extra nights`}</b><p>Waste {pct(hold.waste_pct)} on nights Sam hadn’t used to choose the plan.</p>
        <details><summary>View validation details</summary><CheckList checks={hold.checks} /></details>
      </div>
    </div>
    <div className="sam-plan-actions">
      <button className="btn primary" onClick={onAdopt} disabled={isAdopted}>{isAdopted ? "Plan adopted" : "Adopt this plan"}</button>
      <button className="btn" onClick={() => { useApp.getState().setDate(f.date); useApp.getState().setScenario(scenario); nav("/hall"); }}>Watch a night <SamIcon name="arrow" /></button>
      <button className="btn ghost" onClick={() => nav("/lab", { state: { agentRequest: f.request } })}>Open in the Lab</button>
    </div>
    {note && <div className="alert info" role="status">{note}</div>}
    <details className="sam-disclosure sam-kitchen-changes"><summary><span>Kitchen changes</span><small>{f.changed_dishes.length} dishes</small></summary><div className="sam-disclosure-body">
      <p className="sam-data-caption">Current plan → Sam’s plan. “Runs out” is the share of simulated nights with a stockout.</p>
      <div className="sam-table" role="region" aria-label="Cook sheet changes" tabIndex={0}><table className="t">
        <thead><tr><th>Dish</th><th>Station</th><th className="n">Batch</th><th className="n">Opening pans</th><th className="n">Refill at</th><th className="n">Max extra</th><th className="n">Waste</th><th className="n">Runs out</th></tr></thead>
        <tbody>{f.changed_dishes.map((r) => <tr key={r.dish_id}>
          <td>{r.name}{r.period === "late_night" ? <small className="muted"> · late night</small> : null}{r.protected && <small className="sam-protected">Protected</small>}</td><td>{stationName(r.station)}</td>
          {(["batch_size", "starting_batches", "reorder_point", "max_batches"] as const).map((k) => <td key={k} className="n">{r.before[k] === r.after[k] ? r.after[k] : <><span className="muted">{r.before[k]} →</span> <b>{r.after[k]}</b></>}</td>)}
          <td className="n">{fmtInt(r.waste[0])} → {fmtInt(r.waste[1])}</td><td className="n">{Math.round(r.runout_share[0] * 100)}% → {Math.round(r.runout_share[1] * 100)}%</td>
        </tr>)}</tbody>
      </table></div>
    </div></details>
    <details className="sam-method"><summary>About this analysis</summary><p>Planned by {by} · {f.tries_run ?? f.tries?.length ?? 0} attempts · {f.tiger_lookups?.length ?? 0} history lookups · {fmtInt(f.nights_simulated)} simulated nights. Selected attempt {f.chosen}{f.chosen_by === "goal check" ? " by the goal check" : ""}.</p><CheckList checks={f.check.checks} /></details>
  </section>;
}

function Explanation({ text }: { text: string }) {
  const readable = plain(text).replace(/\*\*/g, "");
  if (readable.length < 360) return <p className="sam-explanation">{readable}</p>;
  const first = readable.split(/(?<=[.!?])\s/)[0];
  return <div className="sam-explanation"><p>{first.length < 320 ? first : "Here’s how this compares with the current plan."}</p><details><summary>Read Sam’s explanation</summary><p>{readable}</p></details></div>;
}

function askedLabel(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** What Sam looked up in Tiger Data (through the Tiger MCP server), with the SQL and the first rows. */
function TigerCard({ lookups, live = false }: { lookups: TigerLookup[]; live?: boolean }) {
  return (
    <details className="card sam-disclosure" aria-label="Tiger Data lookups">
      <summary><span>History Sam checked</span><small>{lookups.length} {lookups.length === 1 ? "lookup" : "lookups"} · synthetic records</small></summary>
      <div className="sam-disclosure-body">
      <p className="sam-data-caption">Read-only queries of the synthetic dining history in Tiger Data.</p>
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
      {live && <p className="sam-data-caption">Sam may check more records as the plan develops.</p>}
      </div>
    </details>
  );
}

function AnswerCard({ f }: { f: AgentFinal }) {
  const by = f.source === "mock" ? "no AI key" : f.source === "cache" ? `${f.model}, replayed from cache` : f.model;
  return (
    <section className="card sam-plan" aria-label="Sam's answer">
      <div className="sam-response-heading">
        <SamAvatar size={44} />
        <div>
          <p className="sam-eyebrow">SAM’S ANSWER</p>
          <h2>Here’s what I found</h2>
        </div>
      </div>
      <div className="sam-answer-copy">{(f.answer ?? "").replace(/\*\*/g, "").split(/\n\s*\n/).filter(Boolean).map((paragraph, i) => <p key={i}>{paragraph}</p>)}</div>
      {f.caveats && <div className="hint"><b>Note:</b> {f.caveats}</div>}
      <details className="sam-method"><summary>About this answer</summary><p>Answered by {by} · {f.tiger_lookups?.length ?? 0} read-only Tiger lookups · no simulations.</p></details>
    </section>
  );
}

function HistoryPanel({ items, loading, activeId, onOpen, onDelete }: {
  items: HistoryItem[]; loading: boolean; activeId?: string; onOpen: (id: string) => void; onDelete: (id: string) => void;
}) {
  return (
    <section className="sam-history" aria-label="Sam's history">
      <h2>Pick up where you left off</h2>
      <p className="muted">Your finished plans and answers, newest first.</p>
      {loading && items.length === 0 && <div className="hint">Loading…</div>}
      {!loading && items.length === 0 && <div className="sam-history-empty"><SamAvatar size={72} /><h3>Your first chat starts here</h3><p>Finished plans and answers will be saved here for next time.</p></div>}
      <ul>
        {items.map((h) => (
          <li key={h.run_id} className={activeId === h.run_id ? "active" : ""}>
            <button className="sam-history-open" onClick={() => onOpen(h.run_id)}>
              <span className="sam-history-meta">
                {h.kind === "question" ? "Question" : h.kind === "whatif" ? "What-if" : "Plan"} for {dateLabel(h.for_date)} · {askedLabel(h.created_at)}
                {h.local ? " · this browser" : ""}
              </span>
              <b>{h.prompt}</b>
              <span className={h.met === true ? "good-t" : h.met === false ? "warn-t" : "faint"}>{h.headline}</span>
            </button>
            <button className="sam-history-del" aria-label={`Delete "${h.prompt}"`} title="Delete" onClick={() => onDelete(h.run_id)}>×</button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function methodText(m?: string) {
  return m === "function_calling" ? "Read by Gemini function calling" : m === "json_schema" ? "Read by AI (JSON settings)" : "Keyword matching (no AI key set)";
}

function WhatIfSettings({ w }: { w: NonNullable<AgentState["whatif"]> }) {
  return (
    <details className="card sam-disclosure" aria-label="What-if settings">
      <summary><span>What Sam is testing</span><small>{w.settings.length} changes</small></summary>
      <div className="sam-disclosure-body">
      {w.settings.length ? <ul className="sam-moves">{w.settings.map((x, i) => <li key={`${i}:${x}`}>{x}</li>)}</ul>
        : <div className="hint">{w.error ?? "Nothing in that maps to a setting."}</div>}
      {w.unsupported.length > 0 && <div className="hint">Not supported: {w.unsupported.join("; ")}</div>}
      <p className="sam-data-caption">{methodText(w.method)}</p>
      </div>
    </details>
  );
}

/** A what-if from the Ask Sam box: the settings Sam read, simulated as asked, against tonight's plan. */
function WhatIfCard({ f }: { f: AgentFinal }) {
  const { adopt, adopted } = useApp();
  const nav = useNavigate();
  const [note, setNote] = useState<string | null>(null);
  const ran = !!f.metrics && !!f.config_hash;
  const isAdopted = ran && adopted[f.date]?.hash === f.config_hash;
  const scenario = { preset: null, lever: "api", hash: f.config_hash, label: f.label };
  const onAdopt = async () => {
    adopt(f.date, scenario);
    loadApiResult(f.config_hash).catch(() => undefined);
    try {
      const sv = await saveScenario(f.label, f.config_hash, "adopted");
      setNote(`Adopted for ${dateLabel(f.date)} and saved on the API${sv.forecast ? ` (forecast ${sv.forecast})` : ""}.`);
    } catch (e) {
      setNote(`Adopted in this browser. The API didn't record it: ${(e as Error).message}.`);
    }
  };
  const m = f.metrics;
  return (
    <section className="card sam-plan" aria-label="Sam's what-if">
      <div className="sam-response-heading">
        <SamAvatar size={44} />
        <div>
          <p className="sam-eyebrow">YOUR WHAT-IF · {dateLabel(f.date)}</p>
          <h2>Here’s what changes</h2>
        </div>
      </div>
      {f.explanation && <Explanation text={f.explanation} />}
      {(f.unsupported?.length ?? 0) > 0 && <p className="sam-caution">Outside this model: {f.unsupported!.join("; ")}</p>}
      {m ? (
        <div className="sam-metrics">
          <Metric label="Food waste" h={m.waste_total} unit="portions" pct={m.waste_total.baseline ? (100 * (m.waste_total.median_change ?? 0)) / m.waste_total.baseline : 0} />
          <Metric label="Missed meals" h={m.missed_meal} unit="students" />
          <Metric label="Long waits (90%)" h={m.wait_p90} unit="min" dec />
        </div>
      ) : <div className="alert warn" style={{ marginTop: 8 }}><b>Nothing simulated</b>{f.summary}</div>}
      {ran && (
        <div className="sam-plan-actions">
          <button className="btn primary" onClick={onAdopt} disabled={isAdopted}>{isAdopted ? "Plan adopted" : "Adopt this what-if"}</button>
          <button className="btn" onClick={() => { useApp.getState().setDate(f.date); useApp.getState().setScenario(scenario); nav("/hall"); }}>Watch a night <SamIcon name="arrow" /></button>
          <button className="btn ghost" onClick={() => nav("/lab", { state: { agentRequest: f.request } })}>Open in the Lab</button>
        </div>
      )}
      {note && <div className="alert info" role="status">{note}</div>}
      <details className="sam-disclosure sam-kitchen-changes"><summary><span>Changes & simulation details</span><small>{f.settings?.length ?? 0} settings</small></summary><div className="sam-disclosure-body">
        {!!f.settings?.length && <ul className="sam-moves">{f.settings.map((x, i) => <li key={`${i}:${x}`}>{plain(x)}</li>)}</ul>}
        {m && <Metric label="Empty-pan time across dishes" h={m.stockout_min_total} unit="dish-min" dec />}
        <p className="sam-data-caption">{methodText(f.method)}{ran ? ` · ${f.nights_simulated ?? 60} simulated nights, including the current plan.` : ""}</p>
      </div></details>
    </section>
  );
}
