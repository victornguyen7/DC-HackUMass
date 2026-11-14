import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import SimilarNights from "./SimilarNights";
import { ApiError } from "../lib/data";
import { classifyFailure, HISTORY_UNAVAILABLE, similarNightsQuery, spread, type SimilarNightsResult } from "../lib/similarNights";
import { useApp } from "../lib/store";

const match = (date: string, similarity: number, extra: Partial<SimilarNightsResult["matches"][0]> = {}) => ({
  date, similarity, students: 4100, weather: "rain", event_night: false, weekday: "Saturday", waste_lb: 640.2,
  stockout_min: 31.5, menu_overlap: null, same_weekday: true, same_weather: true, ...extra,
});
const result = (over: Partial<SimilarNightsResult> = {}): SimilarNightsResult => ({
  query: { date: "2026-11-14", weekday: "Saturday", students: 4000, weather: "rain", event_night: false, menu_date: null },
  matches: [match("2026-10-10", 0.9998), match("2026-10-17", 0.8825, { same_weather: false, weather: "clear", waste_lb: null })],
  source: "tiger-pgvector", synthetic: true, history_count: 43, method: "Cosine similarity of 48-dim condition vectors.",
  note: "Recorded nights are SYNTHETIC.", ...over,
});

let calls: string[] = [];
let reply: (url: string) => Response | Promise<Response>;

beforeEach(() => {
  calls = [];
  useApp.setState({ date: "2026-11-14", adopted: {}, scenario: { preset: null, lever: "" } });
  reply = () => Response.json(result());
  vi.stubGlobal("fetch", async (url: string) => {
    if (url.startsWith("/cache/")) return new Response(readFileSync(resolve("public", url.slice(1)), "utf8"));
    if (url === "/api/health") return Response.json({ ok: true });
    if (url.startsWith("/api/similar-nights")) { calls.push(url); return reply(url); }
    throw new Error(`Unexpected request: ${url}`);
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function page() {
  render(<MemoryRouter initialEntries={["/similar-nights"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><SimilarNights /></MemoryRouter>);
  await screen.findByRole("option", { name: /Prepared menu · Sat, Nov 14/ }); // manifest loaded
}
const search = () => fireEvent.click(screen.getByRole("button", { name: "Find similar nights" }));

it("starts with editable inputs and the selected night's prepared menu", async () => {
  await page();
  expect(screen.getByLabelText("Date")).toHaveProperty("value", "2026-11-14");
  expect(screen.getByLabelText("Expected students")).toHaveProperty("value", "4000");
  expect(screen.getByRole("radio", { name: "Rain" })).toHaveProperty("checked", true);
  expect(screen.getByLabelText("Menu")).toHaveProperty("value", "2026-11-14");
  expect(screen.getByText(/Which past nights looked like this one/)).toBeTruthy();
  expect(calls).toEqual([]);
});

it("omits menu_date for an unknown menu and renders matches in returned order", async () => {
  await page();
  fireEvent.change(screen.getByLabelText("Menu"), { target: { value: "" } });
  search();
  const list = await screen.findByRole("list", { name: /most similar first/ });
  expect(calls).toHaveLength(1);
  const params = new URL(calls[0], "http://x").searchParams;
  expect(params.has("menu_date")).toBe(false);
  expect(Object.fromEntries(params)).toEqual({ date: "2026-11-14", students: "4000", weather: "rain", event_night: "false", limit: "5" });

  const rows = within(list).getAllByRole("listitem").filter((li) => li.classList.contains("similar-match"));
  expect(rows.map((r) => within(r).getByRole("heading").textContent)).toEqual(["Saturday, Oct 10", "Saturday, Oct 17"]);
  expect(within(rows[0]).getByText("100%")).toBeTruthy();
  expect(within(rows[1]).getByText("88%")).toBeTruthy();
  expect(screen.queryByText(/Menu overlap/)).toBeNull();          // null overlap is hidden
  expect(within(rows[1]).getByText("—")).toBeTruthy();             // null waste gets a placeholder
  expect(screen.getAllByText("Synthetic history").length).toBeGreaterThan(0);
  expect(screen.getByText("Searched in Tiger · pgvector")).toBeTruthy();
  expect(screen.getByText(/isn't a forecast for Sat, Nov 14/)).toBeTruthy();
});

it("sends the chosen menu and shows overlap, including zero", async () => {
  reply = () => Response.json(result({
    query: { ...result().query, menu_date: "2026-11-14", event_night: true },
    matches: [match("2026-10-10", 0.96, { menu_overlap: 0.351 }), match("2026-10-03", 0.8, { menu_overlap: 0 })],
  }));
  await page();
  fireEvent.click(screen.getByRole("checkbox", { name: /Event night/ }));
  fireEvent.click(screen.getByRole("button", { name: "3 nights" }));
  search();
  await screen.findByText("Menu overlap 35%");
  expect(screen.getByText("Menu overlap 0%")).toBeTruthy();
  const params = new URL(calls[0], "http://x").searchParams;
  expect(params.get("menu_date")).toBe("2026-11-14");
  expect(params.get("event_night")).toBe("true");
  expect(params.get("limit")).toBe("3");
  expect(screen.getByText(/Event night · Menu of Sat, Nov 14/)).toBeTruthy();
});

it("explains an empty result", async () => {
  reply = () => Response.json(result({ matches: [] }));
  await page();
  search();
  expect(await screen.findByText(/No recorded nights before Sat, Nov 14/)).toBeTruthy();
});

it("shows validation feedback without calling the API for an impossible attendance", async () => {
  await page();
  fireEvent.change(screen.getByLabelText("Expected students"), { target: { value: "0" } });
  search();
  expect(screen.getByText(/whole number from 1 to 100,000/)).toBeTruthy();
  expect(calls).toEqual([]);
});

it("shows the backend's 422 message", async () => {
  reply = () => Response.json({ detail: "no prepared menu for 2025-01-01" }, { status: 422 });
  await page();
  search();
  expect(await screen.findByText("Check the details of the night")).toBeTruthy();
  expect(screen.getByText(/no prepared menu for 2025-01-01/)).toBeTruthy();
});

it("offers a retry when the API is down and keeps the inputs", async () => {
  reply = () => Response.json({ detail: "The simulation API isn't running. Start it with `make server`." }, { status: 503 });
  await page();
  fireEvent.change(screen.getByLabelText("Expected students"), { target: { value: "3200" } });
  search();
  expect(await screen.findByText("Can't reach the search service")).toBeTruthy();
  expect(screen.queryByText(/make server/)).toBeNull();              // no terminal commands in the UI
  reply = () => Response.json(result());
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByRole("list", { name: /most similar first/ });
  expect(calls).toHaveLength(2);
  expect(new URL(calls[1], "http://x").searchParams.get("students")).toBe("3200");
});

it("maps the history 503 separately", async () => {
  reply = () => Response.json({ detail: HISTORY_UNAVAILABLE }, { status: 503 });
  await page();
  search();
  expect(await screen.findByText("Historical search is temporarily unavailable")).toBeTruthy();
});

it("builds queries and classifies failures", () => {
  expect(similarNightsQuery({ date: "2026-11-14", students: 4000, weather: "snow", eventNight: false, menuDate: null, limit: 5 }))
    .toBe("similar-nights?date=2026-11-14&students=4000&weather=snow&event_night=false&limit=5");
  expect(classifyFailure(new ApiError(422, "bad")).kind).toBe("invalid");
  expect(classifyFailure(new ApiError(503, HISTORY_UNAVAILABLE)).kind).toBe("history");
  expect(classifyFailure(new ApiError(503, "proxy")).kind).toBe("offline");
  expect(classifyFailure(new TypeError("Failed to fetch")).kind).toBe("offline");
  expect(spread([match("a", 1, { waste_lb: 10 }), match("b", 1, { waste_lb: null }), match("c", 1, { waste_lb: 30 })], "waste_lb"))
    .toEqual({ median: 20, min: 10, max: 30, n: 2 });
});
