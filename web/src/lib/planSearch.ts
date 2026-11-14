// Plan search on the engine API (IMPROVEMENTS 2.1, 2.2):
//   POST /api/optimize -> kitchen plans searched with Optuna: a waste-vs-stockouts frontier
//   POST /api/worlds   -> a plan vs the baseline in plausible worlds, plus a waste tornado
// Both are background jobs that stream Server-Sent Events; `searchReducer` folds them into one state.

import { api } from "./data";
import type { ApiLevers } from "./data";
import type { Compare, Range } from "./types";

export type TrialRow = {
  n: number; of: number; waste: number; stockout_min: number; wait_p90: number;
  wait_ok?: boolean; within: boolean; feasible: boolean; best_waste: number | null;
};

export type FrontierPlan = {
  n: number; waste: number; stockout_min: number; wait_p90: number; wait_ok: boolean; within: boolean;
  waste_pct: number; changes: string[]; levers: ApiLevers;
  validation?: { seeds: [number, number]; waste_pct: number; stockout_diff_min: number; vs_baseline: Compare };
};

export type WorldsResult = {
  n_worlds: number;
  uncertainty: { crowd_sd: number; peak_shift_sd_min: number; popularity_sd: number };
  baseline_label?: string; plan_label?: string;
  vs_baseline: Compare;
  waste: { baseline: Range; plan: Range };
  tornado?: { metric: string; n_worlds: number; bars: TornadoBar[] } | null;
  note?: string;
};

export type TornadoBar = { input: string; label: string; data_request: string; p10: number; p50: number; p90: number; width: number };

export type OptimizeResult = {
  date: string; trials: number; seeds: number; nights_searched: number; feasible_trials: number;
  baseline: { waste: number; stockout_min: number; wait_p90: number };
  limits: { stockout_min_total: number; wait_p90: number };
  history: Omit<TrialRow, "of" | "feasible">[];
  frontier: FrontierPlan[];
  levers: ApiLevers | null; changes: string[]; method: string;
  validation?: { seeds: [number, number]; vs_baseline: Compare };
  worlds?: Pick<WorldsResult, "n_worlds" | "uncertainty" | "vs_baseline" | "waste">;
};

export type SearchState<R> = {
  status: "idle" | "running" | "done" | "error";
  trials: TrialRow[];
  progress?: { done: number; total: number };
  result?: R;
  error?: string;
};

export const SEARCH_IDLE: SearchState<never> = { status: "idle", trials: [] };

export type SearchEvent = { event: string; data: any }; // eslint-disable-line @typescript-eslint/no-explicit-any

export function searchReducer<R>(s: SearchState<R>, e: SearchEvent): SearchState<R> {
  switch (e.event) {
    case "trial": // a reconnecting stream replays from the start: drop trials we already have
      return { ...s, status: "running", trials: [...s.trials.filter((t) => t.n < e.data.n), e.data] };
    case "progress":
      return { ...s, status: "running", progress: e.data };
    case "final":
      return { ...s, status: "done", progress: undefined, result: e.data as R };
    case "error":
      return { ...s, status: "error", progress: undefined, error: e.data.message };
    default:
      return s;
  }
}

/** Start a search job and follow its stream. Returns a function that stops listening. */
export async function runSearch(kind: "optimize" | "worlds", body: unknown, onEvent: (e: SearchEvent) => void): Promise<() => void> {
  const j = await api<{ job_id: string; stream: string }>(kind, body);
  const es = new EventSource(j.stream);
  let closed = false;
  const close = () => { closed = true; es.close(); };
  for (const name of ["trial", "progress", "final", "error"]) {
    es.addEventListener(name, (m) => {
      const raw = (m as MessageEvent).data;
      if (typeof raw !== "string") return; // the browser's own "error" (connection dropped) has no data
      onEvent({ event: name, data: JSON.parse(raw) });
      if (name === "final" || name === "error") close();
    });
  }
  es.onerror = () => { // EventSource retries on its own; a job the API forgot never comes back
    if (closed) return;
    fetch(`/api/${kind}/${j.job_id}`).then((r) => {
      if (r.status === 404) { onEvent({ event: "error", data: { message: "The API lost this run (was it restarted?). Start it again." } }); close(); }
    }, () => undefined);
  };
  return close;
}

/** "Beats today's plan in 86% of worlds" for one metric (lower is better for every headline metric). */
export function winText(c: Compare, metric: string): string | null {
  const m = c.metrics[metric];
  return m ? `${Math.round(m.win_rate * 100)}%` : null;
}

/** Frontier points as chart coordinates, plus today's plan and every trial (for the cloud). */
export function frontierChart(r: OptimizeResult) {
  const pts = r.history.map((h) => ({ x: h.stockout_min, y: h.waste }));
  const xs = [...pts.map((p) => p.x), r.baseline.stockout_min, r.limits.stockout_min_total];
  const ys = [...pts.map((p) => p.y), r.baseline.waste];
  return {
    pts,
    xMax: Math.max(...xs) * 1.05,
    yMin: Math.min(...ys) * 0.95,
    yMax: Math.max(...ys) * 1.03,
  };
}
