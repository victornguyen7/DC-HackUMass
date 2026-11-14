import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Hall from "./Hall";
import { useApp } from "../lib/store";
import { fmt1, signed } from "../lib/format";
import type { Manifest, Result, Trace } from "../lib/types";

let now = 0;
let frameId = 0;
let frames: Map<number, FrameRequestCallback>;

beforeEach(() => {
  useApp.setState({ date: "2026-11-13", scenario: { preset: null, lever: "" }, hallTime: 1092 });
  now = 0; frameId = 0; frames = new Map();
  vi.spyOn(performance, "now").mockImplementation(() => now);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frames.set(++frameId, callback); return frameId; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  // jsdom does not draw Canvas; use the real replay, menus, controls, and data loaders.
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("fetch", async (url: string) => {
    if (url === "/api/health") return new Response(JSON.stringify({ ok: false }));
    if (!url.startsWith("/cache/")) throw new Error(`Unexpected request: ${url}`);
    const bytes = readFileSync(resolve("public", url.slice(1)));
    return new Response(url.endsWith(".gz") ? gunzipSync(bytes).toString() : bytes.toString());
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const mount = () => render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><Hall /></MemoryRouter>);
const manifest: Manifest = JSON.parse(readFileSync(resolve("public/cache/manifest.json"), "utf8"));

it.each([
  ["smaller_batches", "Cook smaller batches"],
  ["extra_server", "Add a Grill server"],
  ["theme_night", "Test a busier night"],
])("opens the saved %s comparison without a bundled replay or API", async (preset, title) => {
  const entry = manifest.entries.find((e) => e.date === "2026-11-14" && e.preset === preset && !e.lever)!;
  const result: Result = JSON.parse(readFileSync(resolve("public/cache", entry.file), "utf8"));
  expect(entry.trace).toBeNull();
  useApp.setState({ date: entry.date });
  mount();
  await screen.findByRole("button", { name: "Play" });
  fireEvent.click(screen.getByRole("button", { name: "2 Try a change" }));
  fireEvent.click(screen.getByRole("button", { name: new RegExp(title) }));
  const compare = screen.getByRole("button", { name: "Compare 30 nights" });
  expect((compare as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(compare);

  await screen.findByRole("heading", { name: "Results & trade-offs" });
  await screen.findByRole("heading", { name: "Replay unavailable" });
  const details = within(screen.getByRole("region", { name: "Simulation details" }));
  expect(details.getByText(result.label)).toBeTruthy();
  const wasteChange = `${signed(result.vs_baseline!.metrics.waste_total.diff.p50, fmt1)} portions`;
  expect(details.getByText((_, el) => el?.tagName === "STRONG" && el.textContent === wasteChange)).toBeTruthy();
  expect(useApp.getState().scenario.preset).toBe(preset);
  expect(screen.queryByRole("button", { name: "Play" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Floor plan" })).toBeNull();
  expect(screen.getByText(/Its saved results are still available/)).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Watch current plan" }));
  await screen.findByRole("button", { name: "Play" });
  expect(useApp.getState().scenario.preset).toBeNull();
  expect(screen.getByRole("button", { name: "Inspect Latino" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Inspect Desserts and Breads" })).toBeTruthy();
});

it("keeps comparison results visible during replay retries and recovers when the API returns", async () => {
  const entry = manifest.entries.find((e) => e.date === "2026-11-14" && e.preset === "smaller_batches" && !e.lever)!;
  const result: Result = JSON.parse(readFileSync(resolve("public/cache", entry.file), "utf8"));
  useApp.setState({ date: entry.date, scenario: { preset: entry.preset, lever: "" } });
  mount();
  await screen.findByRole("heading", { name: "Replay unavailable" });
  fireEvent.click(screen.getByRole("button", { name: "View comparison" }));

  let respond!: (response: Response) => void;
  const fetchReplay = vi.fn(() => new Promise<Response>((resolve) => { respond = resolve; }));
  vi.stubGlobal("fetch", fetchReplay);
  fireEvent.click(screen.getByRole("button", { name: "Retry replay" }));
  expect(fetchReplay).toHaveBeenCalledWith(`/api/traces/${entry.config_hash}?seed=${result.median_seed}`, undefined);
  expect(screen.getByRole("heading", { name: "Results & trade-offs" })).toBeTruthy();
  expect((screen.getByRole("button", { name: "Loading replay…" }) as HTMLButtonElement).disabled).toBe(true);

  await act(async () => { respond(new Response(JSON.stringify({ detail: "Unavailable" }), { status: 503 })); });
  expect(screen.getByText("The animation could not be loaded. Its saved results are still available.")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Results & trade-offs" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Retry replay" }));
  const trace: Trace = { night_seed: result.median_seed!, students: [], pans: [] };
  await act(async () => { respond(new Response(JSON.stringify(trace))); });
  expect(screen.getByRole("button", { name: "Play" })).toBeTruthy();
  expect(screen.getByRole("region", { name: "Floor plan" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Results & trade-offs" })).toBeTruthy();
  expect(useApp.getState().scenario.preset).toBe(entry.preset);
});

it("keeps Latino and Desserts menus available while the full replay plays", async () => {
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "Inspect Latino" }));
  expect(screen.getByText("Chicken Taco Filling")).toBeTruthy();
  expect(screen.getByText("Quinoa & Mango Salad")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Playback speed"), { target: { value: "60" } });
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  for (let i = 0; i < 120; i++) act(() => {
    now += 1000 / 60;
    const pending = [...frames.values()]; frames.clear();
    pending.forEach((callback) => callback(now));
  });
  expect(screen.getByTestId("playback-clock").textContent).not.toBe("6:12 pm");
  fireEvent.click(screen.getByRole("button", { name: "Pause" }));
  fireEvent.click(screen.getByRole("button", { name: "← All stations" }));
  fireEvent.click(screen.getByRole("button", { name: "Inspect Desserts and Breads" }));
  for (const name of ["Blueberry Crisp", "Orange Cake w/Cream Cheese Frosting", "Multigrain Bread"]) expect(screen.getByText(name)).toBeTruthy();
  fireEvent.change(screen.getByRole("slider", { name: "Playback time" }), { target: { value: "1380" } });
  expect(screen.getByText("Service ended")).toBeTruthy();
  expect(screen.getByText("Blueberry Crisp")).toBeTruthy();
  expect(screen.getByText("At close")).toBeTruthy();
});

it("loads the selected scenario's trace and retains all station access", async () => {
  mount();
  await screen.findByRole("button", { name: "Inspect Latino" });
  fireEvent.click(screen.getByRole("button", { name: "2 Try a change" }));
  fireEvent.click(screen.getByRole("button", { name: /Add a Grill server/ }));
  fireEvent.click(screen.getByRole("button", { name: "Compare 30 nights" }));
  await screen.findByRole("heading", { name: "Results & trade-offs" });
  await waitFor(() => expect((screen.getByLabelText("Replay scenario") as HTMLSelectElement).value).toBe("2026-11-13|extra_server|"));
  fireEvent.click(screen.getByRole("button", { name: "Replay options & legend" }));
  // The representative night changes with every bundle rebuild, so read it from the bundle.
  const manifest: Manifest = JSON.parse(readFileSync(resolve("public/cache/manifest.json"), "utf8"));
  const seed = Number(/_s(\d+)\.json\.gz$/.exec(manifest.entries.find((e) => e.key === "2026-11-13|extra_server|")!.trace!)![1]);
  expect(screen.getByText(new RegExp(`Representative night ${seed} of 30`))).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "1 Watch the hall" }));
  expect(screen.getAllByRole("button", { name: /^Inspect / })).toHaveLength(11);
  fireEvent.click(screen.getByRole("button", { name: "Inspect Latino" }));
  expect(screen.getByText("Pinto Beans")).toBeTruthy();
});
