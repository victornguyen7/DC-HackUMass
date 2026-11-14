// Hall playback: turns an engine trace (timed events, SPEC §8.3) into positions
// on the top-down plan. Pure functions, no React, so they can be unit-tested.
//
// The browser never simulates. Between two events a student dwells at the first
// event's spot and walks to the next spot at the end of the gap; in a line, the
// student moves up from their slot at join time to the counter at service time.

import zones from "./zones.json";
import type { PanEvent, Trace, TraceEvent, TraceStudent } from "./types";

// ---------------------------------------------------------------- plan frame
// public/plan/worcester-2f-plan.svg: 849 x 465 px, 13.37 px per m, origin at
// the SW outer corner, y north (the same frame as data/worcester/layout).
export const PLAN_W = 849;
export const PLAN_H = 465;
export const PX_PER_M = 13.37;
export const toPx = ([x, y]: readonly [number, number] | number[]): [number, number] => [x * PX_PER_M, (34.6 - y) * PX_PER_M];

type P = [number, number];

export const ENTRANCE: P = [37.4, 9.6]; // check-in desk, just inside the main stairs
export const DISH_RETURN: P = [10.9, 26.4];
export const WALK_M_PER_MIN = 70;

// Queue heads and directions, from the measured plan model (scene.py QUEUES).
const QUEUES: Record<string, { head: P; dir: P; fold?: number }> = {
  street_food: { head: [47.4, 24.0], dir: [-1, 0], fold: 9 },
  tandoor: { head: [34.4, 18.2], dir: [1, 0], fold: 9 },
  latino: { head: [15.5, 16.0], dir: [0.243, -0.97], fold: 9 },
  grill: { head: [25.6, 22.6], dir: [-1, 0], fold: 9 },
  seasons: { head: [36.0, 24.6], dir: [0, -1], fold: 9 },
  mediterranean: { head: [27.6, 24.5], dir: [1, 0] },
  sushi: { head: [47.0, 18.3], dir: [-1, 0], fold: 9 },
  noodle_bowl: { head: [52.6, 22.4], dir: [0, 1] },
  display: { head: [55.4, 11.9], dir: [0, -1] },
  carving: { head: [53.4, 11.9], dir: [0, -1] }, // Nov 18 carving line beside Display (assumed spot)
  soups: { head: [28.6, 13.1], dir: [0, -1] },
  desserts: { head: [18.0, 23.6], dir: [0, -1] },
  breads: { head: [20.5, 23.6], dir: [0, -1] },
};

/** Position of the i-th person in a station's line (0 = at the counter).
 * Long lines fold back once beside themselves, like the Figma plan. */
export function queueSlot(station: string, i: number): P {
  const q = QUEUES[station] ?? { head: [32, 20] as P, dir: [1, 0] as P };
  const [dx, dy] = q.dir;
  let k = i;
  let col = 0;
  const fold = q.fold ?? 99;
  if (k >= fold) {
    col = Math.floor(k / fold);
    k = col % 2 === 1 ? fold - 1 - (k % fold) : k % fold;
  }
  return [q.head[0] + dx * 0.62 * k - dy * 0.7 * col, q.head[1] + dy * 0.62 * k + dx * 0.7 * col];
}

// ---------------------------------------------------------------- seats
type Zone = { table_xy_m: number[][] };
const ZONES = (zones as { zones: Record<string, Zone> }).zones;
const SEAT_OFFS: P[] = [[-0.45, 0.42], [0.45, 0.42], [-0.45, -0.42], [0.45, -0.42]];

/** Deterministic seat for a student in a zone (4-tops; seats are assumed, the plan doesn't show tables). */
export function seatFor(zoneId: string, studentId: number): P {
  const z = ZONES[zoneId] ?? ZONES.main;
  const n = z.table_xy_m.length * 4;
  const k = Math.floor(((studentId * 0.6180339887) % 1) * n);
  const [tx, ty] = z.table_xy_m[Math.floor(k / 4)];
  const [ox, oy] = SEAT_OFFS[k % 4];
  return [tx + ox, ty + oy];
}

export const tablesPx = () =>
  Object.entries(ZONES).flatMap(([id, z]) => z.table_xy_m.map((t) => ({ zone: id, xy: toPx(t) })));

// ---------------------------------------------------------------- students
export type Phase = "walking" | "line" | "seated" | "returning";
export type Pose = {
  id: number;
  diet: string;
  xy: P; // meters
  phase: Phase;
  station?: string;
  hands: "empty" | "plate" | "bowl";
  waitedMin?: number;
};

function spot(e: TraceEvent, seat: P, stationPos: Record<string, P>): P {
  switch (e.type) {
    case "enter":
    case "exit":
      return ENTRANCE;
    case "queue_join":
      return queueSlot(e.station!, Math.max(0, e.line ?? 0));
    case "served":
    case "balk":
    case "empty_pan":
      return queueSlot(e.station!, 0);
    case "sit":
    case "seconds":
      return seat;
    case "dish_return":
      return DISH_RETURN;
    default:
      return stationPos[e.station ?? ""] ?? seat;
  }
}

/** Where the student is at event i. A skipped line or an empty pan noticed at the same instant as
 * the previous event is a glance from where they stand (engine 0.4: students decide from a
 * distance), so it keeps the previous position instead of jumping to that station. */
function spotAt(ev: TraceEvent[], i: number, seat: P, stationPos: Record<string, P>): P {
  let j = i;
  while (j > 0 && (ev[j].type === "balk" || ev[j].type === "empty_pan") && Math.abs(ev[j].t - ev[j - 1].t) < 1e-6) j--;
  return spot(ev[j], seat, stationPos);
}

const dist = (a: P, b: P) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const lerp = (a: P, b: P, f: number): P => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];

/** Index of the last event at or before t (binary search), -1 if none. */
function lastAt(ev: TraceEvent[], t: number) {
  let lo = 0, hi = ev.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (ev[mid].t <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

export function poseAt(s: TraceStudent, t: number, stationPos: Record<string, P> = {}): Pose | null {
  const ev = s.events;
  if (!ev.length || t < ev[0].t || t >= ev[ev.length - 1].t) return null;
  const i = lastAt(ev, t);
  const e = ev[i];
  const nx = ev[i + 1];
  const seatZone = ev.find((x) => x.type === "sit")?.seat ?? "main";
  const seat = seatFor(seatZone, s.id);

  // hands: the latest vessel served before t, until dishes go back
  let hands: Pose["hands"] = "empty";
  for (let k = 0; k <= i; k++) {
    const x = ev[k];
    if (x.type === "served") hands = x.vessel === "bowl" ? "bowl" : "plate";
    if (x.type === "seconds") hands = "empty"; // dishes stay at the seat
  }
  if (e.type === "dish_return") hands = "empty";

  // Standing in line: queue_join -> next event at the same station.
  if (e.type === "queue_join" && nx && nx.station === e.station && nx.type !== "balk") {
    const span = Math.max(nx.t - e.t, 1e-6);
    const f = (t - e.t) / span;
    const slot = (e.line ?? 0) * (1 - f);
    const a = queueSlot(e.station!, Math.floor(slot));
    const b = queueSlot(e.station!, Math.max(0, Math.floor(slot) - 1));
    return { id: s.id, diet: s.diet, xy: lerp(a, b, slot - Math.floor(slot)), phase: "line", station: e.station, hands, waitedMin: t - e.t };
  }
  // Several items served in one pass have the same timestamp; stay at the counter.
  const from = spotAt(ev, i, seat, stationPos);
  const to = spotAt(ev, i + 1, seat, stationPos);
  const gap = nx.t - e.t;
  const walk = Math.min(gap, dist(from, to) / WALK_M_PER_MIN);
  const startWalk = nx.t - walk;
  if (t < startWalk) {
    return { id: s.id, diet: s.diet, xy: from, phase: e.type === "sit" ? "seated" : "walking", station: e.station, hands };
  }
  const f = walk > 0 ? (t - startWalk) / walk : 1;
  return {
    id: s.id, diet: s.diet, xy: lerp(from, to, f),
    phase: e.type === "dish_return" || nx.type === "dish_return" ? "returning" : "walking", hands,
  };
}

// ---------------------------------------------------------------- pans
export type PanTimeline = { dish: string; points: { t: number; level: number }[]; batches: { start: number; ready: number }[] };

/** Pan level over time. Engine pan events carry the level *after* the event
 * (model.py `rec.pan(..., pan.level, ...)`); single takes are not exported, so
 * we subtract the trace's `served` events between pan events. */
export function panTimelines(trace: Trace): Map<string, PanTimeline> {
  const out = new Map<string, PanTimeline>();
  const byDish = new Map<string, PanEvent[]>();
  for (const p of trace.pans) {
    if (!byDish.has(p.dish)) byDish.set(p.dish, []);
    byDish.get(p.dish)!.push(p);
  }
  const served = new Map<string, number[]>();
  for (const s of trace.students)
    for (const e of s.events)
      if (e.type === "served" && e.dish && byDish.has(e.dish)) {
        if (!served.has(e.dish)) served.set(e.dish, []);
        served.get(e.dish)!.push(e.t);
      }
  for (const [dish, pans] of byDish) {
    const takes = (served.get(dish) ?? []).sort((a, b) => a - b);
    const marks = pans.filter((p) => p.event !== "batch_start").sort((a, b) => a.t - b.t);
    const points: { t: number; level: number }[] = [];
    let level = 0;
    let ti = 0;
    for (let k = 0; k < marks.length; k++) {
      const m = marks[k];
      while (ti < takes.length && takes[ti] < m.t) {
        level = Math.max(0, level - 1);
        points.push({ t: takes[ti], level });
        ti++;
      }
      level = m.portions;
      points.push({ t: m.t, level });
    }
    const starts = pans.filter((p) => p.event === "batch_start").map((p) => p.t).sort((a, b) => a - b);
    const readies = pans.filter((p) => p.event === "batch_ready").map((p) => p.t).sort((a, b) => a - b);
    out.set(dish, { dish, points, batches: starts.map((s, i) => ({ start: s, ready: readies[i] ?? s })) });
  }
  return out;
}

export function levelAt(tl: PanTimeline, t: number): number {
  const pts = tl.points;
  let lo = 0, hi = pts.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (pts[mid].t <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans < 0 ? 0 : pts[ans].level;
}

/** Peak level reached (for drawing fill bars as a share of a full pan). */
export const peakLevel = (tl: PanTimeline) => Math.max(1, ...tl.points.map((p) => p.level));

// ---------------------------------------------------------------- live counters
export type Counters = {
  inside: number;
  inLine: number;
  seated: number;
  lines: Record<string, number>;
};

export function counters(poses: Pose[]): Counters {
  const lines: Record<string, number> = {};
  let inLine = 0, seated = 0;
  for (const p of poses) {
    if (p.phase === "line" && p.station) {
      inLine++;
      lines[p.station] = (lines[p.station] ?? 0) + 1;
    } else if (p.phase === "seated") seated++;
  }
  return { inside: poses.length, inLine, seated, lines };
}

/** Line length per station sampled every `step` minutes (for the station detail chart). */
export function lineSeries(trace: Trace, station: string, t0: number, t1: number, step = 10): number[] {
  const out: number[] = [];
  for (let t = t0; t < t1; t += step) {
    let n = 0;
    for (const s of trace.students) {
      const ev = s.events;
      const i = lastAt(ev, t + step / 2);
      if (i >= 0 && ev[i].type === "queue_join" && ev[i].station === station && ev[i + 1]?.type !== "balk") n++;
    }
    out.push(n);
  }
  return out;
}
