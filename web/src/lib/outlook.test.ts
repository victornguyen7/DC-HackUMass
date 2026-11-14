import { describe, expect, it } from "vitest";
import { resolveNights, totals, type Outlook, type OutlookEntry } from "./outlook";

const r = (p50: number) => ({ p10: p50 - 10, p50, p90: p50 + 10 });
const entry = (date: string, preset: string | null, lb: number, hash: string, lever = ""): OutlookEntry => ({
  date, preset, lever, label: preset ?? "baseline", config_hash: hash, lb: r(lb), usd: r(lb * 4), portions: r(lb * 4),
  stockout_min: r(40), n_nights: 30, basis: "pre-consumer",
});
const D1 = "2026-11-13", D2 = "2026-11-14", D3 = "2026-11-15", D4 = "2026-11-16";
const o: Outlook = {
  kind: "waste_outlook", generated_at: "", n_nights: 30, dates: [D1, D2, D3, D4], recommended_plan: "smaller_batches", missing: [],
  entries: Object.fromEntries([
    ...[D1, D2, D3, D4].map((d) => [`${d}|baseline|`, entry(d, null, 800, `b-${d}`)]),
    ...[D1, D2, D3, D4].map((d) => [`${d}|smaller_batches|`, entry(d, "smaller_batches", 780, `s-${d}`)]),
    [`${D2}|forecast_reorder|`, entry(D2, "forecast_reorder", 700, "f")],
  ]),
};

describe("waste outlook: the plan each coming night uses", () => {
  it("draws adopted plans (bundle preset or an API run such as Sam's), else the recommended plan", async () => {
    const adopted = {
      [D2]: { preset: "forecast_reorder", lever: "" },                                   // preset adopted in the Lab
      [D3]: { preset: null, lever: "api", hash: "sam1", label: "Sam's plan: cut waste" }, // Sam's plan, run on the API
      [D4]: { preset: null, lever: "api", hash: "gone", label: "Lab edit" },             // API plan, API down
    };
    const load = async (h: string) => {
      if (h === "sam1") return { waste_lb: { ...entry(D3, null, 600, "sam1") } };
      throw new Error("API down");
    };
    const n = await resolveNights(o, adopted, load);
    expect(n.map((x) => [x.source, x.status, x.plan?.lb.p50])).toEqual([
      ["recommended", "ok", 780], ["adopted", "ok", 700], ["adopted", "ok", 600], ["adopted", "needs-api", undefined],
    ]);
    expect(n[2].label).toBe("Sam's plan: cut waste");
    const t = totals(n);
    expect(t.nights).toBe(3);
    expect(t.adopted).toBe(3);
    expect(t.savedLb).toBe(800 * 3 - (780 + 700 + 600));
  });

  it("uses the bundle's numbers when an API-run plan is a bundled scenario", async () => {
    const n = await resolveNights(o, { [D1]: { preset: null, lever: "api", hash: "s-2026-11-13", label: "x" } },
      async () => { throw new Error("should not be called"); });
    expect(n[0]).toMatchObject({ source: "adopted", status: "ok" });
    expect(n[0].plan?.lb.p50).toBe(780);
  });

  it("flags an adopted scenario the outlook doesn't have", async () => {
    const n = await resolveNights(o, { [D1]: { preset: "trim_slow", lever: "" } });
    expect(n[0]).toMatchObject({ source: "adopted", status: "missing" });
  });
});
