// Shared scenario context (SPEC §2): date, meal, active scenario, adopted plans.
import { create } from "zustand";

/** A bundled scenario is (preset, lever). A scenario simulated on the API has
 * lever "api" plus its config hash and label (UI_ISSUES U8). */
export type Scenario = { preset: string | null; lever: string; hash?: string; label?: string };

const DEFAULT_DATE = "2026-11-13";
function readDate(): string {
  try {
    const d = localStorage.getItem("dcwhatif.date");
    return d && /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : DEFAULT_DATE;
  } catch {
    return DEFAULT_DATE;
  }
}

type State = {
  date: string;
  meal: "dinner";
  /** Scenario shown in the Hall and selected in the What-If Lab. */
  scenario: Scenario;
  /** date -> scenario adopted for that date (Dashboard shows it). */
  adopted: Record<string, Scenario>;
  /** Hall playback time (minutes since midnight), so other pages can jump there. */
  hallTime: number | null;
  setDate: (d: string) => void;
  setScenario: (s: Scenario) => void;
  adopt: (date: string, s: Scenario) => void;
  setHallTime: (t: number | null) => void;
};

function readAdopted(): Record<string, Scenario> {
  try {
    return JSON.parse(localStorage.getItem("dcwhatif.adopted") ?? "{}");
  } catch {
    return {};
  }
}

export const useApp = create<State>((set, get) => ({
  date: readDate(),
  meal: "dinner",
  scenario: { preset: null, lever: "" },
  adopted: readAdopted(),
  hallTime: null,
  setDate: (date) => {
    try { localStorage.setItem("dcwhatif.date", date); } catch { /* keep in memory */ }
    set({ date, scenario: { preset: null, lever: "" } });
  },
  setScenario: (scenario) => set({ scenario }),
  adopt: (date, s) => {
    const adopted = { ...get().adopted, [date]: s };
    try {
      localStorage.setItem("dcwhatif.adopted", JSON.stringify(adopted));
    } catch {
      /* private mode: keep in memory only */
    }
    set({ adopted });
  },
  setHallTime: (hallTime) => set({ hallTime }),
}));

export const BASELINE: Scenario = { preset: null, lever: "" };
