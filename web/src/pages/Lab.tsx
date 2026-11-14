import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ErrorBox, Loading, TopBar } from "../components/Shell";
import {
  apiUp, ask, dishPrior, explain, findEntry, listScenarios, loadApiResult, loadManifest, loadResult, saveScenario, similarDishes, simulate,
  type ApiLevers, type AskReply, type NewDish, type SavedScenario,
} from "../lib/data";
import { clock, dateLabel, dishWindow, fmt1, fmtInt, groupFlags, PRESET_LABELS, scenarioLabel, signed, stationName } from "../lib/format";
import { useApiUp, useAsync } from "../lib/hooks";
import { useApp, type Scenario } from "../lib/store";
import type { CookRow, Result, Verdict } from "../lib/types";

type KitchenEdit = { batch_size?: number; starting_batches?: number; reorder_point?: number; last_batch_min?: number };
type Draft = {
  preset: string | null;
  crowd: number; // multiplier, 1 = baseline
  servers: Record<string, number>; // station -> servers (only changed ones)
  kitchen: Record<string, KitchenEdit>;
  /** menu edits (API only) */
  remove?: string[];
  add?: NewDish[];
  popularity?: Record<string, number>;
  /** settings the plain-English box can set that have no control here */
  shift?: number;
  serviceMin?: Record<string, number>;
};
const EMPTY: Draft = { preset: null, crowd: 1, servers: {}, kitchen: {} };
const menuEdits = (d: Draft) => (d.remove?.length ?? 0) + (d.add?.length ?? 0) + Object.keys(d.popularity ?? {}).length;
const hasExtras = (d: Draft) => menuEdits(d) > 0 || !!d.shift || Object.keys(d.serviceMin ?? {}).length > 0;

/** Settings from POST /api/ask -> a draft the controls can show and edit. */
export function draftFromRequest(req: NonNullable<AskReply["request"]>): Draft {
  const lv: ApiLevers = req.levers ?? {};
  const servers: Record<string, number> = {}, serviceMin: Record<string, number> = {};
  for (const [sid, v] of Object.entries(lv.stations ?? {})) {
    if (v.servers != null) servers[sid] = v.servers;
    if (v.service_median_min != null) serviceMin[sid] = v.service_median_min;
  }
  return {
    preset: req.preset ?? null,
    crowd: lv.crowd_multiplier ?? 1,
    servers,
    kitchen: (lv.kitchen ?? {}) as Record<string, KitchenEdit>,
    remove: lv.remove_dishes ?? [],
    add: lv.add_dishes ?? [],
    popularity: lv.popularity ?? {},
    shift: lv.crowd_shift_min ?? 0,
    serviceMin,
  };
}

/** The bundle holds presets and one-lever-at-a-time variations around the
 * baseline. Returns the bundle lever string if this draft is one of those. */
export function bundleLever(d: Draft): string | null {
  const levers: string[] = [];
  if (Math.abs(d.crowd - 1) > 1e-9) levers.push(`crowd_multiplier=${+d.crowd.toFixed(2)}`);
  for (const [s, n] of Object.entries(d.servers)) levers.push(`stations.${s}.servers=${n}`);
  if (Object.keys(d.kitchen).length || hasExtras(d)) return null;
  if (levers.length === 0) return "";
  if (levers.length === 1 && d.preset === null) return levers[0];
  return null;
}

export function apiBody(date: string, d: Draft) {
  const stations: Record<string, { servers?: number; service_median_min?: number }> = {};
  for (const [s, n] of Object.entries(d.servers)) stations[s] = { servers: n };
  for (const [s, m] of Object.entries(d.serviceMin ?? {})) stations[s] = { ...stations[s], service_median_min: m };
  return {
    date,
    preset: d.preset,
    n_nights: 30,
    levers: {
      crowd_multiplier: Math.abs(d.crowd - 1) > 1e-9 ? d.crowd : null,
      crowd_shift_min: d.shift ? d.shift : null,
      stations,
      kitchen: d.kitchen,
      popularity: d.popularity ?? {},
      remove_dishes: d.remove ?? [],
      add_dishes: d.add ?? [],
    },
  };
}

type Run = { result: Result; base: Result; source: "bundle" | "api"; scenario: Scenario; hasTrace: boolean; draft: Draft | null };

export default function Lab() {
  const { date, scenario, setScenario, adopt, adopted } = useApp();
  const nav = useNavigate();
  const [draft, setDraft] = useState<Draft>(() => ({ ...EMPTY, preset: scenario.preset ?? "smaller_batches" }));
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [saved, setSaved] = useState<{ name: string; draft: Draft }[]>(() => {
    try { return JSON.parse(localStorage.getItem("dcwhatif.saved") ?? "[]"); } catch { return []; }
  });
  const [mode, setMode] = useState<"baseline" | "preset" | "saved">("preset");
  const apiOn = useApiUp();
  // scenarios saved on the API (any browser), for this date
  const remote = useAsync<SavedScenario[]>(async () => ((await apiUp()) ? listScenarios(date).catch(() => []) : []), [date, apiOn, note]);

  const base = useAsync(async () => {
    const m = await loadManifest();
    const e = findEntry(m, date, null, "");
    if (!e) throw new Error(`no baseline for ${date} in the bundle`);
    return loadResult(e);
  }, [date]);

  const doRun = async (d: Draft) => {
    setErr(null);
    setNote(null);
    if (!base.data) return;
    const lever = bundleLever(d);
    const m = await loadManifest();
    const e = lever !== null ? findEntry(m, date, d.preset, lever) : undefined;
    if (e) {
      setRun({ result: await loadResult(e), base: base.data, source: "bundle", scenario: { preset: d.preset, lever: lever! }, hasTrace: !!e.trace || (await apiUp()), draft: d });
      return;
    }
    if (!(await apiUp())) {
      setErr("This combination isn't in the offline bundle. Start the API (`make serve`) to simulate it; presets and single crowd/staffing changes work offline.");
      return;
    }
    setBusy("starting");
    try {
      const r = await simulate(apiBody(date, d), setBusy);
      setRun({ result: r, base: base.data, source: "api", scenario: { preset: d.preset, lever: "api", hash: r.config_hash, label: r.label }, hasTrace: true, draft: d });
    } catch (x) {
      setErr((x as Error).message);
    } finally {
      setBusy(null);
    }
  };

  /** Open a scenario saved on the API: its result is cached there, no draft to edit. */
  const openRemote = async (sv: SavedScenario) => {
    setErr(null);
    if (!base.data) return;
    try {
      const r = await loadApiResult(sv.config_hash);
      setRun({ result: r, base: base.data, source: "api", scenario: { preset: null, lever: "api", hash: r.config_hash, label: sv.name }, hasTrace: true, draft: null });
    } catch (x) {
      setErr(`Couldn't open "${sv.name}": ${(x as Error).message}`);
    }
  };

  // run the initial preset once the baseline is in
  useEffect(() => {
    if (base.data) doRun(draft);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base.data]);

  /** Adopting also registers the plan on the API when it's up, which logs its forecast (SPEC §5). */
  const onAdopt = async (r: Run) => {
    adopt(date, r.scenario);
    if (!(await apiUp())) { setNote("Adopted in this browser. Start the API to also record it in the forecast ledger."); return; }
    try {
      const s = await saveScenario(r.result.label, r.result.config_hash, "adopted");
      setNote(`Adopted and saved on the API${s.forecast ? ` (forecast ${s.forecast})` : ""}.`);
    } catch (x) {
      setNote(`Adopted in this browser. The API didn't record it: ${(x as Error).message}.`);
    }
  };

  const label = run ? run.result.label : scenarioLabel(draft.preset, bundleLever(draft) ?? "");
  return (
    <>
      <TopBar title="What-If Lab" sub="Test a decision on 30 simulated nights before trying it on real students"
        scenario={run?.scenario ?? { preset: draft.preset, lever: bundleLever(draft) ?? "custom" }} scenarioPrefix="Draft" />
      <div className="content">
        {base.error && <ErrorBox error={base.error} />}
        {base.loading && <Loading />}
        {base.data && (
          <div className="grid-lab">
            <Builder
              base={base.data} date={date} draft={draft} setDraft={setDraft} mode={mode} setMode={setMode} saved={saved}
              remote={remote.data ?? []} onOpenRemote={openRemote} apiUp={apiOn}
              busy={busy} onRun={() => doRun(draft)}
              onPreset={(p) => { const d = { ...EMPTY, preset: p }; setDraft(d); doRun(d); }}
              onLoadSaved={(d) => { setDraft(d); doRun(d); }}
              onApply={(d) => { setDraft(d); setMode(d.preset ? "preset" : "baseline"); doRun(d); }}
              onSave={async () => {
                const name = `${label} · ${dateLabel(date)}`;
                const next = [...saved.filter((s) => s.name !== name), { name, draft }];
                setSaved(next);
                try { localStorage.setItem("dcwhatif.saved", JSON.stringify(next)); } catch { /* ignore */ }
                if (run && run.draft === draft && (await apiUp())) {
                  await saveScenario(name, run.result.config_hash, "saved").then(
                    () => setNote(`Saved "${name}" in this browser and on the API.`),
                    () => setNote(`Saved "${name}" in this browser.`),
                  );
                } else setNote(`Saved "${name}" in this browser.`);
              }}
            />
            <div className="stack">
              {err && <div className="alert warn" role="alert"><b>Can't run this yet</b>{err}</div>}
              {note && <div className="alert info" role="status">{note}</div>}
              {!run && !err && <Loading what="results" />}
              {run && (
                <>
                  <ResultsCard run={run} date={date} adopted={adopted[date]} apiUp={apiOn}
                    onWatch={run.hasTrace ? () => { setScenario(run.scenario); nav("/hall"); } : undefined}
                    onAdopt={() => onAdopt(run)} />
                  {run.draft && menuEdits(run.draft) > 0 && <DemandShift run={run} />}
                  <div className="grid-2" style={{ gridTemplateColumns: "minmax(0,1fr) 260px" }}>
                    <CookSheet run={run} />
                    <SideEffects run={run} />
                  </div>
                  <PansEmpty run={run} />
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

function Builder({ base, date, draft, setDraft, mode, setMode, saved, remote, onOpenRemote, apiUp, busy, onRun, onPreset, onLoadSaved, onApply, onSave }: {
  base: Result; date: string; draft: Draft; setDraft: (d: Draft) => void; mode: string; setMode: (m: "baseline" | "preset" | "saved") => void;
  saved: { name: string; draft: Draft }[]; remote: SavedScenario[]; onOpenRemote: (s: SavedScenario) => void; apiUp: boolean; busy: string | null;
  onRun: () => void; onPreset: (p: string | null) => void; onLoadSaved: (d: Draft) => void; onApply: (d: Draft) => void; onSave: () => void;
}) {
  const [open, setOpen] = useState<string | null>("batch");
  const batchDishes = base.cook_sheet.filter((d) => d.plan).sort((a, b) => b.portions_to_prepare.p50 - a.portions_to_prepare.p50);
  const [dish, setDish] = useState(batchDishes[0]?.dish_id);
  const cur = batchDishes.find((d) => d.dish_id === dish);
  const edit = (dish && draft.kitchen[dish]) || {};
  const setK = (k: keyof KitchenEdit, v: number) => {
    const plan = cur!.plan!;
    const next = { ...edit, [k]: v };
    if (v === plan[k]) delete next[k];
    const kitchen = { ...draft.kitchen, [dish!]: next };
    if (!Object.keys(next).length) delete kitchen[dish!];
    setDraft({ ...draft, kitchen });
  };
  const offline = bundleLever(draft) !== null;
  const toggle = (id: string) => setOpen(open === id ? null : id);
  return (
    <section className="card" aria-label="Scenario builder">
      <h2>Scenario</h2>
      <AskBox date={date} apiUp={apiUp} onApply={onApply} />
      <div className="hint" style={{ marginBottom: 10 }}>Or start from tonight's baseline</div>
      <div className="seg" role="group" aria-label="Start from" style={{ marginBottom: 10 }}>
        <button aria-pressed={mode === "baseline"} onClick={() => { setMode("baseline"); onPreset(null); }}>Baseline</button>
        <button aria-pressed={mode === "preset"} onClick={() => setMode("preset")}>Preset</button>
        <button aria-pressed={mode === "saved"} onClick={() => setMode("saved")}>Saved</button>
      </div>
      {mode === "preset" && (
        <div className="row" style={{ marginBottom: 12 }}>
          {Object.entries(PRESET_LABELS).map(([id, l]) => (
            <button key={id} className="pill sel" aria-pressed={draft.preset === id} onClick={() => onPreset(id)}>{l}</button>
          ))}
        </div>
      )}
      {mode === "saved" && (
        <div className="stack" style={{ gap: 6, marginBottom: 12 }}>
          {saved.length === 0 && remote.length === 0 && <div className="hint">Nothing saved yet. Build a scenario and press Save.</div>}
          {saved.map((s) => <button key={s.name} className="pill wrap" onClick={() => onLoadSaved(s.draft)}>{s.name}</button>)}
          {remote.filter((r) => !saved.some((s) => s.name === r.name)).map((r) => (
            <button key={r.scenario_id} className="pill wrap" onClick={() => onOpenRemote(r)} title="Saved on the API">
              {r.name}{r.status === "adopted" ? " · adopted" : ""} <span className="faint">· API</span>
            </button>
          ))}
        </div>
      )}

      <Accordion id="menu" open={open === "menu"} onToggle={() => toggle("menu")} title="Menu"
        meta={menuEdits(draft) ? `${menuEdits(draft)} change${menuEdits(draft) === 1 ? "" : "s"}` : `${base.cook_sheet.length} dishes`}>
        <MenuEditor base={base} date={date} draft={draft} setDraft={setDraft} apiUp={apiUp} />
      </Accordion>

      <Accordion id="batch" open={open === "batch"} onToggle={() => toggle("batch")} title="Batch plan" meta={Object.keys(draft.kitchen).length ? `${Object.keys(draft.kitchen).length} dishes changed` : "baseline"}>
        <select className="btn" style={{ width: "100%", marginBottom: 10 }} value={dish} onChange={(e) => setDish(e.target.value)} aria-label="Dish">
          {batchDishes.map((d) => <option key={d.dish_id} value={d.dish_id}>{d.name}{d.period === "late_night" ? " (late night)" : ""}{draft.kitchen[d.dish_id] ? " •" : ""}</option>)}
        </select>
        {cur?.plan && (
          <>
            <Slider label="Batch size" unit="portions" min={5} max={Math.max(80, cur.plan.batch_size * 2)} step={5} base={cur.plan.batch_size} value={edit.batch_size ?? cur.plan.batch_size} onChange={(v) => setK("batch_size", v)} />
            <Slider label="Starting batches" min={0} max={6} step={1} base={cur.plan.starting_batches} value={edit.starting_batches ?? cur.plan.starting_batches} onChange={(v) => setK("starting_batches", v)} />
            <Slider label="Reorder point" unit="portions" min={0} max={Math.max(100, cur.plan.reorder_point * 2)} step={5} base={cur.plan.reorder_point} value={edit.reorder_point ?? cur.plan.reorder_point} onChange={(v) => setK("reorder_point", v)} />
            <Slider label="Last batch" min={dishWindow(base, cur.period)[0] + 30} max={dishWindow(base, cur.period)[1]} step={10} base={cur.plan.last_batch_min} value={edit.last_batch_min ?? cur.plan.last_batch_min} onChange={(v) => setK("last_batch_min", v)} fmt={(v) => clock(v)} />
          </>
        )}
      </Accordion>

      <Accordion id="crowd" open={open === "crowd"} onToggle={() => toggle("crowd")} title="Crowd" meta={`${fmtInt(base.headline.students.p50 * draft.crowd)} expected`}>
        <Slider label="Crowd multiplier" min={0.75} max={1.25} step={0.05} base={1} value={draft.crowd} onChange={(v) => setDraft({ ...draft, crowd: +v.toFixed(2) })} fmt={(v) => `×${v.toFixed(2)}`} />
      </Accordion>

      <Accordion id="staff" open={open === "staff"} onToggle={() => toggle("staff")} title="Stations & staffing" meta={Object.keys(draft.servers).length ? Object.entries(draft.servers).map(([s, n]) => `${stationName(s)}: ${n}`).join(", ") : "baseline"}>
        {base.stations.filter((s) => !s.self_serve && !["display", "soups", "breads", "desserts"].includes(s.id)).map((s) => {
          const v = draft.servers[s.id] ?? s.servers;
          const set = (n: number) => {
            const servers = { ...draft.servers, [s.id]: n };
            if (n === s.servers) delete servers[s.id];
            setDraft({ ...draft, servers });
          };
          return (
            <div key={s.id} className="row" style={{ flexWrap: "nowrap", padding: "3px 0" }}>
              <span style={{ flex: 1 }}>{s.name}</span>
              <button className="btn" style={{ width: 28, padding: 0, justifyContent: "center" }} onClick={() => set(Math.max(1, v - 1))} aria-label={`fewer servers at ${s.name}`}>−</button>
              <span className="num" style={{ width: 20, textAlign: "center", fontWeight: v !== s.servers ? 600 : 400 }}>{v}</span>
              <button className="btn" style={{ width: 28, padding: 0, justifyContent: "center" }} onClick={() => set(v + 1)} aria-label={`more servers at ${s.name}`}>+</button>
            </div>
          );
        })}
      </Accordion>

      {(draft.shift || Object.keys(draft.serviceMin ?? {}).length > 0) && (
        <div className="hint" style={{ margin: "4px 0" }}>
          Also set by the question box:{draft.shift ? ` arrivals ${Math.abs(draft.shift)} min ${draft.shift < 0 ? "earlier" : "later"};` : ""}
          {Object.entries(draft.serviceMin ?? {}).map(([sid, m]) => ` ${stationName(sid)} ${m} min per item;`)}
          {" "}<button className="link" onClick={() => setDraft({ ...draft, shift: 0, serviceMin: {} })}>clear</button>
        </div>
      )}
      <div className="hint" style={{ margin: "4px 0 10px" }}>
        {offline ? "Cached: results load instantly, offline." : apiUp ? "Not cached: runs 30 nights on the API (a few seconds)." : "Not cached offline. Start the API (`make serve`) to run this combination."}
      </div>
      <div className="row">
        <button className="btn primary" onClick={onRun} disabled={!!busy || (!offline && !apiUp)}>{busy ? `Running… ${busy}` : "Run 30 nights"}</button>
        <button className="btn" disabled title="Optimizer is a stretch goal (SPEC §6.1)">Optimize</button>
        <button className="btn ghost" onClick={onSave}>Save</button>
      </div>
    </section>
  );
}

/** Plain English -> settings (POST /api/ask). Nothing runs until the manager applies them. */
function AskBox({ date, apiUp, onApply }: { date: string; apiUp: boolean; onApply: (d: Draft) => void }) {
  const [q, setQ] = useState("");
  const [reply, setReply] = useState<AskReply | null>(null);
  const [state, setState] = useState<"idle" | "busy" | { error: string }>("idle");
  const go = () => {
    if (q.trim().length < 3) return;
    setState("busy");
    setReply(null);
    ask(q.trim(), date).then((r) => { setReply(r); setState("idle"); }, (e) => setState({ error: (e as Error).message }));
  };
  return (
    <div style={{ margin: "8px 0 12px" }}>
      <label htmlFor="ask" className="hint" style={{ display: "block", marginBottom: 4 }}>Ask in plain English</label>
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <input id="ask" className="btn" style={{ flex: 1, minWidth: 0, textAlign: "left" }} value={q} disabled={!apiUp}
          placeholder={apiUp ? "e.g. one more server at Grill and 10% busier" : "Needs the API (make serve)"}
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && go()} />
        <button className="btn" onClick={go} disabled={!apiUp || state === "busy" || q.trim().length < 3}>{state === "busy" ? "Reading…" : "Ask"}</button>
      </div>
      {typeof state === "object" && <div className="hint" role="alert" style={{ marginTop: 6 }}>Couldn't read that: {state.error}</div>}
      {reply && (
        <div className="alert info" style={{ marginTop: 8 }} role="status">
          {reply.ok ? (
            <>
              <b>{reply.settings?.length ? "Settings" : "No settings changed"}</b>
              {reply.settings?.length ? <ul style={{ margin: "2px 0 4px", paddingLeft: 18 }}>{reply.settings.map((x) => <li key={x}>{x}</li>)}</ul> : null}
              {reply.explanation && <div className="hint">{reply.explanation}</div>}
            </>
          ) : <><b>Couldn't turn that into settings</b>{reply.error}</>}
          {reply.unsupported.length > 0 && <div className="hint">Not supported: {reply.unsupported.join("; ")}</div>}
          {reply.source !== "llm" && reply.source !== "cache" && <div className="faint" style={{ fontSize: 11 }}>Keyword matching (no AI key set)</div>}
          {reply.ok && reply.request && (reply.settings?.length ?? 0) > 0 && (
            <div className="row" style={{ marginTop: 6 }}>
              <button className="btn primary" onClick={() => onApply(draftFromRequest(reply.request!))}>Apply and run</button>
              <button className="btn ghost" onClick={() => setReply(null)}>Dismiss</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const ROLES = ["entree", "starch", "side", "soup", "dessert", "bread"];
const DIETS = ["vegetarian", "vegan", "halal"];

/** Remove dishes, change popularity, add a new dish (with AI estimate). All need the API to simulate. */
function MenuEditor({ base, date, draft, setDraft, apiUp }: { base: Result; date: string; draft: Draft; setDraft: (d: Draft) => void; apiUp: boolean }) {
  const dishes = useMemo(() => [...base.cook_sheet].sort((a, b) => a.station.localeCompare(b.station) || a.name.localeCompare(b.name)), [base]);
  const name = (id: string) => base.cook_sheet.find((d) => d.dish_id === id)?.name ?? id;
  const removed = draft.remove ?? [], pop = draft.popularity ?? {}, added = draft.add ?? [];
  const [pick, setPick] = useState("");
  const [popDish, setPopDish] = useState("");
  const [wizard, setWizard] = useState(false);
  const opts = (ex: string[]) => dishes.filter((d) => !ex.includes(d.dish_id)).map((d) => (
    <option key={d.dish_id} value={d.dish_id}>{stationName(d.station)} · {d.name}</option>
  ));
  const setPop = (id: string, v: number) => {
    const next = { ...pop, [id]: v };
    if (Math.abs(v - 1) < 1e-9) delete next[id];
    setDraft({ ...draft, popularity: next });
  };
  return (
    <div className="stack" style={{ gap: 10 }}>
      {!apiUp && <div className="hint">Menu changes are simulated on the API. Start it with <code>make serve</code>.</div>}
      <div>
        <div className="hint" style={{ marginBottom: 4 }}>Remove a dish</div>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <select className="btn" style={{ flex: 1, minWidth: 0 }} value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Dish to remove">
            <option value="">Choose…</option>{opts(removed)}
          </select>
          <button className="btn" disabled={!pick} onClick={() => { setDraft({ ...draft, remove: [...removed, pick] }); setPick(""); }}>Remove</button>
        </div>
        <div className="row" style={{ marginTop: 6 }}>
          {removed.map((id) => (
            <span key={id} className="chip bad">− {name(id)}
              <button className="link" aria-label={`keep ${name(id)}`} style={{ marginLeft: 4 }} onClick={() => setDraft({ ...draft, remove: removed.filter((x) => x !== id) })}>×</button>
            </span>
          ))}
        </div>
      </div>
      <div>
        <div className="hint" style={{ marginBottom: 4 }}>Change how popular a dish is</div>
        <select className="btn" style={{ width: "100%" }} value={popDish} onChange={(e) => setPopDish(e.target.value)} aria-label="Dish popularity">
          <option value="">Choose…</option>{opts(removed)}
        </select>
        {popDish && <Slider label={name(popDish)} min={0.3} max={2} step={0.1} base={1} value={pop[popDish] ?? 1} onChange={(v) => setPop(popDish, +v.toFixed(1))} fmt={(v) => `×${v.toFixed(1)}`} />}
        <div className="row" style={{ marginTop: 6 }}>
          {Object.entries(pop).map(([id, v]) => (
            <span key={id} className="chip">{name(id)} ×{v.toFixed(1)}
              <button className="link" aria-label={`reset ${name(id)}`} style={{ marginLeft: 4 }} onClick={() => setPop(id, 1)}>×</button>
            </span>
          ))}
        </div>
      </div>
      <div>
        <div className="row">
          <span className="hint" style={{ flex: 1 }}>New dishes</span>
          {!wizard && <button className="btn" onClick={() => setWizard(true)}>Add a new dish…</button>}
        </div>
        <div className="row" style={{ marginTop: 6 }}>
          {added.map((d) => (
            <span key={d.name} className="chip good" title={`like ${d.similar_to.map(name).join(", ")}`}>+ {d.name}{d.affinity && Object.keys(d.affinity).length ? " · AI estimate" : ""}
              <button className="link" aria-label={`drop ${d.name}`} style={{ marginLeft: 4 }} onClick={() => setDraft({ ...draft, add: added.filter((x) => x.name !== d.name) })}>×</button>
            </span>
          ))}
        </div>
        {wizard && (
          <NewDishForm base={base} date={date} apiUp={apiUp} dishes={dishes} taken={[...added.map((d) => d.name.toLowerCase()), ...base.cook_sheet.map((d) => d.name.toLowerCase())]}
            onCancel={() => setWizard(false)}
            onAdd={(d) => { setDraft({ ...draft, add: [...added, d] }); setWizard(false); }} />
        )}
      </div>
    </div>
  );
}

function NewDishForm({ base, date, apiUp, dishes, taken, onAdd, onCancel }: {
  base: Result; date: string; apiUp: boolean; dishes: CookRow[]; taken: string[]; onAdd: (d: NewDish) => void; onCancel: () => void;
}) {
  const stations = base.stations.filter((s) => base.cook_sheet.some((d) => d.station === s.id));
  const [d, setD] = useState<NewDish>({ name: "", station: stations[0]?.id ?? "", role: "entree", diet_tags: [], hot: true, similar_to: [], popularity_multiplier: 1 });
  const [prior, setPrior] = useState<Awaited<ReturnType<typeof dishPrior>> | null>(null);
  const [state, setState] = useState<string | null>(null);
  const set = (p: Partial<NewDish>) => { setD({ ...d, ...p }); setPrior(null); };
  const dupe = taken.includes(d.name.trim().toLowerCase());
  const suggest = () => {
    setState("Finding similar dishes…");
    similarDishes(d.name.trim(), date, d.station, d.role).then(
      (r) => { set({ similar_to: r.similar_to.slice(0, 3) }); setState(r.source === "embedding" ? null : "Matched by words (no AI embeddings)."); },
      (e) => setState(`Couldn't suggest: ${(e as Error).message}`),
    );
  };
  const estimate = () => {
    setState("Asking for an estimate…");
    dishPrior(date, { ...d, name: d.name.trim() }).then((r) => { setPrior(r); setState(null); }, (e) => setState(`Couldn't estimate: ${(e as Error).message}`));
  };
  const ready = d.name.trim().length > 0 && !dupe && d.similar_to.length > 0;
  const reasons = prior ? Object.entries(prior.reasons).sort((a, b) => (prior.affinity[b[0]] ?? 1) - (prior.affinity[a[0]] ?? 1)) : [];
  return (
    <div className="card" style={{ boxShadow: "none", marginTop: 8, padding: 12 }} aria-label="New dish">
      <div className="stack" style={{ gap: 8 }}>
        <label className="hint">Name
          <input className="btn" style={{ width: "100%", textAlign: "left" }} value={d.name} onChange={(e) => set({ name: e.target.value })} placeholder="Chicken Tikka Masala" />
        </label>
        {dupe && <div className="hint" role="alert">That dish is already on the menu.</div>}
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <label className="hint" style={{ flex: 1 }}>Station
            <select className="btn" style={{ width: "100%" }} value={d.station} onChange={(e) => set({ station: e.target.value })}>
              {stations.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <label className="hint" style={{ flex: 1 }}>Kind
            <select className="btn" style={{ width: "100%" }} value={d.role} onChange={(e) => set({ role: e.target.value })}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
        </div>
        <div className="row" style={{ fontSize: 12 }}>
          {DIETS.map((t) => (
            <label key={t} className="row" style={{ gap: 4 }}>
              <input type="checkbox" checked={d.diet_tags.includes(t)} onChange={(e) => set({ diet_tags: e.target.checked ? [...d.diet_tags, t] : d.diet_tags.filter((x) => x !== t) })} />{t}
            </label>
          ))}
          <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={d.hot !== false} onChange={(e) => set({ hot: e.target.checked })} />hot</label>
        </div>
        <div>
          <div className="row"><span className="hint" style={{ flex: 1 }}>Similar to (borrows their popularity, 1–3)</span>
            <button className="link" disabled={!apiUp || !d.name.trim()} onClick={suggest}>suggest</button></div>
          <select className="btn" style={{ width: "100%", marginTop: 4 }} value="" aria-label="Similar dish"
            onChange={(e) => e.target.value && d.similar_to.length < 3 && set({ similar_to: [...d.similar_to, e.target.value] })}>
            <option value="">{d.similar_to.length >= 3 ? "Three chosen" : "Add a similar dish…"}</option>
            {dishes.filter((x) => !d.similar_to.includes(x.dish_id)).map((x) => <option key={x.dish_id} value={x.dish_id}>{stationName(x.station)} · {x.name}</option>)}
          </select>
          <div className="row" style={{ marginTop: 6 }}>
            {d.similar_to.map((id) => (
              <span key={id} className="chip">{dishes.find((x) => x.dish_id === id)?.name ?? id}
                <button className="link" style={{ marginLeft: 4 }} aria-label="remove similar dish" onClick={() => set({ similar_to: d.similar_to.filter((x) => x !== id) })}>×</button>
              </span>
            ))}
          </div>
        </div>
        <Slider label="Popularity vs. those dishes" min={0.5} max={2} step={0.1} base={1} value={d.popularity_multiplier ?? 1}
          onChange={(v) => set({ popularity_multiplier: +v.toFixed(1) })} fmt={(v) => `×${v.toFixed(1)}`} />
        <div className="row">
          <button className="btn" disabled={!apiUp || !ready} onClick={estimate} title="Each student type's taste for the new dish, estimated by AI">AI estimate</button>
          {prior && <span className="chip info">{prior.label}</span>}
        </div>
        {prior && (
          <div className="hint">
            Likes it most: {reasons.slice(0, 2).map(([id, r]) => `${id.replace(/_/g, " ")} (×${prior.affinity[id]}, ${r})`).join("; ")}.
            {reasons.length > 2 && <> Least: {reasons.slice(-1).map(([id, r]) => `${id.replace(/_/g, " ")} (×${prior.affinity[id]}, ${r})`)}.</>}
            {" "}Popularity range {prior.popularity.range[0]}–{prior.popularity.range[1]}, borrowed from {prior.popularity.borrowed_from.join(", ")}.
          </div>
        )}
        {state && <div className="hint" role="status">{state}</div>}
        <div className="row">
          <button className="btn primary" disabled={!ready} onClick={() => onAdd({ ...d, name: d.name.trim(), affinity: prior?.affinity ?? {} })}>Add to scenario</button>
          <button className="btn ghost" onClick={onCancel}>Cancel</button>
        </div>
        {!prior && ready && <div className="faint" style={{ fontSize: 11 }}>Without an AI estimate, each student type likes it as much as the similar dishes.</div>}
      </div>
    </div>
  );
}

/** Where demand moves when the menu changes: served portions per dish vs. the baseline (median of 30 nights). */
function DemandShift({ run }: { run: Run }) {
  const baseBy = new Map(run.base.cook_sheet.map((d) => [d.dish_id, d]));
  const now = new Map(run.result.cook_sheet.map((d) => [d.dish_id, d]));
  const ids = new Set([...baseBy.keys(), ...now.keys()]);
  const rows = [...ids].map((id) => {
    const a = baseBy.get(id)?.served.p50 ?? 0, b = now.get(id)?.served.p50 ?? 0;
    return { id, name: now.get(id)?.name ?? baseBy.get(id)?.name ?? id, diff: b - a, isNew: !baseBy.has(id), gone: !now.has(id) };
  }).filter((r) => Math.abs(r.diff) >= 3).sort((a, b) => b.diff - a.diff);
  const top = [...rows.filter((r) => r.diff > 0).slice(0, 5), ...rows.filter((r) => r.diff < 0).slice(-5)];
  const max = Math.max(1, ...top.map((r) => Math.abs(r.diff)));
  return (
    <section className="card">
      <div className="card-head">
        <div><h2>Where demand moves</h2><div className="hint">Portions served per night vs. baseline (median) · biggest gains and losses</div></div>
      </div>
      {top.length === 0 && <div className="hint">No dish moves by 3 or more portions.</div>}
      {top.map((r) => (
        <div key={r.id} style={{ display: "grid", gridTemplateColumns: "200px 1fr 60px", alignItems: "center", gap: 10, padding: "3px 0", fontSize: 12 }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.name}>
            {r.name}{r.isNew ? <span className="chip good" style={{ marginLeft: 4 }}>new</span> : r.gone ? <span className="chip bad" style={{ marginLeft: 4 }}>removed</span> : null}
          </span>
          <div style={{ position: "relative", height: 10 }}>
            <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: 1, background: "var(--border-strong)" }} />
            <div style={{ position: "absolute", top: 1, height: 8, borderRadius: 2, background: r.diff > 0 ? "var(--good)" : "var(--bad)",
              left: r.diff > 0 ? "50%" : `${50 - (Math.abs(r.diff) / max) * 50}%`, width: `${(Math.abs(r.diff) / max) * 50}%` }} />
          </div>
          <span className="num" style={{ textAlign: "right" }}>{signed(Math.round(r.diff))}</span>
        </div>
      ))}
    </section>
  );
}

// Top-level so sliders inside keep their identity (and focus) across renders.
function Accordion({ title, meta, open, onToggle, children }: { id: string; title: string; meta: string; open: boolean; onToggle: () => void; children: React.ReactNode }) {
  return (
    <div style={{ borderTop: "1px solid var(--border)", padding: "12px 0" }}>
      <button onClick={onToggle} aria-expanded={open}
        style={{ all: "unset", cursor: "pointer", display: "flex", width: "100%", alignItems: "center", gap: 8 }}>
        <h3 style={{ flex: 1 }}>{title}</h3><span className="hint">{meta}</span><span aria-hidden>{open ? "−" : "+"}</span>
      </button>
      {open && <div style={{ marginTop: 10 }}>{children}</div>}
    </div>
  );
}

function Slider({ label, unit, min, max, step, base, value, onChange, fmt }: {
  label: string; unit?: string; min: number; max: number; step: number; base: number; value: number; onChange: (v: number) => void; fmt?: (v: number) => string;
}) {
  const f = fmt ?? ((v: number) => `${v}${unit ? ` ${unit}` : ""}`);
  return (
    <label style={{ display: "block", marginBottom: 10 }}>
      <div className="row" style={{ fontSize: 13 }}>
        <span style={{ flex: 1 }}>{label}</span>
        <b style={{ fontWeight: 500 }}>{f(value)}</b>
        {value !== base && <span className="faint">(was {f(base)})</span>}
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(+e.target.value)} style={{ width: "100%", accentColor: "var(--maroon)" }} />
    </label>
  );
}

const verdictChip = (v: Verdict | undefined, wr: number | undefined, n: number) =>
  v === "better" ? <span className="chip good">better · {Math.round((wr ?? 0) * n)}/{n}</span>
    : v === "worse" ? <span className="chip bad">worse · {Math.round((1 - (wr ?? 0)) * n)}/{n}</span>
      : v === "same" ? <span className="chip" title="The difference is smaller than what matters for this metric">no real difference</span>
        : <span className="chip">inconclusive</span>;

function ResultsCard({ run, date, adopted, apiUp, onWatch, onAdopt }: { run: Run; date: string; adopted?: Scenario; apiUp: boolean; onWatch?: () => void; onAdopt?: () => void }) {
  const r = run.result, b = run.base, vs = r.vs_baseline;
  const [why, setWhy] = useState<{ text: string; source: string } | "busy" | { error: string } | null>(null);
  useEffect(() => setWhy(null), [r.config_hash]);
  const isBase = r.config_hash === b.config_hash;
  const n = vs?.n_pairs ?? r.n_nights;
  const metrics = [
    ["waste_total", "Portions wasted", (x: number) => signed(x), fmtInt],
    ["stockout_min_total", "Minutes out of stock", (x: number) => signed(x, fmt1), fmt1],
    ["missed_first_choice_rate", "Missed first choice", (x: number) => `${signed(x * 100, fmt1)} pt`, (x: number) => `${fmt1(x * 100)}%`],
    ["wait_p90", "Peak wait (p90)", (x: number) => `${signed(x, fmt1)} min`, (x: number) => `${fmt1(x)} min`],
  ] as const;
  const isAdopted = !!adopted && (run.scenario.hash
    ? adopted.hash === run.scenario.hash
    : !adopted.hash && adopted.preset === run.scenario.preset && adopted.lever === run.scenario.lever);
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Results vs. baseline</h2>
          <div className="hint">{n} paired nights · same students, same arrival times · {run.source === "bundle" ? "from the offline bundle" : "simulated on the API"}{r.provisional ? " · provisional" : ""}</div>
        </div>
        <div className="row">
          {!isBase && apiUp && (
            <button className="btn ghost" disabled={why === "busy"} title="Why it won or lost, from the 30 paired nights"
              onClick={() => { setWhy("busy"); explain(r.config_hash, b.config_hash).then((x) => setWhy(x), (e) => setWhy({ error: (e as Error).message })); }}>
              {why === "busy" ? "Explaining…" : "Why?"}
            </button>
          )}
          <button className="btn" onClick={onWatch} disabled={!onWatch} title={onWatch ? "" : "No replay in the offline bundle for this scenario. Start the API (make serve) to replay it."}>Watch replay</button>
          <button className="btn primary" onClick={onAdopt} disabled={!onAdopt || !!isAdopted}>{isAdopted ? "Adopted ✓" : `Adopt for ${dateLabel(date, { weekday: "short", month: "short", day: "numeric" })}`}</button>
        </div>
      </div>
      <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}>
        {metrics.map(([k, l, fd, fv]) => {
          const m = vs?.metrics[k];
          const diff = m?.diff.p50 ?? r.headline[k].p50 - b.headline[k].p50;
          return (
            <div key={k} className="card kpi" style={{ boxShadow: "none" }}>
              <div className="label">{l}</div>
              <div className="value" style={{ fontSize: 26 }}>{isBase ? fv(r.headline[k].p50) : fd(diff)}</div>
              <div className="range">{fv(r.headline[k].p50)} vs {fv(b.headline[k].p50)}{m ? ` · ${fd(m.diff.p10)} to ${fd(m.diff.p90)}` : ""}</div>
              {isBase ? <span className="chip">baseline</span> : verdictChip(m?.verdict, m?.win_rate, n)}
            </div>
          );
        })}
      </div>
      {why && why !== "busy" && (
        <div className={"text" in why ? "narration" : "hint"} style={{ marginTop: 10 }} role="status">
          {"text" in why ? <>
            <div className="overline" style={{ fontStyle: "normal", color: "var(--maroon)", marginBottom: 4 }}>Why · {why.source === "template" ? "written from the numbers (no AI)" : "AI, checked against the numbers"}</div>
            {why.text}
          </> : <>Couldn't explain: {why.error}</>}
        </div>
      )}
    </section>
  );
}

const planKey = (d: CookRow) => (d.plan ? `${d.plan.starting_batches}x${d.plan.batch_size}/${d.plan.reorder_point}/${d.plan.last_batch_min}` : "");

function CookSheet({ run }: { run: Run }) {
  const baseBy = new Map(run.base.cook_sheet.map((d) => [d.dish_id, d]));
  const rows = run.result.cook_sheet.filter((d) => d.plan)
    .map((d) => ({ d, changed: planKey(d) !== planKey(baseBy.get(d.dish_id) ?? d) || !baseBy.has(d.dish_id) }))
    .sort((a, b) => Number(b.changed) - Number(a.changed) || b.d.portions_to_prepare.p50 - a.d.portions_to_prepare.p50);
  const [all, setAll] = useState(false);
  const nChanged = rows.filter((r) => r.changed).length;
  return (
    <section className="card">
      <div className="card-head">
        <div><h2>Cook sheet</h2><div className="hint">How much to make and when · {nChanged} changed row{nChanged === 1 ? "" : "s"} highlighted</div></div>
      </div>
      <div className="scroll-x">
        <table className="t">
          <thead><tr><th>Dish</th><th className="n">Prepare</th><th className="n">Start</th><th className="n">Reorder</th><th className="n">Last</th></tr></thead>
          <tbody>
            {rows.slice(0, all ? rows.length : Math.max(8, nChanged)).map(({ d, changed }) => (
              <tr key={d.dish_id} className={changed ? "changed" : ""}>
                <td title={d.name} style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.name}</td>
                <td className="n">{Math.round(d.portions_to_prepare.p50)}</td>
                <td className="n">{d.plan!.starting_batches}×{d.plan!.batch_size}</td>
                <td className="n">{d.plan!.reorder_point}</td>
                <td className="n">{d.plan!.last_batch_clock.replace(" pm", "")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > 8 && <button className="btn ghost" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${rows.length}`}</button>}
    </section>
  );
}

function SideEffects({ run }: { run: Run }) {
  // same kind of alert about the same dish / station / diet counts as "already there", even if its times moved
  const fkey = (f: (typeof run.base.flags)[number]) => `${f.kind}|${f.dish_id ?? f.station ?? (f as { diet?: string }).diet ?? f.text}`;
  const baseKeys = new Set(run.base.flags.map(fkey));
  const flags = groupFlags(run.result.flags);
  const cls = (s: string) => (s === "bad" ? "bad" : s === "warn" ? "warn" : s === "good" ? "good" : "muted");
  return (
    <section className="card">
      <h2>Side effects</h2>
      <div className="hint" style={{ marginBottom: 10 }}>Checked automatically · new vs. baseline marked</div>
      <div className="stack" style={{ gap: 8 }}>
        {flags.length === 0 && <div className="alert good"><b>Nothing flagged</b></div>}
        {flags.slice(0, 6).map((f, i) => (
          <div key={i} className={`alert ${cls(f.severity)}`}>
            <b>{f.kind.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())}{f.kind === "long_line" ? !run.base.flags.some((x) => x.kind === "long_line") && <span className="chip maroon" style={{ marginLeft: 6 }}>new</span>
              : !baseKeys.has(fkey(f)) && <span className="chip maroon" style={{ marginLeft: 6 }}>new</span>}</b>
            {f.text}
          </div>
        ))}
      </div>
    </section>
  );
}

function PansEmpty({ run }: { run: Run }) {
  const r = run.result, b = run.base;
  const bBy = new Map(b.cook_sheet.map((d) => [d.dish_id, d]));
  const rows = useMemo(() => r.cook_sheet
    .filter((d) => (d.runout_window && d.runout_share >= 0.3) || ((bBy.get(d.dish_id)?.runout_share ?? 0) >= 0.3))
    .sort((a, b2) => (a.runout_window?.p50_min ?? 9e9) - (b2.runout_window?.p50_min ?? 9e9)).slice(0, 10),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  [r, b]);
  const t0 = r.open, t1 = r.close;
  const x = (t: number) => `${((t - t0) / (t1 - t0)) * 100}%`;
  const bar = (d: CookRow | undefined, color: string, top: number) => {
    if (!d?.runout_window || d.runout_share < 0.3) return null;
    const p50 = d.runout_window.p50_min;
    return <div title={`${d.runout_window.from}–${d.runout_window.to}, ${Math.round(d.runout_share * 100)}% of nights`}
      style={{ position: "absolute", left: x(p50), width: `max(4px, calc(${x(t1)} - ${x(p50)}))`, top, height: 6, borderRadius: 3, background: color, opacity: 0.4 + 0.6 * d.runout_share }} />;
  };
  return (
    <section className="card">
      <div className="card-head">
        <div><h2>When pans are empty</h2><div className="hint">Median run-out time to close · grey = baseline, maroon = this scenario · darker = more nights</div></div>
        <span className="chip">{clock(t0, false)}–{clock(t1)}</span>
      </div>
      {rows.length === 0 && <div className="hint">No dish runs out in 30%+ of nights, in either plan.</div>}
      {rows.map((d) => (
        <div key={d.dish_id} style={{ display: "grid", gridTemplateColumns: "180px 1fr", alignItems: "center", gap: 10, padding: "4px 0", fontSize: 12 }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={d.name}>{d.name}</span>
          <div style={{ position: "relative", height: 18, background: "var(--bg-muted)", borderRadius: 4 }}>
            {bar(bBy.get(d.dish_id), "var(--text-3)", 2)}
            {bar(d, "var(--maroon)", 10)}
          </div>
        </div>
      ))}
      <div style={{ display: "grid", gridTemplateColumns: "180px 1fr", gap: 10 }}>
        <span />
        <div className="row hint" style={{ justifyContent: "space-between" }}>
          {Array.from({ length: Math.floor((t1 - t0) / 60) + 1 }, (_, i) => <span key={i}>{clock(t0 + i * 60, false)}</span>)}
        </div>
      </div>
    </section>
  );
}
