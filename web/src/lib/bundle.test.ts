// Checks the UI's playback and data logic against the real demo bundle in public/cache.
import { existsSync, readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { counters, levelAt, panTimelines, poseAt, queueSlot, seatFor, type Pose } from "./playback";
import { missedFirst } from "../pages/Hall";
import { apiBody, bundleLever, draftFromRequest } from "../pages/Lab";
import { findEntry } from "./data";
import { clock, groupFlags, scenarioLabel } from "./format";
import type { Backtest, Manifest, Result, Trace } from "./types";

const CACHE = join(__dirname, "../../public/cache");
const json = <T,>(p: string): T => JSON.parse(readFileSync(join(CACHE, p), "utf8"));
const manifest = json<Manifest>("manifest.json");
const base = findEntry(manifest, manifest.featured_date, null, "")!;
const result = json<Result>(base.file);
const trace: Trace = JSON.parse(gunzipSync(readFileSync(join(CACHE, base.trace!))).toString());

// Outer footprint (m), from data/worcester/layout/floor_plan.json
const FOOT = [[0, 6.5], [30.8, 6.5], [30.8, 0], [63.5, 0], [63.5, 21.1], [61.9, 21.1], [61.9, 34.5], [1.6, 34.5], [1.6, 25.4], [0, 25.4]];
function inside([x, y]: number[]) {
  let c = false;
  for (let i = 0, j = FOOT.length - 1; i < FOOT.length; j = i++) {
    const [xi, yi] = FOOT[i], [xj, yj] = FOOT[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c;
  }
  return c;
}

describe("bundle", () => {
  it("every manifest entry points at files that exist", () => {
    const missing = manifest.entries.flatMap((e) => [e.file, e.trace, e.narration].filter((f): f is string => !!f && !existsSync(join(CACHE, f))));
    expect(missing).toEqual([]);
  });
  it("every date has a baseline and every preset; baselines and featured-date presets have a replay", () => {
    for (const d of manifest.dates) {
      for (const p of [null, ...manifest.presets]) {
        const e = findEntry(manifest, d, p, "");
        expect(e, `${d} ${p}`).toBeTruthy();
        // The bundle exports preset traces for the featured date only (docs/UI_ISSUES.md W-bundle).
        if (p === null || d === manifest.featured_date) expect(e!.trace, `${d} ${p} trace`).toBeTruthy();
      }
    }
  });
});

describe("playback", () => {
  const times = [result.open + 5, 18 * 60, 18 * 60 + 12, 19 * 60, 20 * 60 + 30, result.close - 1];
  const posesAt = (t: number) => trace.students.map((s) => poseAt(s, t)).filter((p): p is Pose => !!p);

  it("queue slot 0 is the queue head", () => {
    expect(queueSlot("tandoor", 0)).toEqual([34.4, 18.2]);
    expect(queueSlot("tandoor", 3)[0]).toBeGreaterThan(34.4); // Tandoor's line runs east (east frontage)
  });

  it("seats are inside the building and in the right room", () => {
    for (let id = 1; id < 500; id++) {
      expect(inside(seatFor("main", id))).toBe(true);
      const b = seatFor("back", id);
      expect(inside(b)).toBe(true);
      expect(b[1]).toBeLessThan(6.5); // back seating room is y 0-6.5
    }
  });

  it("no student is drawn outside the footprint", () => {
    const out: string[] = [];
    for (const t of times) for (const p of posesAt(t)) if (!inside(p.xy)) out.push(`#${p.id} @${clock(t)} ${p.phase} ${p.xy.map((v) => v.toFixed(1))}`);
    expect(out.slice(0, 10)).toEqual([]);
  });

  it("students are hidden before they enter and after they leave", () => {
    for (const s of trace.students.slice(0, 300)) {
      expect(poseAt(s, s.events[0].t - 0.01)).toBeNull();
      expect(poseAt(s, s.events[s.events.length - 1].t + 0.01)).toBeNull();
    }
  });

  it("inside count matches enter/exit events", () => {
    for (const t of times) {
      const expected = trace.students.filter((s) => s.events[0].t <= t && t < s.events[s.events.length - 1].t).length;
      expect(counters(posesAt(t)).inside).toBe(expected);
    }
  });

  it("hands are empty until the first serving", () => {
    for (const s of trace.students.slice(0, 300)) {
      const firstServed = s.events.find((e) => e.type === "served");
      const t = firstServed ? firstServed.t - 0.01 : s.events[1]?.t;
      const p = t != null ? poseAt(s, t) : null;
      if (p) expect(p.hands).toBe("empty");
    }
  });

  it("pan levels never go negative and are 0 when a student finds the pan empty", () => {
    const pans = panTimelines(trace);
    for (const tl of pans.values()) for (const p of tl.points) expect(p.level).toBeGreaterThanOrEqual(0);
    let checked = 0, zero = 0;
    for (const s of trace.students)
      for (const e of s.events)
        if (e.type === "empty_pan" && e.dish && pans.has(e.dish) && e.t < result.close) {
          checked++;
          if (levelAt(pans.get(e.dish)!, e.t) === 0) zero++;
        }
    expect(checked).toBeGreaterThan(50);
    expect(zero / checked).toBeGreaterThan(0.95);
  });

  it("missed-first-choice colouring is close to the engine's metric", () => {
    const share = missedFirst(trace.students).size / trace.students.length;
    expect(Math.abs(share - result.headline.missed_first_choice_rate.p50)).toBeLessThan(0.08);
  });
});

describe("lab levers", () => {
  const D = { preset: null, crowd: 1, servers: {}, kitchen: {} };
  it("maps single cached levers to bundle keys", () => {
    expect(bundleLever(D)).toBe("");
    expect(bundleLever({ ...D, crowd: 1.1 })).toBe("crowd_multiplier=1.1");
    expect(bundleLever({ ...D, servers: { grill: 3 } })).toBe("stations.grill.servers=3");
    expect(findEntry(manifest, manifest.featured_date, null, bundleLever({ ...D, crowd: 0.75 })!)).toBeTruthy();
  });
  it("combinations and kitchen edits need the API", () => {
    expect(bundleLever({ ...D, crowd: 1.1, servers: { grill: 3 } })).toBeNull();
    expect(bundleLever({ ...D, preset: "combined", crowd: 1.1 } as never)).toBeNull();
    expect(bundleLever({ ...D, kitchen: { x: { batch_size: 10 } } })).toBeNull();
  });
  it("labels scenarios", () => {
    expect(scenarioLabel(null, "")).toBe("Baseline");
    expect(scenarioLabel(null, "stations.street_food.servers=3")).toBe("Baseline · Street Food 3 servers");
  });
});

describe("API-backed UI pieces", () => {
  it("menu edits are never served from the bundle", () => {
    const D = { preset: null, crowd: 1, servers: {}, kitchen: {} };
    expect(bundleLever({ ...D, remove: ["grill.halal_burger_patty"] })).toBeNull();
    expect(bundleLever({ ...D, popularity: { x: 1.2 } })).toBeNull();
    expect(bundleLever({ ...D, shift: -15 })).toBeNull();
    expect(bundleLever({ ...D, remove: [], add: [], popularity: {}, shift: 0, serviceMin: {} })).toBe("");
  });
  it("plain-English settings round-trip into an API request", () => {
    const levers = {
      crowd_multiplier: 1.1, crowd_shift_min: -15,
      stations: { grill: { servers: 3, service_median_min: 0.4 } },
      kitchen: { "grill.x": { batch_size: 10 } },
      popularity: { "grill.x": 1.3 }, remove_dishes: ["latino.y"],
    };
    const d = draftFromRequest({ date: "2026-11-13", preset: "smaller_batches", levers });
    expect(d.preset).toBe("smaller_batches");
    const body = apiBody("2026-11-13", d);
    expect(body.preset).toBe("smaller_batches");
    expect(body.levers).toMatchObject({ ...levers, add_dishes: [] });
  });
  it("long-line alerts collapse into one, after the warnings", () => {
    const g = groupFlags(result.flags);
    const n = result.flags.filter((f) => f.kind === "long_line").length;
    expect(n).toBeGreaterThan(1);
    expect(g).toHaveLength(result.flags.length - n + 1);
    const i = g.findIndex((f) => f.kind === "long_line");
    expect(g.slice(0, i).every((f) => f.severity === "warn")).toBe(true);
    expect(g[i].text).toMatch(new RegExp(`^${n} stations have more than 15 people`));
  });
  it("back-test per-station split adds up to the nightly totals", () => {
    const b = json<Backtest>("waste/backtest.json");
    expect(b.dishes?.length).toBeGreaterThan(20);
    for (const n of b.nights) {
      const st = Object.values(n.by_station!);
      expect(Math.abs(st.reduce((a, x) => a + x.recorded_lb, 0) - n.recorded_waste_lb)).toBeLessThan(1);
      // plan per station is the mean of seeds; the nightly figure is the median, so allow a few percent
      expect(Math.abs(st.reduce((a, x) => a + x.plan_lb, 0) - n.plan_waste_lb.p50) / n.plan_waste_lb.p50).toBeLessThan(0.05);
    }
  });
});
