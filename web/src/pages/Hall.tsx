import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ErrorBox, Loading, TopBar } from "../components/Shell";
import { Meter, Spark } from "../components/Charts";
import { apiUp, loadApiHighlights, loadApiTrace, loadNarration, loadTrace, narrateStudent } from "../lib/data";
import { clock, dateLabel, dishWindow, stationName } from "../lib/format";
import { useApiUp, useAsync, useScenarioResult } from "../lib/hooks";
import {
  counters, levelAt, lineSeries, panTimelines, peakLevel, PLAN_H, PLAN_W, poseAt, toPx, type Pose,
} from "../lib/playback";
import { useApp } from "../lib/store";
import type { CookRow, Narration, Result, Trace, TraceStudent } from "../lib/types";

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
type View = "overview" | "serving" | "top";
const SPEEDS = [1, 10, 30, 60];

type Replay = { trace: Trace | null; source: "bundle" | "api" | null; note?: string };

export default function Hall() {
  const { date, scenario, hallTime, setHallTime } = useApp();
  const res = useScenarioResult(date, scenario);
  const entry = res.data?.entry;
  const seed = res.data?.result.median_seed;
  // Bundle trace first; otherwise ask the API to replay the median night (UI_ISSUES U3).
  const replay = useAsync<Replay>(async () => {
    if (!entry) return { trace: null, source: null };
    if (entry.trace) return { trace: await loadTrace(entry), source: "bundle" };
    if (!(await apiUp())) {
      return { trace: null, source: null, note: "No replay was exported to the offline bundle for this scenario. Start the API (`make serve`) to replay it, or pick a preset." };
    }
    try {
      return { trace: await loadApiTrace(entry.config_hash, seed), source: "api" };
    } catch (e) {
      return { trace: null, source: null, note: `The API couldn't replay this scenario (${(e as Error).message}).` };
    }
  }, [entry?.key]);
  const trace = replay.data?.trace ?? null;
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
    ? `${replay.data?.source === "api" ? "Replayed on the API · " : ""}Worcester 2nd floor · simulated median night (seed ${res.data.result.median_seed} of ${res.data.result.n_nights})`
    : "Worcester 2nd floor";
  return (
    <>
      <TopBar title="Hall" sub={sub} scenario={res.data?.fallback ? { preset: null, lever: "" } : scenario} />
      <div className="content">
        {res.error && <ErrorBox error={res.error} />}
        {res.data?.fallback && <div className="banner">{res.data.reason ?? `This scenario isn't available for ${dateLabel(date)}; showing the baseline.`}</div>}
        {replay.data?.note && <div className="banner">{replay.data.note}</div>}
        {replay.error && <ErrorBox error={replay.error} />}
        {(res.loading || replay.loading) && !trace && <Loading what="the night's replay (≈1 MB)" />}
        {res.data && trace && (
          <HallView key={entry?.key} result={res.data.result} trace={trace} narration={narr.data ?? null}
            configHash={entry!.config_hash} initialT={hallTime} onTime={setHallTime} />
        )}
      </div>
    </>
  );
}

function HallView({ result, trace, narration, configHash, initialT, onTime }: {
  result: Result; trace: Trace; narration: Narration | null; configHash: string; initialT: number | null; onTime: (t: number) => void;
}) {
  const nav = useNavigate();
  const [t, setT] = useState(() => initialT ?? Math.min(result.close, 18 * 60 + 12));
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(30);
  const [colorBy, setColorBy] = useState<ColorBy>("diet");
  const [view, setView] = useState<View>("top");
  const [station, setStation] = useState<string>("tandoor");
  // Default to a highlight student who is inside at the starting time, so the card matches the map.
  const pickDefault = () => {
    const hs = narration?.highlights ?? [];
    const inside = hs.find((h) => {
      const s = trace.students.find((x) => x.id === h.student_id);
      return s && s.events[0].t <= t && t < s.events[s.events.length - 1].t;
    });
    // none inside yet: the highlight who arrives soonest after the starting time
    const next = [...hs].map((h) => ({ h, s: trace.students.find((x) => x.id === h.student_id) }))
      .filter((x) => x.s).sort((a, b) => Math.abs(a.s!.events[0].t - t) - Math.abs(b.s!.events[0].t - t))[0]?.h;
    return (inside ?? next ?? hs[0])?.student_id ?? null;
  };
  const [studentId, setStudentId] = useState<number | null>(pickDefault);
  // narration can arrive after the trace
  useEffect(() => {
    if (studentId == null && narration) setStudentId(pickDefault());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [narration]);

  // keep the shared context in sync (Dashboard "jump to time" lands here)
  useEffect(() => onTime(t), [t, onTime]);

  // playback clock
  const tRef = useRef(t);
  tRef.current = t;
  useEffect(() => {
    if (!playing) return;
    let raf = 0, last = performance.now(), acc = 0;
    const tick = (now: number) => {
      acc += ((now - last) / 1000) * (speed / 60); // sim minutes
      last = now;
      if (acc > 1 / 30 || speed === 1) {
        const next = tRef.current + acc;
        acc = 0;
        if (next >= result.close + 30) { setPlaying(false); setT(result.close + 30); return; }
        setT(next);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, result.close]);

  const names = useMemo(() => Object.fromEntries(result.cook_sheet.map((d) => [d.dish_id, d.name])), [result]);
  const stationPos = useMemo(() => Object.fromEntries(result.stations.map((s) => [s.id, s.pos as [number, number]])), [result]);
  const pans = useMemo(() => panTimelines(trace), [trace]);
  const missed = useMemo(() => missedFirst(trace.students), [trace]);
  const byId = useMemo(() => new Map(trace.students.map((s) => [s.id, s])), [trace]);

  const poses = useMemo(() => {
    const out: Pose[] = [];
    for (const s of trace.students) {
      const p = poseAt(s, t, stationPos);
      if (p) out.push(p);
    }
    return out;
  }, [trace, t, stationPos]);
  const c = counters(poses);
  const longest = Object.entries(c.lines).sort((a, b) => b[1] - a[1])[0];
  // Only the dishes of the service period on at time t (dinner, then late night from 9 pm).
  const period = t >= (result.dinner_close ?? result.close) ? "late_night" : "dinner";
  const inPeriod = (d: CookRow) => (d.period ?? "dinner") === period;
  const stationDishes = (sid: string) => result.cook_sheet.filter((d) => d.station === sid && !d.to_order && pans.has(d.dish_id) && inPeriod(d));
  const rowOf = useMemo(() => Object.fromEntries(result.cook_sheet.map((d) => [d.dish_id, d])), [result]);
  const emptyNow = [...pans.values()].filter((tl) => {
    const d = rowOf[tl.dish];
    if (!d) return false;
    const [a, b] = dishWindow(result, d.period);
    return t >= a && t < b && levelAt(tl, t) <= 0;
  });
  const isEmpty = (sid: string) => emptyNow.filter((tl) => rowOf[tl.dish]?.station === sid).map((tl) => names[tl.dish] ?? tl.dish);

  const color = (p: Pose) => {
    if (colorBy === "diet") return DIET_COLOR[p.diet] ?? DIET_COLOR.none;
    if (colorBy === "missed") return missed.has(p.id) ? "var(--bad)" : "var(--border-strong)";
    if (p.phase !== "line") return "var(--border-strong)";
    const w = p.waitedMin ?? 0;
    return w > 10 ? "var(--bad)" : w > 5 ? "var(--warn)" : "var(--good)";
  };

  return (
    <div className="grid-2">
      <div className="stack">
        <section className="card" aria-label="Floor plan">
          <div className="row" style={{ marginBottom: 12 }}>
            <div className="seg" role="group" aria-label="Camera">
              <button aria-pressed={view === "overview"} onClick={() => setView("overview")}>3D overview</button>
              <button disabled title="Not built yet: needs the 3D scene (SPEC §3.1)">Serving line</button>
              <button aria-pressed={view === "top"} onClick={() => setView("top")}>Top-down</button>
            </div>
            <span className="spacer" />
            <span className="faint">Color by</span>
            <div className="seg" role="group" aria-label="Color by">
              <button aria-pressed={colorBy === "diet"} onClick={() => setColorBy("diet")}>Diet</button>
              <button aria-pressed={colorBy === "wait"} onClick={() => setColorBy("wait")}>Wait time</button>
              <button aria-pressed={colorBy === "missed"} onClick={() => setColorBy("missed")}>Missed 1st choice</button>
            </div>
          </div>
          {view === "overview" ? (
            <div className="plan-wrap">
              <img src="/plan/worcester-2f-render.svg" alt="3D overview of Worcester second floor" style={{ width: "100%", display: "block" }} />
              <div className="banner" style={{ position: "absolute", left: 12, bottom: 12, right: 12 }}>
                Static render. Animated 3D isn't built yet; switch to Top-down to watch the night.
              </div>
            </div>
          ) : (
            <div className="plan-wrap">
              <svg viewBox={`0 0 ${PLAN_W} ${PLAN_H}`} role="img" aria-label={`Top-down plan at ${clock(t)}: ${c.inside} students inside`}>
                <image href="/plan/worcester-2f-plan.svg" width={PLAN_W} height={PLAN_H} />
                {poses.map((p) => {
                  const [x, y] = toPx(p.xy);
                  const sel = p.id === studentId;
                  return (
                    <g key={p.id} className="student" onClick={() => setStudentId(p.id)}>
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
                    <g key={sid} className="station-tag" transform={`translate(${x - (w * 1.3) / 2},${y - 24}) scale(1.3)`} onClick={() => setStation(sid)}
                      role="button" aria-label={`${label}: ${line} in line${empties.length ? `, ${empties.length} empty` : ""}`}>
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
            </div>
          )}
          <div className="legend">
            {colorBy === "diet" && Object.entries(DIET_COLOR).map(([k, v]) => <span key={k}><i className="dot" style={{ background: v, margin: 0 }} />{dietLabel(k)}</span>)}
            {colorBy === "wait" && (<>
              <span><i className="dot" style={{ background: "var(--good)", margin: 0 }} />in line &lt; 5 min</span>
              <span><i className="dot" style={{ background: "var(--warn)", margin: 0 }} />5–10 min</span>
              <span><i className="dot" style={{ background: "var(--bad)", margin: 0 }} />&gt; 10 min</span>
              <span><i className="dot" style={{ background: "var(--border-strong)", margin: 0 }} />not in a line</span>
            </>)}
            {colorBy === "missed" && (<>
              <span><i className="dot" style={{ background: "var(--bad)", margin: 0 }} />never got first choice</span>
              <span><i className="dot" style={{ background: "var(--border-strong)", margin: 0 }} />got it</span>
            </>)}
            <span><i className="dot" style={{ background: "#fff", border: "1px solid var(--text-3)", margin: 0 }} />plate</span>
            <span><i className="dot" style={{ background: "var(--diet-halal)", margin: 0 }} />bowl</span>
            <span className="spacer" />
            <span className="faint">Seating assumed · 1 dot = 1 student</span>
          </div>
        </section>

        <Timeline result={result} trace={trace} t={t} setT={(x) => { setPlaying(false); setT(x); }}
          playing={playing} setPlaying={setPlaying} speed={speed} setSpeed={setSpeed}
          onCompare={() => nav("/lab")} />
      </div>

      <div className="stack">
        <section className="card" aria-label="Right now">
          <div className="card-head">
            <h3>Right now</h3>
            <span className="chip mono">{clock(t)}</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {([["Inside", c.inside], ["In line", c.inLine], ["Seated", c.seated], ["Pans empty", emptyNow.length]] as const).map(([k, v]) => (
              <div key={k} style={{ background: "var(--bg-muted)", borderRadius: 8, padding: "8px 10px" }}>
                <div className="muted" style={{ fontSize: 12 }}>{k}</div>
                <div style={{ fontSize: 22, fontWeight: 600, lineHeight: "28px" }} className="tnum">{v}</div>
              </div>
            ))}
          </div>
          <div className="row" style={{ marginTop: 10, fontSize: 12 }}>
            <span className="muted">Longest line</span>
            {longest ? <span className="chip warn">{stationName(longest[0])} · {longest[1]}</span> : <span className="faint">none</span>}
          </div>
        </section>

        <StationCard result={result} trace={trace} sid={station} t={t} pans={pans} stationDishes={stationDishes} />
        {studentId != null && byId.get(studentId) && (
          <StudentCard key={studentId} s={byId.get(studentId)!} narration={narration} names={names} t={t} missed={missed.has(studentId)}
            configHash={configHash} seed={trace.night_seed} onPick={setStudentId} />
        )}
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

function Timeline({ result, trace, t, setT, playing, setPlaying, speed, setSpeed, onCompare }: {
  result: Result; trace: Trace; t: number; setT: (t: number) => void; playing: boolean; setPlaying: (p: boolean) => void;
  speed: number; setSpeed: (s: number) => void; onCompare: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const t0 = result.open, t1 = result.close;
  const bins = useMemo(() => {
    const n = Math.ceil((t1 - t0) / 10);
    const b = new Array(n).fill(0);
    for (const s of trace.students) {
      const e = s.events[0];
      const i = Math.floor((e.t - t0) / 10);
      if (i >= 0 && i < n) b[i]++;
    }
    return b;
  }, [trace, t0, t1]);
  const marks = useMemo(() => {
    const out: { t: number; kind: "empty" | "ready" | "expire" }[] = [];
    for (const p of trace.pans) {
      if (p.t <= t0 || p.t >= t1) continue;
      if (p.event === "batch_ready") out.push({ t: p.t, kind: "ready" });
      else if (p.event === "expire") out.push({ t: p.t, kind: "expire" });
      else if (p.portions === 0 && p.event !== "batch_start") out.push({ t: p.t, kind: "empty" });
    }
    // pans that hit zero from serving: one mark per dish run-out window
    for (const d of result.cook_sheet) if (d.runout_window && d.runout_share >= 0.5) out.push({ t: d.runout_window.p50_min, kind: "empty" });
    return out;
  }, [trace, result, t0, t1]);
  const max = Math.max(1, ...bins);
  const frac = (x: number) => (x - t0) / (t1 - t0);
  const pick = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    setT(t0 + Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * (t1 - t0));
  };
  const markColor = { empty: "var(--bad)", ready: "var(--good)", expire: "var(--warn)" };
  return (
    <section className="card" aria-label="Timeline">
      <div className="row" style={{ marginBottom: 8 }}>
        <button className="btn primary" style={{ width: 32, padding: 0, justifyContent: "center", borderRadius: 16 }}
          onClick={() => { if (t >= t1 + 29) setT(t0); setPlaying(!playing); }} aria-label={playing ? "Pause" : "Play"}>
          {playing ? "❚❚" : "▶"}
        </button>
        <b style={{ fontSize: 22, fontWeight: 600, minWidth: 92 }} className="tnum">{clock(t)}</b>
        <div className="seg" role="group" aria-label="Speed">
          {SPEEDS.map((s) => <button key={s} aria-pressed={speed === s} onClick={() => setSpeed(s)}>{s}×</button>)}
        </div>
        <span className="spacer" />
        <span className="legend" style={{ margin: 0 }}>
          <span><i className="dot" style={{ background: "var(--bad)", margin: 0 }} />Pan empty</span>
          <span><i className="dot" style={{ background: "var(--good)", margin: 0 }} />Batch ready</span>
          <span><i className="dot" style={{ background: "var(--warn)", margin: 0 }} />Food expired</span>
        </span>
        <button className="btn" onClick={onCompare} title="Split-screen compare isn't built (SPEC P2); opens the What-If Lab">Compare with…</button>
      </div>
      <div
        ref={ref} className="scrub" role="slider" tabIndex={0} aria-label="Playback time"
        aria-valuemin={t0} aria-valuemax={t1} aria-valuenow={Math.round(t)} aria-valuetext={clock(t)}
        onPointerDown={(e) => { (e.target as Element).setPointerCapture?.(e.pointerId); pick(e.clientX); }}
        onPointerMove={(e) => { if (e.buttons) pick(e.clientX); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") setT(Math.min(t1, t + 5));
          if (e.key === "ArrowLeft") setT(Math.max(t0, t - 5));
          if (e.key === " ") { e.preventDefault(); setPlaying(!playing); }
        }}
      >
        <svg viewBox="0 0 1000 90" preserveAspectRatio="none" width="100%" height="90">
          {marks.map((m, i) => <circle key={i} cx={frac(m.t) * 1000} cy={6} r={2.4} fill={markColor[m.kind]} />)}
          {bins.map((v, i) => {
            const x = (i / bins.length) * 1000, w = 1000 / bins.length;
            const played = t0 + (i + 1) * 10 <= t + 0.001;
            return <rect key={i} x={x + w * 0.08} y={70 - (v / max) * 54} width={w * 0.84} height={(v / max) * 54} rx={2} fill={played ? "var(--data-1)" : "var(--band)"} />;
          })}
          <line x1={frac(Math.min(t, t1)) * 1000} x2={frac(Math.min(t, t1)) * 1000} y1={0} y2={72} stroke="var(--maroon)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
        </svg>
        <div className="row" style={{ justifyContent: "space-between", fontSize: 11, color: "var(--text-3)", marginTop: -14 }}>
          {Array.from({ length: Math.floor((t1 - t0) / 30) + 1 }, (_, i) => <span key={i}>{clock(t0 + i * 30, false)}</span>)}
        </div>
      </div>
      <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>Bars: students arriving per 10 min on this night · arrow keys step 5 min, space plays</div>
    </section>
  );
}

function StationCard({ result, trace, sid, t, pans, stationDishes }: {
  result: Result; trace: Trace; sid: string; t: number;
  pans: ReturnType<typeof panTimelines>; stationDishes: (sid: string) => Result["cook_sheet"];
}) {
  const st = result.stations.find((s) => s.id === sid);
  const ids = sid === "desserts" ? ["desserts", "breads"] : [sid];
  const dishes = ids.flatMap(stationDishes);
  const toOrder = result.cook_sheet.filter((d) => ids.includes(d.station) && d.to_order);
  const series = useMemo(() => lineSeries(trace, sid, result.open, Math.min(result.close, 19 * 60), 10), [trace, sid, result]);
  const nowIdx = Math.floor((t - result.open) / 10);
  const inLine = useMemo(() => {
    let n = 0;
    for (const s of trace.students) { const p = poseAt(s, t); if (p?.phase === "line" && p.station === sid) n++; }
    return n;
  }, [trace, t, sid]);
  return (
    <section className="card" aria-label={`${stationName(sid)} station`}>
      <h3>{sid === "desserts" ? "Desserts · Breads" : stationName(sid)}</h3>
      <div className="hint" style={{ marginBottom: 10 }}>
        {sid === "tandoor" ? "Center island, east side · " : ""}
        {st ? `${st.self_serve ? "self-serve" : `${st.servers} servers`}${st.to_order ? " · made to order" : ""} · ` : ""}{inLine} in line
      </div>
      <div className="stack" style={{ gap: 8 }}>
        {dishes.map((d) => {
          const tl = pans.get(d.dish_id)!;
          const lvl = levelAt(tl, t);
          const peak = peakLevel(tl);
          const cooking = tl.batches.find((b) => b.start <= t && t < b.ready);
          const empty = lvl <= 0 && t >= result.open;
          return (
            <div key={d.dish_id} className="row" style={{ flexWrap: "nowrap", fontSize: 13 }}>
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={d.name}>{d.name}</span>
              <Meter frac={lvl / peak} color={lvl / peak < 0.15 ? "var(--bad)" : lvl / peak < 0.35 ? "var(--warn)" : "var(--good)"} width={44} />
              <span className="num" style={{ width: 56, textAlign: "right", color: empty ? "var(--bad)" : undefined }}>
                {empty ? (cooking ? `next ${clock(cooking.ready, false)}` : "empty") : lvl}
              </span>
            </div>
          );
        })}
        {toOrder.length > 0 && <div className="hint">{toOrder.length} dish{toOrder.length > 1 ? "es" : ""} made to order (no pan)</div>}
        {!dishes.length && !toOrder.length && <div className="hint">No dishes from this station tonight.</div>}
      </div>
      <div className="hint" style={{ margin: "12px 0 4px" }}>Queue length, {clock(result.open, false)}–7:00 pm</div>
      <Spark values={series} highlight={nowIdx} />
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
          Narration is pre-generated for the night's highlight students; start the API (<code>make serve</code>) to narrate anyone.
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
