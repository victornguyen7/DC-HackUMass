import type { Range } from "./types";

export const fmtInt = (n: number) => Math.round(n).toLocaleString("en-US");
export const fmt1 = (n: number) => (Math.round(n * 10) / 10).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** Minutes since midnight -> "6:12 pm". */
export function clock(min: number, withSuffix = true) {
  const m = Math.round(min) % (24 * 60);
  const h = Math.floor(m / 60);
  const mm = String(m % 60).padStart(2, "0");
  const h12 = ((h + 11) % 12) + 1;
  return withSuffix ? `${h12}:${mm} ${h >= 12 ? "pm" : "am"}` : `${h12}:${mm}`;
}

/** [start, end) minutes of the service period a dish belongs to (dinner, or late night after dinner_close). */
export function dishWindow(r: { open: number; close: number; dinner_close?: number }, period?: string): [number, number] {
  const dinnerEnd = r.dinner_close ?? r.close;
  return period === "late_night" ? [dinnerEnd, r.close] : [r.open, dinnerEnd];
}

export const rangeText = (r: Range, f: (n: number) => string = fmtInt) => `${f(r.p10)}–${f(r.p90)}`;

export function signed(n: number, f: (n: number) => string = fmtInt) {
  if (f(Math.abs(n)) === f(0)) return `±${f(0)}`;
  return `${n > 0 ? "+" : "−"}${f(Math.abs(n))}`;
}

export function dateLabel(iso: string, opts: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric" }) {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("en-US", opts);
}

export const STATION_NAMES: Record<string, string> = {
  grill: "Grill", tandoor: "Tandoor", mediterranean: "Mediterranean", seasons: "Seasons", latino: "Latino",
  street_food: "Street Food", noodle_bowl: "Noodle Bowl", sushi: "Sushi", display: "Display", soups: "Soups",
  breads: "Breads", desserts: "Desserts", carving: "Carving",
};
export const stationName = (id: string) => STATION_NAMES[id] ?? id.replace(/_/g, " ");

export const PRESET_LABELS: Record<string, string> = {
  smaller_batches: "Smaller batches of favorites",
  trim_slow: "Trim slow sellers",
  extra_server: "Extra server at busiest",
  theme_night: "Theme night +25%",
  combined: "Combined plan",
};

export function scenarioLabel(preset: string | null, lever: string, label?: string) {
  if (lever === "api") return label ?? "API run";
  const base = preset ? PRESET_LABELS[preset] ?? preset : "Baseline";
  if (!lever) return base;
  const [k, v] = lever.split("=");
  if (k === "crowd_multiplier") return `${base} · crowd ×${v}`;
  const m = k.match(/^stations\.(.+)\.servers$/);
  if (m) return `${base} · ${stationName(m[1])} ${v} servers`;
  return `${base} · ${lever}`;
}

export function downloadCSV(name: string, rows: (string | number)[][]) {
  const esc = (v: string | number) => {
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const blob = new Blob([rows.map((r) => r.map(esc).join(",")).join("\n") + "\n"], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/** One alert for all long lines instead of one per station (UI_ISSUES U12), placed after the warnings. */
export function groupFlags<F extends { kind: string; severity: string; text: string; station?: string }>(flags: F[]): F[] {
  const long = flags.filter((f) => f.kind === "long_line");
  if (long.length < 2) return flags;
  const people = (f: F) => Number(f.text.match(/about (\d+)/)?.[1] ?? 0);
  const sorted = [...long].sort((a, b) => people(b) - people(a));
  const parts = sorted.map((f) => `${f.station ? stationName(f.station) : f.text.split(" line")[0]} ${people(f)}`);
  const merged = {
    ...sorted[0],
    station: undefined,
    text: `${long.length} stations have more than 15 people in line at peak: ${parts.slice(0, 4).join(", ")}${parts.length > 4 ? ` and ${parts.length - 4} more` : ""}.`,
  } as F;
  const rest = flags.filter((f) => f.kind !== "long_line");
  const firstInfo = rest.findIndex((f) => f.severity !== "warn" && f.severity !== "bad");
  const at = firstInfo === -1 ? rest.length : firstInfo;
  return [...rest.slice(0, at), merged, ...rest.slice(at)];
}
