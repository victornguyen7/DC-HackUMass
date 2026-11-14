import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { OptimizePanel, OptimizeResultView, WorldsPanel, WorldsResultView } from "./PlanSearch";
import { frontierChart, searchReducer, SEARCH_IDLE, winText, type OptimizeResult, type WorldsResult } from "../lib/planSearch";

const cmp = (waste: number, win: number) => ({
  n_pairs: 30,
  metrics: {
    waste_total: { diff: { p10: waste - 50, p50: waste, p90: waste + 50 }, win_rate: win, verdict: "better" as const },
    stockout_min_total: { diff: { p10: 0, p50: 5.8, p90: 12 }, win_rate: 0.3, verdict: "worse" as const },
    wait_p90: { diff: { p10: 0, p50: 0, p90: 0.1 }, win_rate: 0.5, verdict: "same" as const },
  },
});

const OPT: OptimizeResult = {
  date: "2026-11-13", trials: 80, seeds: 10, nights_searched: 800, feasible_trials: 1,
  baseline: { waste: 3364, stockout_min: 37.8, wait_p90: 15.2 },
  limits: { stockout_min_total: 39.7, wait_p90: 15.7 },
  history: [
    { n: 1, waste: 3364, stockout_min: 37.8, wait_p90: 15.2, within: true, best_waste: 3364 },
    { n: 2, waste: 2410, stockout_min: 43.1, wait_p90: 15.3, within: false, best_waste: 3364 },
    { n: 3, waste: 4100, stockout_min: 236, wait_p90: 17, within: false, best_waste: 3364 },
  ],
  frontier: [
    { n: 2, waste: 2410, stockout_min: 43.1, wait_p90: 15.3, wait_ok: true, within: false, waste_pct: -28.4,
      changes: ["Soups: batches x0.6", "Tandoor: batches x0.6", "Latino: batches x0.8", "Breads: reorder at x1.2"],
      levers: { kitchen: { "soups.lentil": { batch_size: 36, max_batches: 15 } } },
      validation: { seeds: [31, 60], waste_pct: -28.6, stockout_diff_min: 5.8, vs_baseline: cmp(-960, 1) } },
    { n: 1, waste: 3364, stockout_min: 37.8, wait_p90: 15.2, wait_ok: true, within: true, waste_pct: 0, changes: ["Today's plan"], levers: {} },
  ],
  levers: null, changes: [], method: "Searched 80 plans across 800 simulated nights.",
};

const WORLDS: WorldsResult = {
  n_worlds: 100, uncertainty: { crowd_sd: 0.1, peak_shift_sd_min: 10, popularity_sd: 0.3 },
  vs_baseline: cmp(-54.5, 0.86), waste: { baseline: { p10: 3000, p50: 3360, p90: 3800 }, plan: { p10: 2950, p50: 3300, p90: 3750 } },
  tornado: { metric: "waste_total", n_worlds: 50, bars: [
    { input: "crowd_sd", label: "Crowd size", data_request: "R01 attendance export", p10: 2966, p50: 3360, p90: 3843, width: 876 },
    { input: "seed", label: "Night-to-night only", data_request: "nothing", p10: 3240, p50: 3360, p90: 3471, width: 232 },
  ] },
};

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("folds stream events and drops replayed trials", () => {
  let s = searchReducer<OptimizeResult>(SEARCH_IDLE, { event: "trial", data: { n: 1, of: 2, waste: 10 } });
  s = searchReducer(s, { event: "trial", data: { n: 2, of: 2, waste: 9 } });
  s = searchReducer(s, { event: "trial", data: { n: 1, of: 2, waste: 10 } }); // reconnect replays from the start
  expect(s.trials.map((t) => t.n)).toEqual([1]);
  s = searchReducer(s, { event: "progress", data: { done: 3, total: 9 } });
  expect(s.progress).toEqual({ done: 3, total: 9 });
  s = searchReducer(s, { event: "final", data: OPT });
  expect(s.status).toBe("done");
  expect(s.progress).toBeUndefined();
  expect(searchReducer(s, { event: "error", data: { message: "boom" } })).toMatchObject({ status: "error", error: "boom" });
});

it("sizes the frontier chart around every trial, today and the limit", () => {
  const c = frontierChart(OPT);
  expect(c.pts).toHaveLength(3);
  expect(c.xMax).toBeGreaterThan(236);
  expect(c.yMin).toBeLessThan(2410);
  expect(winText(WORLDS.vs_baseline, "waste_total")).toBe("86%");
  expect(winText(WORLDS.vs_baseline, "nope")).toBeNull();
});

it("shows the trade-off honestly and loads a frontier plan", () => {
  const onLoad = vi.fn();
  render(<OptimizeResultView r={OPT} onLoad={onLoad} />);
  screen.getByText("Less waste costs stockouts here");
  // the unseen-night numbers lead, not the search-night ones
  screen.getByText(/−28\.6% waste · \+5\.8 stockout min/);
  expect(screen.queryByText("Today's plan")).toBeNull(); // today's plan is a chart point, not a card
  fireEvent.click(screen.getByRole("button", { name: "All 4 changes" }));
  screen.getByText("Breads: reorder at x1.2");
  fireEvent.click(screen.getByRole("button", { name: "Load into builder" }));
  expect(onLoad).toHaveBeenCalledWith(OPT.frontier[0].levers);
  screen.getByRole("img", { name: /Trade-off: 2 frontier plans/ });
});

it("shows win rates in worlds and the tornado widest first", () => {
  render(<WorldsResultView r={WORLDS} />);
  screen.getByText("86%");
  screen.getByText(/crowd ±10%, rush ±10 min, dish popularity ±30%/);
  const labels = screen.getAllByText(/Crowd size|Night-to-night only/).map((e) => e.textContent);
  expect(labels).toEqual(["Crowd size", "Night-to-night only"]);
  screen.getByText("ask for: R01 attendance export");
});

class FakeES {
  static last: FakeES | null = null;
  handlers: Record<string, (m: MessageEvent) => void> = {};
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeES.last = this; }
  addEventListener(name: string, fn: (m: MessageEvent) => void) { this.handlers[name] = fn; }
  close() { this.closed = true; }
  send(name: string, data: unknown) { this.handlers[name]?.({ data: JSON.stringify(data) } as MessageEvent); }
}

beforeEach(() => { FakeES.last = null; });

it("streams an optimizer run into progress, then the result", async () => {
  const posts: unknown[] = [];
  vi.stubGlobal("EventSource", FakeES);
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    if (url === "/api/optimize") { posts.push(JSON.parse(String(init?.body))); return Response.json({ job_id: "j1", stream: "/api/optimize/j1/stream" }); }
    throw new Error(`Unexpected request: ${url}`);
  });
  render(<OptimizePanel date="2026-11-13" apiUp onLoad={() => undefined} />);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Optimize plan" })); });
  expect(posts).toEqual([{ date: "2026-11-13" }]);
  expect(FakeES.last?.url).toBe("/api/optimize/j1/stream");
  act(() => FakeES.last!.send("trial", { n: 1, of: 80, waste: 3364, best_waste: 3364 }));
  screen.getByText(/Plan 1: 3,364 wasted · best within limits 3,364/);
  act(() => FakeES.last!.send("progress", { done: 20, total: 90 }));
  screen.getByText("Re-checking the best plans on nights the search never saw");
  act(() => FakeES.last!.send("final", OPT));
  expect(FakeES.last!.closed).toBe(true);
  screen.getByRole("button", { name: "Search again" });
  screen.getByText("Less waste costs stockouts here");
});

it("needs the API and a plan the builder made", () => {
  render(<WorldsPanel apiUp={false} request={{ date: "2026-11-13" }} />);
  expect(screen.getByRole("button", { name: "Test in 100 worlds" })).toHaveProperty("disabled", true);
  cleanup();
  render(<WorldsPanel apiUp request={null} />);
  expect(screen.getByRole("button", { name: "Test in 100 worlds" })).toHaveProperty("disabled", true);
});
