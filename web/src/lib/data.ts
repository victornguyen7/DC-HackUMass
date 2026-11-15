// Loads the static demo bundle (public/cache). Every page reads from here, so
// the demo works with no API and no network (ENGINE.md §6, "L3 static bundle").

import type {
  Backtest, DateInfo, Manifest, ManifestEntry, MenuRef, Narration, Result, Trace, WasteFacts, WasteHistory,
} from "./types";

const BASE = `${import.meta.env.BASE_URL ?? "/"}cache/`;
const memo = new Map<string, Promise<unknown>>();

export function fetchJSON<T>(path: string): Promise<T> {
  let p = memo.get(path) as Promise<T> | undefined;
  if (!p) {
    p = fetch(BASE + path).then(async (r) => {
      if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
      if (path.endsWith(".gz")) return JSON.parse(await gunzipText(r)) as T;
      return (await r.json()) as T;
    });
    p.catch(() => memo.delete(path));
    memo.set(path, p);
  }
  return p;
}

/** Some static servers add `Content-Encoding: gzip` for .gz (the browser then
 * already decompressed it), others serve raw bytes. Check the magic number. */
async function gunzipText(r: Response): Promise<string> {
  const buf = new Uint8Array(await r.arrayBuffer());
  if (buf[0] !== 0x1f || buf[1] !== 0x8b) return new TextDecoder().decode(buf);
  const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
  return await new Response(stream).text();
}

export const loadManifest = () => fetchJSON<Manifest>("manifest.json");
export const loadDates = () => fetchJSON<DateInfo[]>("ref/dates.json");
export const loadLayout = () => fetchJSON<Layout>("ref/layout.json");
export const loadMenu = (date: string) => fetchJSON<MenuRef>(`ref/menu/${date}.json`);
export const loadArrivals = (date: string) =>
  fetchJSON<{ expected_students: number; windows: { start: string; start_min: number; expected: number }[] }>(
    `ref/arrivals/${date}.json`,
  );
export const loadResult = (e: ManifestEntry) => fetchJSON<Result>(e.file);
export const loadTrace = (e: ManifestEntry) => (e.trace ? fetchJSON<Trace>(e.trace) : Promise.resolve(null));
export const loadNarration = (e: ManifestEntry) =>
  e.narration ? fetchJSON<Narration>(e.narration) : Promise.resolve(null);
export const loadHistory = () => fetchJSON<WasteHistory>("waste/history.json");
export const loadBacktest = () => fetchJSON<Backtest>("waste/backtest.json");
export const loadFacts = () => fetchJSON<WasteFacts>("waste/facts.json");

export type Layout = {
  stations: Record<string, { pos_m: [number, number]; plan_label: string }>;
  key_points: Record<string, { pos_m: [number, number] }>;
  doors: { id: string; pos_m: [number, number] }[];
  seating_zones: { id: string; pos_m: [number, number]; seats: number; table_xy_m: [number, number][] }[];
};

/** Bundle key: "<date>|<preset or baseline>|<lever>". */
export function entryKey(date: string, preset: string | null, lever = "") {
  return `${date}|${preset ?? "baseline"}|${lever}`;
}

export function findEntry(m: Manifest, date: string, preset: string | null, lever = ""): ManifestEntry | undefined {
  const k = entryKey(date, preset, lever);
  return m.entries.find((e) => e.key === k);
}

/** Minimal POST /api/simulate client for configs that aren't in the bundle
 * (needs `make server`). Polls the job until done. */
export async function simulate(body: unknown, onProgress?: (msg: string) => void): Promise<Result> {
  const r = await fetch("/api/simulate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(r.status === 404 || r.status === 502 || r.status === 503 || r.status === 504
    ? "The simulation API isn't running. Start it with `make server`; cached presets still work."
    : `Simulation failed: HTTP ${r.status} ${await r.text()}`);
  const j = await r.json();
  if (j.status === "done") { rememberResult(j.result); return j.result as Result; }
  for (let i = 0; i < 240; i++) {
    await new Promise((res) => setTimeout(res, 500));
    const s = await (await fetch(`/api/jobs/${j.job_id}`)).json();
    onProgress?.(`running · ${Math.round((i + 1) / 2)} s`);
    if (s.status === "done") {
      const res = (s.result ?? (await (await fetch(`/api/results/${j.config_hash}`)).json())) as Result;
      rememberResult(res);
      return res;
    }
    if (s.status === "error") throw new Error(s.error ?? "simulation failed");
  }
  throw new Error("Simulation timed out");
}

// ---------------------------------------------------------------- live API (`make server`)
// Everything below is optional: the pages work from the bundle alone and use
// these only when the API answers.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/${path}`, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : Array.isArray(j.detail) ? j.detail.map((d: { msg: string }) => d.msg).join("; ") : msg;
    } catch { /* not JSON */ }
    throw new ApiError(r.status, msg);
  }
  return (await r.json()) as T;
}

let health: Promise<boolean> | null = null;
/** True when the engine API answers. The dev proxy turns "API down" into 200 {ok:false}. */
export function apiUp(): Promise<boolean> {
  if (!health) {
    health = fetch("/api/health").then(async (r) => r.ok && (await r.json()).ok === true).catch(() => false);
    // re-check after a while so starting `make server` mid-demo is picked up
    health.then(() => setTimeout(() => { health = null; }, 15000));
  }
  return health;
}

const resultKey = (h: string) => `dcwhatif.result.${h}`;
/** Results of API runs are kept in localStorage so an adopted plan still shows offline. */
export function rememberResult(r: Result) {
  try { localStorage.setItem(resultKey(r.config_hash), JSON.stringify(r)); } catch { /* quota / private mode */ }
}
export async function loadApiResult(h: string): Promise<Result> {
  try {
    const r = await api<Result>(`results/${h}`);
    rememberResult(r);
    return r;
  } catch (e) {
    try {
      const s = localStorage.getItem(resultKey(h));
      if (s) return JSON.parse(s) as Result;
    } catch { /* ignore */ }
    throw e;
  }
}

/** Median-night (or `seed`) trace from the API; the response is gzip-encoded, the browser decodes it. */
export const loadApiTrace = (h: string, seed?: number) =>
  api<Trace>(`traces/${h}${seed ? `?seed=${seed}` : ""}`);

export type Highlight = { student_id: number; reason: string; title: string };
export const loadApiHighlights = (h: string, seed: number) =>
  api<{ students: Highlight[] }>(`traces/${h}/highlights?seed=${seed}`).then((r) => r.students);

/** One student's card + narration (LLM, cached, or template) without streaming. */
export const narrateStudent = (h: string, seed: number, id: number) =>
  api<{ card: Narration["students"][string]["card"]; text: string; source: string; reason?: string }>(
    `narrate/${h}/${seed}/${id}?stream=false`,
  );

export type SavedScenario = { scenario_id: string; name: string; config_hash: string; status: "saved" | "adopted"; date: string };
export const listScenarios = (date: string) => api<SavedScenario[]>(`scenarios?date=${date}`);
export const saveScenario = (name: string, config_hash: string, status: "saved" | "adopted") =>
  api<SavedScenario & { forecast?: string }>("scenarios", { name, config_hash, status });

export type AskReply = {
  question: string; ok: boolean; error?: string | null; explanation: string; unsupported: string[];
  request?: { date?: string; preset?: string; levers?: ApiLevers }; settings?: string[]; source: string;
  /** which way answered: Gemini function calling, JSON-schema output, or keyword matching (no key / both failed) */
  method?: "function_calling" | "json_schema" | "keywords";
};
export type ApiLevers = {
  crowd_multiplier?: number | null;
  crowd_shift_min?: number | null;
  stations?: Record<string, { servers?: number; service_median_min?: number }>;
  kitchen?: Record<string, Record<string, number>>;
  popularity?: Record<string, number>;
  remove_dishes?: string[];
  add_dishes?: NewDish[];
};
export type NewDish = {
  name: string; station: string; role: string; diet_tags: string[]; vessel?: string; hot?: boolean;
  similar_to: string[]; popularity_multiplier?: number; affinity?: Record<string, number>;
};
export const ask = (question: string, date: string) => api<AskReply>("ask", { question, date });
export const similarDishes = (name: string, date: string, station?: string, role?: string) =>
  api<{ suggestions: { dish_id: string; name: string; station: string; role: string; similarity: number }[]; similar_to: string[]; source: string }>(
    "ai/similar-dishes", { name, date, station, role, k: 3 });
/** A new dish's look-alikes across every menu in Tiger and what they sold (POST /api/dish-evidence). */
export type DishEvidence = {
  neighbors: { dish_id: string; name: string; station: string; role: string; distance: number; nights: number;
    eaten_per_night: number | null; per_student: number | null }[];
  per_student_range: [number, number] | null; neighbor_nights: number;
  new_dish_sd: number; neighbor_spread: number | null; default_sd: number;
  popularity_check: { dishes: number; tau: number | null; detected: boolean; note: string };
};
export const dishEvidence = (date: string, dish: Pick<NewDish, "name" | "station" | "role" | "diet_tags" | "hot" | "similar_to">) =>
  api<DishEvidence>("dish-evidence", { date, name: dish.name, station: dish.station, role: dish.role,
    diet_tags: dish.diet_tags, hot: dish.hot, similar_to: dish.similar_to });
export const dishPrior = (date: string, dish: NewDish) =>
  api<{ affinity: Record<string, number>; reasons: Record<string, string>; label: string; source: string;
    popularity: { value: number; range: [number, number]; borrowed_from: string[] }; lever: NewDish }>("ai/dish-prior", { date, dish });
export const explain = (scenario_hash: string, baseline_hash: string) =>
  api<{ text: string; source: string; reason?: string }>("explain", { scenario_hash, baseline_hash, n_nights: 30 });
