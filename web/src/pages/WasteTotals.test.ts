import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { Backtest } from "../lib/types";
import { dishLabel, dishUsdTotal } from "./Waste";

describe("per-dish $ / week (UI_ISSUES U16)", () => {
  it("adds up to the average simulated week when the back-test exports it", () => {
    const b = JSON.parse(readFileSync(resolve(process.cwd(), "../data/synthetic/worcester/backtest.json"), "utf8")) as Backtest;
    const total = dishUsdTotal(b, null);
    if (b.headline.usd_per_week_mean != null) expect(Math.abs(total - b.headline.usd_per_week_mean)).toBeLessThan(2);
    const stations = [...new Set((b.dishes ?? []).map((d) => d.station))];
    const byStation = stations.reduce((s, st) => s + dishUsdTotal(b, st), 0);
    expect(Math.abs(byStation - total)).toBeLessThan(0.5);
  });
});

it("tells dinner and late-night versions of a dish apart", () => {
  expect(dishLabel("late.grill.halal_burger_patty", "Halal Burger Patty")).toBe("Halal Burger Patty (late night)");
  expect(dishLabel("grill.halal_burger_patty", "Halal Burger Patty")).toBe("Halal Burger Patty");
});
