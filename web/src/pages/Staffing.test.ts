import { describe, expect, it } from "vitest";
import type { Result } from "../lib/types";
import { staffingNeed } from "./Dashboard";

describe("servers needed (UI_ISSUES U5)", () => {
  it("picks the busiest hour and keeps hours in order", () => {
    const r = { staffing: { grill: { "18": 3, "16": 1, "19": 3, "21": 2 } } } as unknown as Result;
    const need = staffingNeed(r, "grill");
    expect(need.peak).toBe(3);
    expect(need.hour).toBe(18); // first of the tied busiest hours
    expect(need.byHour.map(([h]) => h)).toEqual([16, 18, 19, 21]);
  });
  it("is empty for older bundles without staffing", () => {
    expect(staffingNeed({} as Result, "grill")).toEqual({ peak: 0, hour: null, byHour: [] });
  });
});
