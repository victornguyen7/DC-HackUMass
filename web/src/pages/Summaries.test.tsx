import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Dashboard from "./Dashboard";
import Waste from "./Waste";
import { useApp } from "../lib/store";

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
  const title = await screen.findByRole("heading", { name: "Top waste dishes" });
  const section = title.closest("section")!;
  expect(within(section).getAllByRole("columnheader").map((el) => el.textContent)).toEqual(["Dish", "Change", "$ / week"]);
  expect(within(section).getAllByRole("row")).toHaveLength(9);
  expect(within(section).getAllByText(/saved|more/).length).toBeGreaterThan(0);
});
