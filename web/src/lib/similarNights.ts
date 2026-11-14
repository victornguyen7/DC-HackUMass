// Similar Nights: client for GET /api/similar-nights (docs/SIMILAR_NIGHTS.md).
// The search needs the live engine API; there is no browser-only fallback.
import { api, ApiError } from "./data";

export const WEATHERS = ["clear", "rain", "snow", "cloudy", "windy", "other"] as const;
export type Weather = (typeof WEATHERS)[number];

export const WEATHER_LABELS: Record<string, string> = {
  clear: "Clear", rain: "Rain", snow: "Snow", cloudy: "Cloudy", windy: "Windy", other: "Other", cold: "Cold",
};

export type SimilarNightsParams = {
  date: string;
  students: number;
  weather: Weather;
  eventNight: boolean;
  /** null = unknown menu: the parameter is omitted, never sent as "". */
  menuDate: string | null;
  limit: number;
};

export type SimilarMatch = {
  date: string;
  similarity: number;
  students: number;
  weather: string;
  event_night: boolean;
  weekday: string;
  waste_lb: number | null;
  stockout_min: number | null;
  menu_overlap: number | null;
  same_weekday: boolean;
  same_weather: boolean;
};

export type SimilarNightsResult = {
  query: { date: string; weekday: string; students: number; weather: string; event_night: boolean; menu_date: string | null };
  matches: SimilarMatch[];
  source: "tiger-pgvector" | "local-synthetic";
  synthetic: boolean;
  history_count: number;
  method: string;
  note: string;
};

/** The backend's fixed 503 detail when Tiger answered but the search failed there. */
export const HISTORY_UNAVAILABLE = "Historical search is temporarily unavailable.";

export function similarNightsQuery(p: SimilarNightsParams): string {
  const q = new URLSearchParams({
    date: p.date,
    students: String(p.students),
    weather: p.weather,
    event_night: String(p.eventNight),
    limit: String(p.limit),
  });
  if (p.menuDate) q.set("menu_date", p.menuDate);
  return `similar-nights?${q}`;
}

export const searchSimilarNights = (p: SimilarNightsParams) => api<SimilarNightsResult>(similarNightsQuery(p));

export type SearchFailure =
  | { kind: "invalid"; message: string }
  | { kind: "history"; message: string }
  | { kind: "offline"; message: string };

/** Sort a failed request into the three states the page shows. */
export function classifyFailure(e: unknown): SearchFailure {
  if (e instanceof ApiError) {
    if (e.status === 422 || e.status === 400) return { kind: "invalid", message: e.message };
    if (e.status === 503 && e.message === HISTORY_UNAVAILABLE) return { kind: "history", message: e.message };
    if (e.status === 404) return { kind: "offline", message: "This engine doesn't have Similar Nights yet." };
  }
  return { kind: "offline", message: "The search service isn't answering." };
}

export const percent = (x: number) => `${Math.round(x * 100)}%`;

/** Local-time YYYY-MM-DD, `days` from today. */
export function isoDay(days = 0, from = new Date()): string {
  const d = new Date(from.getFullYear(), from.getMonth(), from.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function median(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Median and range of a recorded outcome across the matches (nulls skipped). */
export function spread(matches: SimilarMatch[], key: "waste_lb" | "stockout_min" | "students") {
  const xs = matches.map((m) => m[key]).filter((x): x is number => x !== null && x !== undefined);
  return xs.length ? { median: median(xs)!, min: Math.min(...xs), max: Math.max(...xs), n: xs.length } : null;
}
