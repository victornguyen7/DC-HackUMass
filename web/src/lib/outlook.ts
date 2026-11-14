// Future waste (engine outlook.py): the coming menu dates, simulated, in the recorded history's units.
// Each night shows the plan adopted for it (Lab, preset or Ask Sam), else the recommended plan,
// against tonight's plan as written (the baseline).

import { api, apiUp, entryKey, fetchJSON, loadApiResult } from "./data";
import type { Range, WasteSummary } from "./types";
import type { Scenario } from "./store";

export type OutlookEntry = WasteSummary & { date: string; preset: string | null; lever: string; label: string; config_hash: string };
export type Outlook = {
  kind: "waste_outlook"; generated_at: string; n_nights: number; dates: string[]; recommended_plan: string;
  entries: Record<string, OutlookEntry>; missing: string[];
};

export type NightPlan = {
  date: string;
  baseline?: OutlookEntry;
  /** the plan this night is drawn with; undefined when its numbers can't be loaded */
  plan?: WasteSummary;
  label: string;
  source: "adopted" | "recommended";
  /** ok, or why there are no numbers: an API plan with the API down, or a scenario the outlook lacks */
  status: "ok" | "needs-api" | "missing";
  scenario?: Scenario;
};

/** The bundle's copy (written by `dcsim bundle --waste-only`), else the live API. */
export async function loadOutlook(): Promise<{ data: Outlook; source: "bundle" | "api" }> {
  try {
    return { data: await fetchJSON<Outlook>("waste/outlook.json"), source: "bundle" };
  } catch {
    if (await apiUp()) return { data: await api<Outlook>("waste/outlook"), source: "api" };
    throw new Error("No waste outlook in the demo bundle yet. Run `cd engine && uv run dcsim bundle --waste-only` (seconds), or start the API (`make serve`).");
  }
}

/** Which plan each coming night uses, and its simulated waste. */
export async function resolveNights(o: Outlook, adopted: Record<string, Scenario>,
  loadResult: (hash: string) => Promise<{ waste_lb?: WasteSummary | null }> = loadApiResult): Promise<NightPlan[]> {
  const byHash = new Map(Object.values(o.entries).map((e) => [e.config_hash, e]));
  return Promise.all(o.dates.map(async (date): Promise<NightPlan> => {
    const baseline = o.entries[entryKey(date, null, "")];
    const sc = adopted[date];
    if (!sc) {
      const rec = o.entries[entryKey(date, o.recommended_plan, "")];
      return { date, baseline, plan: rec, label: rec?.label ?? o.recommended_plan, source: "recommended", status: rec ? "ok" : "missing" };
    }
    const label = sc.label ?? (sc.preset ? sc.preset.replace(/_/g, " ") : "baseline");
    if (sc.hash) { // run on the API (Lab edits, Ask Sam), unless it happens to be a bundled scenario
      const known = byHash.get(sc.hash);
      if (known) return { date, baseline, plan: known, label, source: "adopted", status: "ok", scenario: sc };
      try {
        const r = await loadResult(sc.hash);
        if (r.waste_lb) return { date, baseline, plan: r.waste_lb, label, source: "adopted", status: "ok", scenario: sc };
      } catch { /* API down and no saved copy */ }
      return { date, baseline, label, source: "adopted", status: "needs-api", scenario: sc };
    }
    const e = o.entries[entryKey(date, sc.preset, sc.lever)];
    return { date, baseline, plan: e, label, source: "adopted", status: e ? "ok" : "missing", scenario: sc };
  }));
}

/** Sum of per-night medians (a typical week of these nights), and the saving against the baseline. */
export function totals(nights: NightPlan[]) {
  const ok = nights.filter((n) => n.status === "ok" && n.plan && n.baseline);
  const sum = (f: (n: NightPlan) => number) => ok.reduce((a, n) => a + f(n), 0);
  const planLb = sum((n) => n.plan!.lb.p50), baseLb = sum((n) => n.baseline!.lb.p50);
  return {
    nights: ok.length, planLb, baseLb, savedLb: baseLb - planLb,
    savedUsd: sum((n) => n.baseline!.usd.p50 - n.plan!.usd.p50),
    stockoutChange: sum((n) => n.plan!.stockout_min.p50 - n.baseline!.stockout_min.p50),
    adopted: nights.filter((n) => n.source === "adopted").length,
  };
}

export const mid = (r: Range) => r.p50;
