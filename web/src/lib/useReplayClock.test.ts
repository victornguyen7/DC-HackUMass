import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useReplayClock } from "./useReplayClock";

let now = 0;
let id = 0;
let frames: Map<number, FrameRequestCallback>;

function advance(count = 60) {
  for (let i = 0; i < count; i++) {
    act(() => {
      now += 1000 / 60;
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach((callback) => callback(now));
    });
  }
}

beforeEach(() => {
  now = 0; id = 0; frames = new Map();
  vi.spyOn(performance, "now").mockImplementation(() => now);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frames.set(++id, callback); return id; });
  vi.stubGlobal("cancelAnimationFrame", (key: number) => frames.delete(key));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("full-trace replay clock", () => {
  it("advances continuously at the chosen speed and survives inspector rerenders", () => {
    const { result, rerender } = renderHook(({ panel }) => { void panel; return useReplayClock(990, 1470, 1092); }, { initialProps: { panel: "stations" } });
    act(() => { result.current.setSpeed(60); result.current.toggle(); });
    advance();
    expect(result.current.time).toBeCloseTo(1093, 0);
    rerender({ panel: "latino" });
    advance();
    expect(result.current.time).toBeCloseTo(1094, 0);
    expect(result.current.playing).toBe(true);
  });

  it("pauses exactly where requested and resumes from a scrubbed time", () => {
    const { result } = renderHook(() => useReplayClock(990, 1470, 1092));
    act(() => result.current.toggle());
    advance();
    act(() => result.current.toggle());
    const paused = result.current.time;
    advance();
    expect(result.current.time).toBe(paused);
    act(() => result.current.seek(1320));
    expect(result.current.playing).toBe(false);
    act(() => result.current.toggle());
    advance();
    expect(result.current.time).toBeCloseTo(1320.5, 0);
  });

  it("stops at the final frame and Play restarts an ended replay", () => {
    const { result } = renderHook(() => useReplayClock(990, 1093, 1092.9));
    act(() => { result.current.setSpeed(60); result.current.toggle(); });
    advance();
    expect(result.current.time).toBe(1093);
    expect(result.current.playing).toBe(false);
    act(() => result.current.toggle());
    expect(result.current.time).toBe(990);
    expect(result.current.playing).toBe(true);
  });
});
