// Sam the Minuteman, the planning agent (engine `ai/agent.py`, POST /api/agent).
// A run streams its steps over Server-Sent Events; `agentReducer` folds them into one state.

import { api } from "./data";
import type { AskReply } from "./data";

export type Check = { name: string; ok: boolean; text: string };
export type GoalCheck = {
  met: boolean; checks: Check[]; waste_pct: number;
  protected_worse: { dish_id: string; name: string; station: string; runout_share: [number, number]; empty_min_p50: [number, number] }[];
};
export type Headline = { baseline: number; now: number; median_change?: number; verdict?: string };
export type AgentTry = {
  id: number; note: string; moves: string[]; settings: string[]; config_hash: string; check: GoalCheck;
  waste: Headline; missed_meal: Headline; wait_p90: Headline; request: NonNullable<AskReply["request"]>;
};
export type Goal = {
  kind?: "plan" | "question";
  waste_cut_pct: number; protect_diets: string[]; protect_dishes: string[]; keep_missed_meals: boolean; keep_wait: boolean;
  summary: string; unsupported: string[];
};
export type PlanRow = {
  dish_id: string; name: string; station: string; period: string; protected: boolean; diet_tags: string[];
  before: Record<"batch_size" | "starting_batches" | "reorder_point" | "max_batches", number>;
  after: Record<"batch_size" | "starting_batches" | "reorder_point" | "max_batches", number>;
  last_batch: string; waste: [number, number]; runout_share: [number, number];
};
export type TigerLookup = {
  n?: number; why: string; sql: string | null; row_count?: number | null; columns?: string[]; rows?: (string | null)[][];
  truncated?: boolean; error?: string;
};
export type AgentFinal = {
  kind?: "plan" | "question"; answer?: string; tiger_lookups?: TigerLookup[]; history_id?: string; created_at?: string;
  tries_run?: number; headline?: string;
  agent: string; goal_text: string; goal: Goal; date: string; chosen: number; chosen_by: "agent" | "rules" | "goal check";
  reasoning: string; caveats: string; check: GoalCheck; holdout: GoalCheck & { seeds: [number, number] };
  request: NonNullable<AskReply["request"]>; config_hash: string; baseline_hash: string; label: string;
  moves: string[]; settings: string[]; changed_dishes: PlanRow[]; tries: AgentTry[]; notices: string[];
  source: "llm" | "cache" | "mock" | "mixed"; model: string | null; nights_simulated: number;
};

export type AgentState = {
  status: "idle" | "starting" | "running" | "done" | "error";
  goalText?: string;
  model?: string | null;
  provider?: string;
  maxTries?: number;
  nNights?: number;
  goal?: Goal;
  goalSource?: string;
  protectedDishes?: { dish_id: string; name: string }[];
  baseline?: { waste_total: number; missed_meal: number; wait_p90: number };
  /** tries in order; the one being simulated has only id/note/moves */
  tries: (Partial<AgentTry> & { id: number; note: string; moves: string[] })[];
  progress?: { what: string; done: number; total: number };
  notices: string[];
  thoughts: string[];
  tiger: TigerLookup[];
  validating?: { try_id: number; seeds: [number, number] };
  final?: AgentFinal;
  error?: string;
};

export const AGENT_IDLE: AgentState = { status: "idle", tries: [], notices: [], thoughts: [], tiger: [] };

export type AgentEvent = { event: string; data: any }; // eslint-disable-line @typescript-eslint/no-explicit-any

export function agentReducer(s: AgentState, e: AgentEvent): AgentState {
  const d = e.data;
  switch (e.event) {
    case "start": // a reconnecting stream replays from the start: begin again
      return { ...AGENT_IDLE, status: "running", goalText: d.goal_text, model: d.model, provider: d.provider, maxTries: d.max_tries, nNights: d.n_nights };
    case "goal":
      return { ...s, goal: d.goal, goalSource: d.source, protectedDishes: d.protected, baseline: d.baseline };
    case "progress":
      return { ...s, progress: d };
    case "try_start":
      return { ...s, progress: undefined, tries: [...s.tries.filter((t) => t.id !== d.id), { id: d.id, note: d.note, moves: d.moves }] };
    case "try":
      return { ...s, progress: undefined, tries: s.tries.map((t) => (t.id === d.id ? { ...t, ...d } : t)) };
    case "notice":
      return { ...s, notices: [...s.notices, d.text] };
    case "tiger":
      return { ...s, tiger: [...s.tiger.filter((x) => x.n !== d.n), d] };
    case "thought":
      return { ...s, thoughts: [...s.thoughts, d.text] };
    case "validate_start":
      return { ...s, progress: undefined, validating: d };
    case "final":
      return { ...s, status: "done", progress: undefined, final: d, tries: d.tries ?? [] };
    case "error":
      return { ...s, status: "error", progress: undefined, error: d.message };
    default:
      return s;
  }
}

const EVENTS = ["start", "goal", "progress", "try_start", "try", "notice", "thought", "tiger", "validate_start", "final", "error"];

/** Start a run and follow its stream. Returns a function that stops listening. */
export async function runAgent(goal: string, date: string, onEvent: (e: AgentEvent) => void): Promise<() => void> {
  const j = await api<{ job_id: string; stream: string }>("agent", { goal, date });
  const es = new EventSource(j.stream);
  let closed = false;
  const close = () => { closed = true; es.close(); };
  for (const name of EVENTS) {
    es.addEventListener(name, (m) => {
      const raw = (m as MessageEvent).data;
      if (typeof raw !== "string") return; // the browser's own "error" (connection dropped) has no data
      onEvent({ event: name, data: JSON.parse(raw) });
      if (name === "final" || name === "error") close();
    });
  }
  // EventSource retries on its own; a run that's gone (API restarted) never comes back.
  es.onerror = () => {
    if (closed) return;
    fetch(`/api/agent/${j.job_id}`).then((r) => {
      if (r.status === 404) { onEvent({ event: "error", data: { message: "The API lost this run (was it restarted?). Ask Sam again." } }); close(); }
    }, () => undefined);
  };
  return close;
}

// ---------------------------------------------------------------- history (final analyses only)
// Kept on the API (shared) and in this browser (so past analyses still open when the API is down).

export type HistoryItem = {
  run_id: string; kind: "plan" | "question"; prompt: string; for_date: string; created_at: string;
  met: boolean | null; headline: string; local?: boolean;
};
const LOCAL_KEY = "dcwhatif.sam.history";
const LOCAL_MAX = 40;

type LocalEntry = HistoryItem & { final: AgentFinal };
function readLocal(): LocalEntry[] {
  try { return JSON.parse(localStorage.getItem(LOCAL_KEY) ?? "[]") as LocalEntry[]; } catch { return []; }
}
function writeLocal(xs: LocalEntry[]) {
  try { localStorage.setItem(LOCAL_KEY, JSON.stringify(xs.slice(0, LOCAL_MAX))); } catch { /* quota / private mode */ }
}

/** The history keeps only the final analysis: drop per-try details. */
export function slimFinal(f: AgentFinal): AgentFinal {
  const { tries, settings, ...rest } = f; // eslint-disable-line @typescript-eslint/no-unused-vars
  return { ...rest, tries: [], settings: [], tries_run: f.tries_run ?? tries?.length ?? 0 } as AgentFinal;
}

export function headlineOf(f: AgentFinal): string {
  if (f.kind === "question") return (f.answer ?? "").replace(/[*_`#]+/g, "").trim().slice(0, 160);
  const n = f.tries_run ?? f.tries?.length ?? 0;
  return `${f.check?.met ? "Goal met" : "Closest plan"} · waste ${f.check?.waste_pct > 0 ? "+" : ""}${(f.check?.waste_pct ?? 0).toFixed(1)}% · try ${f.chosen} of ${n}`;
}

export function rememberLocal(f: AgentFinal) {
  const id = f.history_id ?? `local-${Date.now()}`;
  const e: LocalEntry = {
    run_id: id, kind: f.kind ?? "plan", prompt: f.goal_text, for_date: f.date, created_at: f.created_at ?? new Date().toISOString(),
    met: f.kind === "question" ? null : !!f.check?.met, headline: headlineOf(f), final: slimFinal(f),
  };
  writeLocal([e, ...readLocal().filter((x) => x.run_id !== id)]);
}

export async function listHistory(apiOn: boolean): Promise<HistoryItem[]> {
  const local = readLocal();
  let remote: HistoryItem[] = [];
  if (apiOn) remote = await api<HistoryItem[]>("agent/history").catch(() => []);
  const ids = new Set(remote.map((r) => r.run_id));
  const merged = [...remote, ...local.filter((l) => !ids.has(l.run_id)).map(({ final, ...x }) => ({ ...x, local: true }))]; // eslint-disable-line @typescript-eslint/no-unused-vars
  return merged.sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export async function openHistory(id: string, apiOn: boolean): Promise<AgentFinal> {
  const l = readLocal().find((x) => x.run_id === id);
  if (l) return l.final;
  if (!apiOn) throw new Error("This analysis is saved on the API, which isn't running.");
  const h = await api<{ final: AgentFinal; created_at: string; run_id: string }>(`agent/history/${id}`);
  return { ...h.final, history_id: h.run_id, created_at: h.created_at };
}

/** Removes the browser copy, and the API copy when the API allows it (it may need the admin token). */
export async function deleteHistory(id: string, apiOn: boolean): Promise<string | null> {
  writeLocal(readLocal().filter((x) => x.run_id !== id));
  if (!apiOn || id.startsWith("local-")) return null;
  const r = await fetch(`/api/agent/history/${id}`, { method: "DELETE" });
  return r.ok || r.status === 404 ? null : `Removed from this browser; the API kept it (HTTP ${r.status}, admin token needed).`;
}
