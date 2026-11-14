import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { alongRoute, isWalkable, walkingRoute } from "./navigation";
import { DISH_RETURN, ENTRANCE, poseAt, queueSlot, seatFor } from "./playback";
import type { Point } from "./hallGeometry";
import type { Manifest, Trace } from "./types";

const stations = ["grill", "tandoor", "mediterranean", "seasons", "latino", "street_food", "noodle_bowl", "sushi", "display", "soups", "desserts", "breads"];
function verifyRoute(from: Point, to: Point) {
  const route = walkingRoute(from, to);
  const samples = Math.max(1, Math.ceil(route.length / .1));
  for (let i = 0; i <= samples; i++) {
    const p = alongRoute(route, i / samples);
    if (!isWalkable(p)) throw new Error(`Route crosses a solid: ${from} → ${to} at ${p}`);
  }
  return route;
}

describe("routes used by both hall views", () => {
  it("detours around the center island and stair core", () => {
    const center = verifyRoute([22, 18], [35, 18]);
    expect(center.length).toBeGreaterThan(16);
    const stairs = verifyRoute([37.5, 12], [50, 8]);
    expect(stairs.length).toBeGreaterThan(Math.hypot(12.5, -4) + 2);
  });

  it("enters back seating through a doorway, never through the partition", () => {
    for (const [from, to] of [ [[29, 12], [34, 4]], [[54, 9], [54, 4]], [[62, 11], [62.5, 3.7]] ] as [Point, Point][]) {
      const route = verifyRoute(from, to);
      let crossings = 0;
      for (let i = 1; i < route.points.length; i++) {
        const a = route.points[i - 1], b = route.points[i];
        if ((a[1] > 6.5) === (b[1] > 6.5)) continue;
        const x = a[0] + (b[0] - a[0]) * (6.5 - a[1]) / (b[1] - a[1]);
        expect([[32, 33.4], [56.2, 57.4], [60.8, 62]].some(([left, right]) => x > left && x < right), `partition crossed at x=${x}`).toBe(true);
        crossings++;
      }
      expect(crossings).toBeGreaterThan(0);
    }
  });

  it("keeps crowded queue slots and movement between folded rows off solids", () => {
    for (const station of stations) for (let i = 0; i < 100; i++) {
      verifyRoute(queueSlot(station, i), queueSlot(station, i + 1));
    }
  }, 20000);

  it("routes to seats, serving counters, and dish return without changing event timing", () => {
    for (let id = 1; id <= 80; id++) {
      const seat = seatFor(id % 2 ? "main" : "back", id);
      verifyRoute(ENTRANCE, seat);
      verifyRoute(seat, DISH_RETURN);
      verifyRoute(queueSlot(stations[id % stations.length], 0), seat);
    }
    const root = join(__dirname, "../../public/cache");
    const manifest: Manifest = JSON.parse(readFileSync(join(root, "manifest.json"), "utf8"));
    for (const preset of [null, "theme_night"]) {
      const entry = manifest.entries.find((e) => e.date === manifest.featured_date && e.preset === preset && !e.lever)!;
      const trace: Trace = JSON.parse(gunzipSync(readFileSync(join(root, entry.trace!))).toString());
      for (const student of trace.students.filter((_, i) => i % 173 === 0)) {
        for (let i = 0; i < student.events.length - 1; i++) {
          const a = student.events[i], b = student.events[i + 1];
          for (const f of [0, .2, .5, .9, .99]) {
            const t = a.t + (b.t - a.t) * f, pose = poseAt(student, t);
            expect(pose && isWalkable(pose.xy), `student ${student.id} at ${t}`).toBe(true);
          }
        }
        expect(poseAt(student, student.events[0].t - .01)).toBeNull();
        expect(poseAt(student, student.events.at(-1)!.t)).toBeNull();
      }
    }
  }, 30000);
});
