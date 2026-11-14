import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Hall from "./Hall";
import { useApp } from "../lib/store";

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
  expect(screen.getByText(/Representative night 25 of 30/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "1 Watch the hall" }));
  expect(screen.getAllByRole("button", { name: /^Inspect / })).toHaveLength(11);
  fireEvent.click(screen.getByRole("button", { name: "Inspect Latino" }));
  expect(screen.getByText("Pinto Beans")).toBeTruthy();
});
