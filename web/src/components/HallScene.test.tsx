import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HallScene } from "./HallScene";
import type { SceneCallbacks, SceneData } from "../lib/hall3d";

const mocks = vi.hoisted(() => ({
  create: vi.fn(), update: vi.fn(), zoom: vi.fn(), reset: vi.fn(), setMode: vi.fn(), refreshLabels: vi.fn(), dispose: vi.fn(),
}));
vi.mock("../lib/hall3d", () => ({ createHallScene: mocks.create }));
let callbacks: SceneCallbacks;
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("WebGL2RenderingContext", class {});
  mocks.create.mockImplementation((_canvas, c: SceneCallbacks) => { callbacks = c; return mocks; });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const data: SceneData = { poses: [{ id: 12, xy: [23, 22], diet: "none", phase: "walking", hands: "empty" }], stations: [{ id: "grill", pos: [25, 22], line: 5, empty: 0 }], selectedStation: null, selectedStudent: null, colorBy: "wait", missed: new Set() };

it("keeps one camera through replay updates and passes selections to the current handlers", async () => {
  const onStation = vi.fn(), onStudent = vi.fn(), onTopDown = vi.fn();
  const { rerender, unmount } = render(<HallScene {...data} onStation={onStation} onStudent={onStudent} onTopDown={onTopDown} />);
  await screen.findByRole("button", { name: "Reset" });
  fireEvent.click(screen.getByRole("button", { name: "Move" }));
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
  expect(mocks.setMode).toHaveBeenCalledWith("pan");
  expect(mocks.zoom).toHaveBeenCalledWith(1.25);
  const nextStudent = vi.fn();
  const next = { ...data, poses: [{ ...data.poses[0], xy: [24, 22] as [number, number] }] };
  rerender(<HallScene {...next} onStation={onStation} onStudent={nextStudent} onTopDown={onTopDown} />);
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(mocks.reset).not.toHaveBeenCalled();
  expect(mocks.update.mock.lastCall?.[0].poses[0].xy).toEqual([24, 22]);
  act(() => callbacks.onStudent(12));
  expect(nextStudent).toHaveBeenCalledWith(12);
  expect(onStudent).not.toHaveBeenCalled();
  act(() => callbacks.onLabels([{ id: "grill", label: "Grill", x: 100, y: 120, visible: true, left: 50, top: 70, width: 90, height: 44 }]));
  fireEvent.click(screen.getByRole("button", { name: "Grill: 5 in line" }));
  expect(onStation).toHaveBeenCalledWith("grill");
  fireEvent.click(screen.getByRole("button", { name: "Reset" }));
  expect(mocks.reset).toHaveBeenCalledOnce();
  unmount();
  expect(mocks.dispose).toHaveBeenCalledOnce();
});

it("offers the top-down view when WebGL is unsupported", async () => {
  vi.stubGlobal("WebGL2RenderingContext", undefined);
  const onTopDown = vi.fn();
  render(<HallScene {...data} onStation={vi.fn()} onStudent={vi.fn()} onTopDown={onTopDown} />);
  fireEvent.click(await screen.findByRole("button", { name: "Open top-down view" }));
  expect(onTopDown).toHaveBeenCalledOnce();
  expect(mocks.create).not.toHaveBeenCalled();
});

it("replaces unusable controls with a recovery action if the GPU context is lost", async () => {
  render(<HallScene {...data} onStation={vi.fn()} onStudent={vi.fn()} onTopDown={vi.fn()} />);
  await screen.findByRole("button", { name: "Reset" });
  act(() => callbacks.onUnavailable());
  await waitFor(() => expect(screen.queryByRole("button", { name: "Reset" })).toBeNull());
  expect(screen.getByRole("button", { name: "Open top-down view" })).toBeTruthy();
});
