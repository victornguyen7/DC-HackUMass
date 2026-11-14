import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Dashboard from "./Dashboard";
import Waste from "./Waste";
import { useApp } from "../lib/store";
import { fmt1 } from "../lib/format";

const originalShowModal = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, "showModal");
beforeEach(() => {
  useApp.setState({ date: "2026-11-13", adopted: {}, scenario: { preset: null, lever: "" } });
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: function (this: HTMLDialogElement) { this.setAttribute("open", ""); } });
  vi.stubGlobal("fetch", async (url: string) => {
    if (!url.startsWith("/cache/")) throw new Error(`Unexpected request: ${url}`);
    return new Response(readFileSync(resolve("public", url.slice(1)), "utf8"));
  });
});
afterEach(() => {
  cleanup(); vi.unstubAllGlobals();
  if (originalShowModal) Object.defineProperty(HTMLDialogElement.prototype, "showModal", originalShowModal);
  else Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");
});

it("keeps menus and detailed kitchen tables out of the overview until opened", async () => {
  render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><Dashboard /></MemoryRouter>);
  await screen.findByRole("button", { name: /View production plan/ });
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByRole("heading", { name: "Tonight's menu by station" })).toBeNull();
  expect(screen.queryByText("Weather: not connected")).toBeNull();
  expect(screen.queryByText("Personas uncalibrated")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^Menu / }));
  expect(screen.getByRole("heading", { name: "Tonight's menu by station" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Expected arrivals" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Overview" }));
  for (const [button, title] of [[/View production plan/, "Production plan"], [/View staffing/, "Staffing"], [/View leftovers/, "Expected left at close"]] as const) {
    fireEvent.click(screen.getByRole("button", { name: button }));
    const dialog = screen.getByRole("dialog", { name: title });
    expect(within(dialog).getByRole("heading")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: `Close ${title}` }));
    expect(screen.queryByRole("dialog")).toBeNull();
  }
  fireEvent.click(screen.getByRole("button", { name: /View alerts/ }));
  const alerts = screen.getByRole("dialog", { name: "Arrival alerts" });
  expect(within(alerts).getAllByRole("button", { name: /View in simulation/ }).length).toBeGreaterThan(0);
});

it("shows only Dish, Change, and $ / week in Top waste dishes", async () => {
  render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><Waste /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Last 4 weeks" })); // the page opens on the coming nights
  const title = await screen.findByRole("heading", { name: "Top waste dishes" });
  const section = title.closest("section")!;
  expect(within(section).getAllByRole("columnheader").map((el) => el.textContent)).toEqual(["Dish", "Change", "$ / week"]);
  expect(within(section).getAllByRole("row")).toHaveLength(9);
  expect(within(section).getAllByText(/saved|more/).length).toBeGreaterThan(0);
});

it("opens on the coming nights and follows the adopted plan per night", async () => {
  const manifest = JSON.parse(readFileSync(resolve("public/cache/manifest.json"), "utf8")) as { dates: string[] };
  // The real bundle copy when `dcsim bundle --waste-only` has written it (the page caches it), else a stub.
  const real = resolve("public/cache/waste/outlook.json");
  type E = { lb: { p50: number } };
  let outlook: { entries: Record<string, E> };
  if (existsSync(real)) outlook = JSON.parse(readFileSync(real, "utf8"));
  else {
    const r = (p50: number) => ({ p10: p50 - 20, p50, p90: p50 + 20 });
    const e = (date: string, preset: string | null, lb: number) => ({ date, preset, lever: "", label: preset ?? "baseline",
      config_hash: `${preset}-${date}`, lb: r(lb), usd: r(lb * 4), portions: r(lb * 4), stockout_min: r(40), n_nights: 30, basis: "pre-consumer" });
    outlook = { kind: "waste_outlook", generated_at: "", n_nights: 30, dates: manifest.dates, recommended_plan: "smaller_batches", missing: [],
      entries: Object.fromEntries(manifest.dates.flatMap((d) => [[`${d}|baseline|`, e(d, null, 800)], [`${d}|smaller_batches|`, e(d, "smaller_batches", 760)],
        [`${d}|forecast_reorder|`, e(d, "forecast_reorder", 700)]])) } as never;
    vi.stubGlobal("fetch", async (url: string) => {
      if (url === "/cache/waste/outlook.json") return new Response(JSON.stringify(outlook));
      if (!url.startsWith("/cache/")) throw new Error(`Unexpected request: ${url}`);
      return new Response(readFileSync(resolve("public", url.slice(1)), "utf8"));
    });
  }
  const d1 = manifest.dates[1];
  const base = outlook.entries[`${d1}|baseline|`].lb.p50, plan = outlook.entries[`${d1}|forecast_reorder|`].lb.p50;
  const change = plan / base - 1;
  const expected = `${change > 0 ? "+" : "−"}${fmt1(Math.abs(change) * 100)}%`;
  useApp.setState({ adopted: { [d1]: { preset: "forecast_reorder", lever: "" } } });
  render(<MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><Waste /></MemoryRouter>);
  await screen.findByRole("heading", { name: /next 8 nights/ });
  expect(screen.queryByRole("button", { name: "Last 8 weeks" })).toBeNull(); // only ranges that exist
  const table = (await screen.findByRole("heading", { name: "Plan per night" })).closest("section")!;
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(manifest.dates.length);
  expect(within(rows[1]).getByText("Adopted")).toBeTruthy();
  expect(within(rows[1]).getByText(expected)).toBeTruthy();       // the adopted preset vs tonight's plan
  expect(within(rows[0]).getByText("Recommended")).toBeTruthy();
  expect(screen.getByText(`1 of ${manifest.dates.length}`)).toBeTruthy(); // nights with an adopted plan
});
