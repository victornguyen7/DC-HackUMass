import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ErrorBox, Loading, TopBar } from "../components/Shell";
import { Meter, Spark } from "../components/Charts";
import { HallScene } from "../components/HallScene";
import { ReplayAlert, type LiveAlert } from "../components/ReplayAlert";
import { useReplayClock } from "../lib/useReplayClock";
import { TABLE_SOLIDS } from "../lib/hallGeometry";
import { apiUp, loadApiHighlights, loadApiTrace, loadNarration, loadTrace, narrateStudent } from "../lib/data";
import { clock, dateLabel, dishWindow, fmt1, groupFlags, PRESET_LABELS, signed, stationName } from "../lib/format";
import { useApiUp, useAsync, useManifest, useScenarioResult } from "../lib/hooks";
import {
  counters, levelAt, lineSeries, panTimelines, peakLevel, PLAN_H, PLAN_W, poseAt, toPx, type Pose,
} from "../lib/playback";
import { useApp } from "../lib/store";
import type { ManifestEntry, Narration, Result, Trace, TraceStudent } from "../lib/types";

// Label anchors on the top-down plan (px), from the measured plan model (render_meta.json).
const ANCHORS: Record<string, [number, number]> = {
  desserts: [238.0, 133.7], mediterranean: [361.0, 120.3], seasons: [484.0, 120.3], street_food: [647.1, 123.0],
  latino: [147.1, 228.6], grill: [350.3, 176.5], tandoor: [451.9, 219.3], soups: [382.4, 274.1],
  sushi: [645.8, 217.9], noodle_bowl: [701.9, 176.5], display: [740.7, 260.7],
  carving: [712.0, 244.0],
};
const MARKERS: { xy: [number, number]; text: string; dark?: boolean }[] = [
  { xy: [150, 96], text: "Dish return (assumed)", dark: true },
  { xy: [501.4, 342], text: "↑ Swipe in · entry (assumed)", dark: true },
  { xy: [628.4, 427.8], text: "Back seating · indoor" },
  { xy: [572, 142], text: "Wok · closed at dinner" },
];

const DIET_COLOR: Record<string, string> = {
  none: "var(--diet-none)", vegetarian: "var(--diet-veg)", vegan: "var(--diet-vegan)", halal: "var(--diet-halal)",
};
const dietLabel = (d: string) => ({ none: "No restriction", vegetarian: "Vegetarian", vegan: "Vegan", halal: "Halal" } as Record<string, string>)[d] ?? d;
type ColorBy = "diet" | "wait" | "missed";
type View = "isometric" | "top";
type Step = "watch" | "try" | "compare";
const SPEEDS = [1, 10, 30, 60];

type Replay = { trace: Trace | null; configHash: string | null; source: "bundle" | "api" | null; note?: string };

export default function Hall() {
  const { date, scenario, hallTime, setHallTime, setScenario } = useApp();
  const [step, setStep] = useState<Step>("watch");
  const [draftPreset, setDraftPreset] = useState(scenario.preset ?? "extra_server");
  const [replayAttempt, setReplayAttempt] = useState(0);
  const manifest = useManifest();
  const res = useScenarioResult(date, scenario);
  const entry = res.data?.entry;
  const seed = res.data?.result.median_seed;
  // Bundle trace first; otherwise ask the API to replay the median night (UI_ISSUES U3).
  const replay = useAsync<Replay>(async () => {
    if (!entry) return { trace: null, configHash: null, source: null };
    if (entry.trace) return { trace: await loadTrace(entry), configHash: entry.config_hash, source: "bundle" };
    if (replayAttempt === 0 && !(await apiUp())) {
      return { trace: null, configHash: entry.config_hash, source: null, note: "No animation was saved for this scenario on this date. Its saved results are still available." };
    }
    try {
      return { trace: await loadApiTrace(entry.config_hash, seed), configHash: entry.config_hash, source: "api" };
    } catch {
      return { trace: null, configHash: entry.config_hash, source: null, note: "The animation could not be loaded. Its saved results are still available." };
    }
  }, [entry?.key, replayAttempt]);
  const currentReplay = replay.data?.configHash === entry?.config_hash ? replay.data : undefined;
  const trace = currentReplay?.trace ?? null;
  const replayLoading = replay.loading || (!currentReplay && !replay.error);
  const narr = useAsync<Narration | null>(async () => {
    if (!entry || !trace) return null;
    if (entry.narration) return loadNarration(entry);
    if (!(await apiUp())) return null;
    try {
      const highlights = await loadApiHighlights(entry.config_hash, trace.night_seed);
      return { config_hash: entry.config_hash, seed: trace.night_seed, highlights, students: {} };
    } catch {
      return null;
    }
  }, [entry?.key, trace]);

  const sub = res.data
    ? `Worcester, second floor · one representative night from ${res.data.result.n_nights} simulations`
    : "Worcester 2nd floor";
  return (
    <>
      <TopBar title="Simulation" sub={sub} scenario={res.data?.fallback ? { preset: null, lever: "" } : scenario} />
      <div className="content simulation-content">
        {res.error && <ErrorBox error={res.error} />}
        {res.data?.fallback && <div className="banner">{res.data.reason ?? `This scenario isn't available for ${dateLabel(date)}; showing the baseline.`}</div>}
        {res.loading && <Loading what="simulation results" />}
        {res.data && !res.loading && (
          <HallView key={entry?.key} result={res.data.result} trace={trace} narration={narr.data && narr.data.config_hash === entry?.config_hash ? narr.data : null}
            configHash={entry!.config_hash} initialT={hallTime} onTime={setHallTime}
            step={step} setStep={setStep} draftPreset={draftPreset} setDraftPreset={setDraftPreset}
            scenarios={(manifest.data?.entries ?? []).filter((e) => e.date === date)} replayKey={entry!.key}
            replayLoading={replayLoading} replayNote={currentReplay?.note ?? (replay.error ? "The animation could not be loaded. Its saved results are still available." : undefined)}
            onRetryReplay={() => setReplayAttempt((attempt) => attempt + 1)}
            onReplay={(e) => setScenario({ preset: e.preset, lever: e.lever })} />
        )}
      </div>
    </>
  );
}

function HallView({ result, trace, narration, configHash, initialT, onTime, step, setStep,
  draftPreset, setDraftPreset, scenarios, replayKey, onReplay, replayLoading, replayNote, onRetryReplay }: {
  result: Result; trace: Trace | null; narration: Narration | null; configHash: string;
  initialT: number | null; onTime: (t: number) => void;
  step: Step; setStep: (step: Step) => void; draftPreset: string; setDraftPreset: (preset: string) => void;
  scenarios: ManifestEntry[]; replayKey: string; onReplay: (entry: ManifestEntry) => void;
  replayLoading: boolean; replayNote?: string; onRetryReplay: () => void;
}) {
  const nav = useNavigate();
  const replayEnd = result.close + 30;
  const { time: t, timeRef, playing, setPlaying, speed, setSpeed, seek, toggle } = useReplayClock(
    result.open, replayEnd, initialT ?? Math.min(result.close, 18 * 60 + 12));
  const [colorBy, setColorBy] = useState<ColorBy>("wait");
  const [view, setView] = useState<View>("isometric");
  const [replayOptionsOpen, setReplayOptionsOpen] = useState(false);
  const [station, setStation] = useState<string | null>(null);
  const [studentId, setStudentId] = useState<number | null>(null);
  const names = useMemo(() => Object.fromEntries(result.cook_sheet.map((d) => [d.dish_id, d.name])), [result]);
  const stationPos = useMemo(() => Object.fromEntries(result.stations.map((s) => [s.id, s.pos as [number, number]])), [result]);
  const pans = useMemo<ReturnType<typeof panTimelines>>(() => trace ? panTimelines(trace) : new Map(), [trace]);
  const missed = useMemo(() => missedFirst(trace?.students ?? []), [trace]);
  const byId = useMemo(() => new Map(trace?.students.map((s) => [s.id, s]) ?? []), [trace]);
  const rowOf = useMemo(() => Object.fromEntries(result.cook_sheet.map((d) => [d.dish_id, d])), [result]);
  const poses = useMemo(() => (trace?.students ?? []).map((s) => poseAt(s, t, stationPos)).filter((p): p is Pose => p !== null), [trace, t, stationPos]);
  const c = counters(poses);
  // Keep navigation context without updating the entire app store on every animation frame.
  useEffect(() => { if (!playing) onTime(t); }, [t, playing, onTime]);
  useEffect(() => () => onTime(timeRef.current), [onTime, timeRef]);
  const emptyNow = [...pans.values()].filter((tl) => {
    const d = rowOf[tl.dish];
    if (!d) return false;
    const [a, b] = dishWindow(result, d.period);
    return t >= a && t < b && levelAt(tl, t) <= 0;
  });
  const isEmpty = (sid: string) => emptyNow.filter((tl) => rowOf[tl.dish]?.station === sid || (sid === "desserts" && rowOf[tl.dish]?.station === "breads")).map((tl) => names[tl.dish] ?? tl.dish);
  const queueCount = (sid: string) => (c.lines[sid] ?? 0) + (sid === "desserts" ? c.lines.breads ?? 0 : 0);
  const visibleStations = result.stations.filter((s) => s.id !== "breads");
  const pickStation = (sid: string) => { setStation(sid); setStudentId(null); setStep("watch"); };
  const pickStudent = (id: number) => { setStudentId(id); setStation(null); setStep("watch"); setPlaying(false); };
  const goStep = (next: Step) => { setStation(null); setStudentId(null); setStep(next); };
  const color = (p: Pose) => {
    if (colorBy === "diet") return DIET_COLOR[p.diet] ?? DIET_COLOR.none;
    if (colorBy === "missed") return missed.has(p.id) ? "var(--bad)" : "var(--border-strong)";
    if (p.phase !== "line") return "var(--border-strong)";
    return (p.waitedMin ?? 0) > 10 ? "var(--bad)" : (p.waitedMin ?? 0) > 5 ? "var(--warn)" : "var(--good)";
  };
  // A comparison needs saved results, not an animation. Most dates only bundle
  // a baseline trace, even though all preset comparisons are available offline.
  const draftEntry = scenarios.find((e) => e.preset === draftPreset && !e.lever);
  const replays = scenarios.filter((e) => !!e.trace);
  const baseline = scenarios.find((e) => !e.preset && !e.lever);
  const presets = [
    ["smaller_batches", "Cook smaller batches", "Reduce the batch size of favorites"],
    ["extra_server", "Add a Grill server", "Test one extra server at the busiest station"],
    ["theme_night", "Test a busier night", "Theme-night preset: 25% more crowd"],
  ];
  const longest = [...visibleStations].sort((a, b) => queueCount(b.id) - queueCount(a.id))[0];
  const liveAlerts: LiveAlert[] = visibleStations.flatMap<LiveAlert>((s) => {
    const empty = isEmpty(s.id), line = queueCount(s.id), label = stationName(s.id);
    if (empty.length) return [{ id: `empty:${s.id}`, station: s.id, kind: "empty", title: `${label} has ${empty.length === 1 ? "an empty pan" : "empty pans"}`, message: `${empty[0]}${empty.length > 1 ? ` and ${empty.length - 1} more ${empty.length === 2 ? "dish" : "dishes"}` : ""} ${empty.length === 1 ? "is" : "are"} empty.` }];
    if (line > 15) return [{ id: `queue:${s.id}`, station: s.id, kind: "queue", title: `${label} has a long line`, message: `${line} students are waiting. Open the station to check its queue and menu.` }];
    return [];
  }).sort((a, b) => Number(a.kind === "queue") - Number(b.kind === "queue"));

  return (
    <div className="guided-hall">
      {trace && <ReplayAlert alerts={liveAlerts} playing={playing} time={t} onInspect={(sid) => { pickStation(sid); setPlaying(false); }} />}
      <aside className="guided-sidebar" aria-label="Simulation guide">
        <nav className="guided-steps" aria-label="Simulation steps">
          {([["watch", "Watch the hall"], ["try", "Try a change"], ["compare", "Compare"]] as const).map(([id, label], i) => (
            <button key={id} aria-current={step === id ? "step" : undefined} onClick={() => goStep(id)}><span>{i + 1}</span>{label}</button>
          ))}
        </nav>
        <section className="card guided-inspector" aria-label="Simulation details" tabIndex={0}>
          {station || studentId !== null ? <button className="back-to-stations" onClick={() => { setStation(null); setStudentId(null); }}>← All stations</button> : null}
          {station && trace ? <StationCard result={result} trace={trace} sid={station} t={t} pans={pans} inLine={queueCount(station)} />
            : studentId !== null && byId.get(studentId) && trace ? <StudentCard key={studentId} s={byId.get(studentId)!} narration={narration} names={names} t={t}
                missed={missed.has(studentId)} configHash={configHash} seed={trace.night_seed} onPick={pickStudent} />
            : step === "watch" && !trace ? <>
              <p className="guide-eyebrow">{result.n_nights} simulated nights</p>
              <h2>{replayLoading ? "Preparing the hall" : "Explore this plan"}</h2>
              <p className="guide-copy">{replayLoading ? "The animation is loading. You can explore scenarios while it gets ready." : "Saved results are available even when a plan has no animation. Compare its effects or choose another scenario."}</p>
              <button className="btn primary guide-action" onClick={() => goStep(result.vs_baseline ? "compare" : "try")}>{result.vs_baseline ? "View comparison" : "Try a change"}</button>
            </> : step === "watch" ? <>
              <p className="guide-eyebrow">Explore the current replay</p>
              <h2>Every station.<br />The whole night.</h2>
              <p className="guide-copy">Press Play to follow the students. Select a station for its dishes, pan levels, and queue history.</p>
              <button className="btn primary guide-action" onClick={() => goStep("try")}>Try a change <span aria-hidden>→</span></button>
              <div className="station-directory-head"><h3>Stations</h3><span>In line now</span></div>
              <div className="station-directory">
                {visibleStations.map((s) => <button key={s.id} onClick={() => pickStation(s.id)} aria-label={`Inspect ${s.id === "desserts" ? "Desserts and Breads" : stationName(s.id)}`}>
                  <span>{s.id === "desserts" ? "Desserts · Breads" : stationName(s.id)}</span>
                  <span>{isEmpty(s.id).length > 0 && <small className="empty-pan-indicator">{isEmpty(s.id).length} empty</small>}<b>{queueCount(s.id)}</b></span>
                </button>)}
              </div>
              <details className="student-directory"><summary>Student stories</summary>
                {(narration?.highlights ?? []).map((h) => <button className="link" key={h.student_id} onClick={() => pickStudent(h.student_id)}>{h.title}</button>)}
                {!narration?.highlights.length && <p className="guide-copy">Select a student in the hall to inspect their journey.</p>}
              </details>
            </> : step === "try" ? <>
              <p className="guide-eyebrow">A small change, tested</p><h2>What if we…</h2>
              <p className="guide-copy">Choose a scenario to compare with the current plan. Saved results open immediately; an animation loads when available.</p>
              {presets.map(([id, title, description]) => <button key={id} className="guided-preset" aria-pressed={draftPreset === id} onClick={() => setDraftPreset(id)}>
                <i aria-hidden /><span><b>{title}</b><small>{description}</small></span>
              </button>)}
              <button className="btn primary guide-action" disabled={!draftEntry} onClick={() => { if (draftEntry) { onTime(t); onReplay(draftEntry); setStep("compare"); } }}>Compare {result.n_nights} nights</button>
              {!draftEntry && <p className="guide-copy">No saved comparison for this scenario on this date. Use advanced settings to run a new comparison.</p>}
              <button className="link guide-advanced" onClick={() => nav("/lab")}>Advanced settings & all scenarios</button>
            </> : <>
              <p className="guide-eyebrow">{result.n_nights} simulated nights</p><h2>Results & trade-offs</h2><p className="guide-copy">{result.label}</p>
              {result.vs_baseline ? <>
                {([["waste_total", "Waste", "portions"], ["wait_p90", "Long waits (p90)", "min"], ["stockout_min_total", "Dish unavailability", "dish-min"]] as const).map(([id, label, unit]) => {
                  const metric = result.vs_baseline!.metrics[id];
                  return <div className="guided-metric" key={id}><div><span>{label}</span><b className={`chip ${metric.verdict === "better" ? "good" : metric.verdict === "worse" ? "warn" : ""}`}>{metric.verdict === "inconclusive" ? "Unclear" : metric.verdict === "same" ? "No real difference" : metric.verdict}</b></div>
                    <strong>{signed(metric.diff.p50, fmt1)} <small>{unit}</small></strong><p>Range {signed(metric.diff.p10, fmt1)} to {signed(metric.diff.p90, fmt1)}</p></div>;
                })}
                <p className="guide-footnote">Paired changes from baseline, with 10–90% ranges. Dish-minutes sum unavailable time across dishes.</p>
                <details className="comparison-more"><summary>Student outcomes & alerts</summary>
                  <p>{fmt1(result.headline.missed_first_choice_rate.p50 * 100)}% miss their first choice.</p>
                  <p>{Math.round(result.headline.missed_meal.p50)} students miss a meal (median).</p>
                  {groupFlags(result.flags).map((flag, i) => <p key={i}>{flag.text}</p>)}
                </details>
                <button className="btn primary guide-action" onClick={() => nav("/lab")}>Full results & cook sheet</button>
              </> : <><p className="guide-copy">This is the baseline. Try a change to see the effect across {result.n_nights} paired nights.</p><button className="btn primary guide-action" onClick={() => goStep("try")}>Choose a scenario</button></>}
            </>}
        </section>
      </aside>
      <div className="guided-stage">
        {!trace ? <section className="card replay-unavailable" aria-label="Replay availability">
          <p className="guide-eyebrow">{PRESET_LABELS[scenarios.find((e) => e.key === replayKey)?.preset ?? ""] ?? result.label} · {dateLabel(result.date)}</p>
          <h2>{replayLoading ? "Preparing the replay…" : "Replay unavailable"}</h2>
          <p role="status">{replayLoading ? "Your results are ready. The animation is loading separately." : replayNote ?? "No animation is available for this scenario. You can still explore its saved results."}</p>
          <div className="row">
            {baseline?.trace && baseline.key !== replayKey && <button className="btn primary" onClick={() => { onTime(t); onReplay(baseline); setStep("watch"); }}>Watch current plan</button>}
            <button className="btn" disabled={replayLoading} onClick={onRetryReplay}>{replayLoading ? "Loading replay…" : "Retry replay"}</button>
          </div>
          <button className="link guide-advanced" onClick={() => nav("/lab")}>Full results & advanced settings</button>
        </section> : <>
        <section className={`guided-viewport ${view === "top" ? "top-down-viewport" : ""}`} aria-label="Floor plan">
          <div className="hall-scene-heading">
            <div><h2>Worcester, second floor</h2><p>{result.label} · representative simulated night</p></div>
            <div className="hall-view-controls"><button onClick={() => setView(view === "isometric" ? "top" : "isometric")}>{view === "isometric" ? "Top-down view" : "Isometric view"}</button>
              <label>Color by <select value={colorBy} onChange={(e) => setColorBy(e.target.value as ColorBy)}><option value="wait">Wait time</option><option value="diet">Diet</option><option value="missed">Missed first choice</option></select></label></div>
          </div>
          {view === "isometric" ? <HallScene poses={poses} stations={visibleStations.map((s) => ({ id: s.id, pos: s.pos, line: queueCount(s.id), empty: isEmpty(s.id).length }))}
            selectedStation={station} selectedStudent={studentId} onStation={pickStation} onStudent={pickStudent} colorBy={colorBy} missed={missed} /> : <div className="plan-wrap">
              <svg viewBox={`0 0 ${PLAN_W} ${PLAN_H}`} role="img" aria-label={`Top-down plan at ${clock(t)}: ${c.inside} students inside`}>
                <image href="/plan/worcester-2f-plan.svg" width={PLAN_W} height={PLAN_H} />
                <g aria-hidden fill="#ded9d0" stroke="#817b72" strokeWidth={.6}>
                  {TABLE_SOLIDS.map((table) => <polygon key={table.id} points={table.polygon.map((p) => toPx(p).join(",")).join(" ")} />)}
                </g>
                {poses.map((p) => {
                  const [x, y] = toPx(p.xy);
                  const sel = p.id === studentId;
                  return (
                    <g key={p.id} className="student" onClick={() => pickStudent(p.id)}>
                      <circle cx={x} cy={y} r={sel ? 4.6 : 3.1} fill={color(p)} stroke={sel ? "var(--maroon)" : "#fff"} strokeWidth={sel ? 2 : 0.6} />
                      {p.hands !== "empty" && (
                        <circle cx={x + 3} cy={y - 2.6} r={1.5} fill={p.hands === "plate" ? "#fff" : "var(--diet-halal)"} stroke="var(--text-3)" strokeWidth={0.4} />
                      )}
                    </g>
                  );
                })}
                {Object.entries(ANCHORS).map(([sid, [x, y]]) => {
                  const empties = isEmpty(sid === "desserts" ? "desserts" : sid);
                  const label = sid === "desserts" ? "Desserts · Breads" : stationName(sid);
                  const line = sid === "desserts" ? (c.lines.desserts ?? 0) + (c.lines.breads ?? 0) : c.lines[sid] ?? 0;
                  const w = label.length * 4.6 + 26 + (empties.length ? 46 : 0);
                  const sel = sid === station;
                  return (
                    <g key={sid} className="station-tag" transform={`translate(${x - (w * 1.3) / 2},${y - 24}) scale(1.3)`} onClick={() => pickStation(sid)}
                      role="button" tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pickStation(sid); } }} aria-label={`${label}: ${line} in line${empties.length ? `, ${empties.length} empty` : ""}`}>
                      <rect width={w} height={14} rx={4} fill="#fff" stroke={sel ? "var(--maroon)" : empties.length ? "var(--bad)" : "var(--border-strong)"} strokeWidth={sel ? 1.2 : 0.6} />
                      <text x={5} y={10} fontSize={7.5} fontWeight={500} fill="var(--text)">{label}</text>
                      <rect x={label.length * 4.6 + 8} y={2.5} width={14} height={9} rx={4.5} fill={line >= 12 ? "var(--warn-soft)" : "var(--bg-muted)"} />
                      <text x={label.length * 4.6 + 15} y={9.6} fontSize={6.5} textAnchor="middle" fill={line >= 12 ? "var(--warn)" : "var(--text-2)"}>{line}</text>
                      {empties.length > 0 && (
                        <>
                          <rect x={label.length * 4.6 + 25} y={2.5} width={42} height={9} rx={4.5} fill="var(--bad-soft)" />
                          <text x={label.length * 4.6 + 46} y={9.6} fontSize={6} textAnchor="middle" fill="var(--bad)">{empties.length === 1 ? `${short(empties[0])} out` : `${empties.length} pans out`}</text>
                        </>
                      )}
                    </g>
                  );
                })}
                {MARKERS.map((m) => {
                  const w = m.text.length * 4.3 + 12;
                  return (
                    <g key={m.text} transform={`translate(${m.xy[0] - (w * 1.25) / 2},${m.xy[1] - 9}) scale(1.25)`} pointerEvents="none">
                      <rect width={w} height={14} rx={7} fill={m.dark ? "var(--text)" : "#ffffffd9"} />
                      <text x={w / 2} y={10} fontSize={7.5} textAnchor="middle" fill={m.dark ? "#fff" : "var(--text-2)"}>{m.text}</text>
                    </g>
                  );
                })}
              </svg>
            </div>}
          <div className="hall-live-stats" aria-label="Right now">
            <span><b>{c.inside}</b> inside</span><span><b>{c.inLine}</b> waiting</span><span><b>{emptyNow.length}</b> empty pans</span>
            <small>{c.seated} seated · {clock(t)}</small>
          </div>
        </section>
        <Timeline result={result} trace={trace} t={t} setT={seek} playing={playing} toggle={toggle} speed={speed} setSpeed={setSpeed} end={replayEnd} />
        <div className="hall-replay-options" data-open={replayOptionsOpen}>
          <button className="replay-options-toggle" aria-expanded={replayOptionsOpen} aria-controls="replay-options" onClick={() => setReplayOptionsOpen(!replayOptionsOpen)}>
            Replay options & legend <span aria-hidden>{replayOptionsOpen ? "−" : "+"}</span>
          </button>
        <div className="hall-bottom-bar" id="replay-options">
          <label>Replay scenario <select value={replayKey} onChange={(e) => { const entry = replays.find((r) => r.key === e.target.value); if (entry) { onTime(t); onReplay(entry); } }}>
            {!replays.some((r) => r.key === replayKey) && <option value={replayKey}>{result.label}</option>}
            {replays.map((e) => <option key={e.key} value={e.key}>{e.preset ? PRESET_LABELS[e.preset] ?? e.label : "Current plan (baseline)"}{e.lever ? ` · ${e.lever}` : ""}</option>)}
          </select></label>
          <div className="hall-scene-legend">
            {colorBy === "wait" ? <><span><i style={{ background: "#a4d7b7" }} />Under 5 min</span><span><i style={{ background: "#eac28d" }} />5–10 min</span><span><i style={{ background: "#f39e97" }} />Over 10 min</span></> : colorBy === "diet" ? Object.keys(DIET_COLOR).map((diet) => <span key={diet}><i style={{ background: DIET_COLOR[diet] }} />{dietLabel(diet)}</span>) : <><span><i style={{ background: "#f39e97" }} />Missed first choice</span><span><i style={{ background: "#c2d0d5" }} />Got first choice</span></>}
          </div>
          <p>Illustrated hall · assumed seating · one figure per student{longest && queueCount(longest.id) > 0 ? ` · Longest line: ${stationName(longest.id)} (${queueCount(longest.id)})` : ""}</p>
          <details className="replay-metadata"><summary>Replay information</summary><p>Representative night {trace.night_seed} of {result.n_nights}. Serving ends at {clock(result.close)}; playback includes the following 30 minutes. Station menus and pan levels follow the selected time.</p></details>
        </div>
        </div>
        </>}
      </div>
    </div>
  );
}

const short = (name: string) => name.split(/[ (]/)[0];

/** First choice = the dish of the first line joined / walked away from / found empty; missed if never served. */
export function missedFirst(students: TraceStudent[]) {
  const out = new Set<number>();
  for (const s of students) {
    const first = s.events.find((e) => e.dish && (e.type === "queue_join" || e.type === "balk" || e.type === "empty_pan"));
    if (first && !s.events.some((e) => e.type === "served" && e.dish === first.dish)) out.add(s.id);
  }
  return out;
}

function Timeline({ result, trace, t, setT, playing, toggle, speed, setSpeed, end }: {
  result: Result; trace: Trace; t: number; setT: (t: number) => void;
  playing: boolean; toggle: () => void; speed: number; setSpeed: (speed: number) => void; end: number;
}) {
  const bins = useMemo(() => {
    const values = new Array(Math.ceil((end - result.open) / 10)).fill(0);
    for (const student of trace.students) {
      const i = Math.floor((student.events[0].t - result.open) / 10);
      if (i >= 0 && i < values.length) values[i]++;
    }
    return values;
  }, [trace, result.open, end]);
  const max = Math.max(1, ...bins);
  return <section className="card guided-timeline" aria-label="Timeline">
    <div className="playback-toolbar">
      <button className="btn playback-toggle" aria-label={playing ? "Pause" : "Play"} onClick={toggle}><span aria-hidden>{playing ? "Ⅱ" : "▷"}</span>{playing ? "Pause" : "Play"}</button>
      <b className="playback-clock tnum" data-testid="playback-clock">{clock(t)}</b>
      <label className="playback-speed">Speed <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Playback speed">{SPEEDS.map((value) => <option value={value} key={value}>{value}×</option>)}</select></label>
      <span className="playback-status" role="status">{playing ? "Playing full replay" : t >= end ? "Replay complete" : "Paused"}</span>
      <button className="link" onClick={() => setT(result.open)}>Restart</button>
    </div>
    <div className="playback-arrivals" aria-hidden>{bins.map((v, i) => <span key={i} style={{ height: `${Math.max(3, v / max * 100)}%`, opacity: result.open + i * 10 <= t ? 1 : .32 }} />)}</div>
    <input type="range" className="playback-scrubber" min={result.open} max={end} step={.05} value={t} onChange={(e) => setT(Number(e.target.value))}
      aria-label="Playback time" aria-valuetext={clock(t)} onKeyDown={(e) => {
        if (e.key === " ") { e.preventDefault(); toggle(); }
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); setT(t + (e.key === "ArrowRight" ? 5 : -5)); }
      }} />
    <div className="playback-ticks"><span>{clock(result.open)}</span><span>Students arriving · continuous replay</span><span>{clock(end)}</span></div>
  </section>;
}

function StationCard({ result, trace, sid, t, pans, inLine }: {
  result: Result; trace: Trace; sid: string; t: number; inLine: number;
  pans: ReturnType<typeof panTimelines>;
}) {
  const st = result.stations.find((s) => s.id === sid);
  const ids = sid === "desserts" ? ["desserts", "breads"] : [sid];
  const activePeriod = t >= (result.dinner_close ?? result.close) ? "late_night" : "dinner";
  const [menuPeriod, setMenuPeriod] = useState<"dinner" | "late_night">(activePeriod);
  useEffect(() => setMenuPeriod(activePeriod), [activePeriod, sid]);
  const allDishes = result.cook_sheet.filter((d) => ids.includes(d.station));
  const periods = [...new Set(allDishes.map((d) => d.period ?? "dinner"))];
  // Closed stations still expose their complete dinner menu during late night.
  const shownPeriod = periods.includes(menuPeriod) ? menuPeriod : periods[0] ?? "dinner";
  const dishes = allDishes.filter((d) => (d.period ?? "dinner") === shownPeriod && !d.to_order && pans.has(d.dish_id));
  const toOrder = allDishes.filter((d) => (d.period ?? "dinner") === shownPeriod && d.to_order);
  const [opens, closes] = dishWindow(result, shownPeriod);
  const serviceStatus = t < opens ? "Not open yet" : t >= closes ? "Service ended" : "Serving now";
  const inspectionT = Math.max(opens, Math.min(t, closes - .001));
  const series = useMemo(() => {
    const main = lineSeries(trace, sid, result.open, result.close + 30, 10);
    if (sid !== "desserts") return main;
    const breads = lineSeries(trace, "breads", result.open, result.close + 30, 10);
    return main.map((v, i) => v + (breads[i] ?? 0));
  }, [trace, sid, result.open, result.close]);
  return (
    <section className="card station-detail" aria-label={`${stationName(sid)} station`}>
      <h3>{sid === "desserts" ? "Desserts · Breads" : stationName(sid)}</h3>
      <p className="station-service">{st?.self_serve ? "Self-serve" : `${st?.servers ?? 0} servers`}{st?.to_order ? " · made to order" : ""}</p>
      <div className="station-summary"><div><strong>{inLine}</strong><span>in line now</span></div><span className={`chip ${t >= opens && t < closes ? "good" : ""}`}>{serviceStatus}</span></div>
      {periods.length > 1 && <label className="station-period">Menu <select value={shownPeriod} onChange={(e) => setMenuPeriod(e.target.value as "dinner" | "late_night")}>{periods.map((period) => <option key={period} value={period}>{period === "late_night" ? "Late night" : "Dinner"}</option>)}</select></label>}
      <div className="station-menu-heading"><h4>{shownPeriod === "late_night" ? "Late-night dishes" : "Dinner dishes"}</h4><span>{t >= closes ? "At close" : t < opens ? "At opening" : "Portions left"}</span></div>
      <div className="station-dishes">
        {dishes.map((d) => {
          const tl = pans.get(d.dish_id)!;
          const level = levelAt(tl, inspectionT), peak = peakLevel(tl);
          const cooking = tl.batches.find((b) => b.start <= inspectionT && inspectionT < b.ready);
          return <div className="station-dish" key={d.dish_id}>
            <div><span>{d.name}</span><b className={level <= 0 ? "bad-text" : ""}>{level <= 0 ? "Empty" : level}</b></div>
            <Meter frac={level / peak} color={level / peak < .15 ? "var(--bad)" : level / peak < .35 ? "var(--warn)" : "var(--good)"} width={240} />
            {cooking && <small>Next batch ready at {clock(cooking.ready)}</small>}
          </div>;
        })}
        {toOrder.map((d) => <div className="station-dish" key={d.dish_id}><div><span>{d.name}</span></div><small>Made to order · no pan inventory</small></div>)}
        {!dishes.length && !toOrder.length && <p className="guide-copy">No menu items for this station on this date.</p>}
      </div>
      <div className="station-queue-chart"><h4>Queue throughout the night</h4><Spark values={series} highlight={Math.floor((t - result.open) / 10)} /><div><span>{clock(result.open)}</span><span>{clock(result.close + 30)}</span></div></div>
    </section>
  );
}

function StudentCard({ s, narration, names, t, missed, configHash, seed, onPick }: {
  s: TraceStudent; narration: Narration | null; names: Record<string, string>; t: number; missed: boolean;
  configHash: string; seed: number; onPick: (id: number) => void;
}) {
  const cached = narration?.students[String(s.id)];
  const highlight = narration?.highlights.find((h) => h.student_id === s.id);
  const api = useApiUp();
  // On-demand narration from the API for students the bundle didn't pre-generate (UI_ISSUES U10).
  const [live, setLive] = useState<{ text: string; source: string; title?: string } | "loading" | { error: string } | null>(null);
  const narrate = () => {
    setLive("loading");
    narrateStudent(configHash, seed, s.id).then(
      (r) => setLive({ text: r.text, source: r.source, title: r.card?.title }),
      (e) => setLive({ error: (e as Error).message }),
    );
  };
  // highlight students of an API replay have no stored text yet: write it right away
  useEffect(() => {
    if (!cached && highlight && api && live === null) narrate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, cached, highlight]);
  const story = cached ? { text: cached.text, source: cached.source } : live && typeof live === "object" && "text" in live ? live : null;
  const title = cached?.card.title ?? (story && "title" in story ? story.title : undefined) ?? highlight?.title ?? `Student #${s.id} · ${s.persona.replace(/_/g, " ")}`;
  const [all, setAll] = useState(false);
  const evs = collapse(s.events, names);
  const shown = all ? evs : evs.length > 12 ? [...evs.slice(0, 8), null, ...evs.slice(-3)] : evs;
  return (
    <section className="card" aria-label="Student">
      <div className="row" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
        <i className="dot" style={{ background: DIET_COLOR[s.diet] ?? DIET_COLOR.none, width: 10, height: 10, marginTop: 5 }} />
        <div>
          <h3>{title.split(" · ")[0]}</h3>
          <div className="hint">{dietLabel(s.diet)} · {title.split(" · ")[1] ?? s.persona}{missed ? " · missed first choice" : ""}</div>
        </div>
      </div>
      <div className="timeline-ev" style={{ margin: "10px 0" }}>
        {shown.map((e, i) =>
          e === null ? (
            <button key="more" className="btn ghost" style={{ gridColumn: "1 / -1", height: 24, padding: 0 }} onClick={() => setAll(true)}>
              … {evs.length - 11} more events
            </button>
          ) : (
            <div key={i} style={{ display: "contents", opacity: e.t > t ? 0.45 : 1 }}>
              <span className="mono">{clock(e.t, false)}</span>
              <span>{e.text}</span>
            </div>
          ),
        )}
      </div>
      {story ? (
        <div className="narration">
          <div className="overline" style={{ fontStyle: "normal", color: "var(--maroon)", marginBottom: 4 }}>
            AI narration · from recorded events{story.source === "template" ? " (template)" : ""}
          </div>
          “{story.text}”
        </div>
      ) : live === "loading" ? (
        <div className="hint" role="status">Writing this student's story…</div>
      ) : api ? (
        <div className="stack" style={{ gap: 6 }}>
          {live && "error" in live && <div className="hint" role="alert">Couldn't narrate: {live.error}</div>}
          <button className="btn" onClick={narrate}>Narrate this student</button>
        </div>
      ) : (
        <div className="hint">
          Narration is pre-generated for the night's highlight students; start the API (<code>make server</code>) to narrate anyone.
          {narration?.highlights.length ? <> Try {narration.highlights.slice(0, 3).map((h, i) => (
            <span key={h.student_id}>{i ? ", " : ""}<button className="link" onClick={() => onPick(h.student_id)}>#{h.student_id}</button></span>
          ))}.</> : null}
        </div>
      )}
    </section>
  );
}

/** Timeline rows; runs of "walked away" / "empty pan" within 2 minutes fold into one row. */
function collapse(events: TraceStudent["events"], names: Record<string, string>) {
  const out: { t: number; text: string }[] = [];
  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    if (e.type === "balk" || e.type === "empty_pan") {
      let j = i;
      while (j + 1 < events.length && events[j + 1].type === e.type && events[j + 1].t - e.t < 2) j++;
      if (j > i) {
        const what = e.type === "balk" ? "lines (too long)" : "empty pans";
        const stations = [...new Set(events.slice(i, j + 1).map((x) => stationName(x.station ?? "")))];
        out.push({ t: e.t, text: `Looked over and skipped ${j - i + 1} ${what}: ${stations.slice(0, 3).join(", ")}${stations.length > 3 ? "…" : ""}` });
        i = j;
        continue;
      }
    }
    out.push({ t: e.t, text: eventText(e, names) });
  }
  return out;
}

function eventText(e: TraceStudent["events"][number], names: Record<string, string>) {
  const dish = e.dish ? names[e.dish] ?? e.dish : "";
  const st = e.station ? stationName(e.station) : "";
  switch (e.type) {
    case "enter": return "Swiped in";
    case "queue_join": return `Joined ${st} line (${e.line ?? 0} ahead)`;
    case "served": return `${dish} · ${e.vessel ?? "plate"}`;
    case "balk": return `Skipped ${st}: ${e.line ?? 0} in line`;
    case "empty_pan": return `${dish} empty`;
    case "sit": return `Sat down · ${e.seat === "back" ? "back seating" : "main room"}`;
    case "seconds": return `Went back for seconds${dish ? ` (${dish})` : ""}`;
    case "retry": return `Looked again at the line for ${dish || st}`;
    case "closed": return `${dish} service ended while in line`;
    case "dish_return": return "Dropped dishes at the return";
    case "exit": return "Left";
  }
}
