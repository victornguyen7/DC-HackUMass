import { describe, expect, it } from "vitest";
import { AGENT_IDLE, agentReducer, type AgentEvent } from "./agent";

const check = (met: boolean) => ({ met, checks: [{ name: "waste", ok: met, text: "Waste" }], waste_pct: -12, protected_worse: [] });

describe("agentReducer", () => {
  it("folds a run's events into tries and a final plan", () => {
    const evs: AgentEvent[] = [
      { event: "start", data: { goal_text: "cut waste", model: "m", provider: "gemini", max_tries: 8, n_nights: 30 } },
      { event: "goal", data: { goal: { summary: "Cut waste" }, source: "llm", protected: [], baseline: { waste_total: 3000 } } },
      { event: "try_start", data: { id: 1, note: "try it", moves: ["a"] } },
      { event: "progress", data: { what: "try 1", done: 8, total: 30 } },
      { event: "try", data: { id: 1, note: "try it", moves: ["a"], check: check(false) } },
      { event: "notice", data: { text: "heads up" } },
    ];
    const s = evs.reduce(agentReducer, AGENT_IDLE);
    expect(s.status).toBe("running");
    expect(s.tries).toHaveLength(1);
    expect(s.tries[0].check?.met).toBe(false);
    expect(s.progress).toBeUndefined();
    expect(s.notices).toEqual(["heads up"]);
    const done = agentReducer(s, { event: "final", data: { chosen: 1, tries: [{ ...s.tries[0], check: check(false) }] } });
    expect(done.status).toBe("done");
    expect(done.final?.chosen).toBe(1);
  });

  it("starts over when a reconnecting stream replays from the start", () => {
    let s = agentReducer(AGENT_IDLE, { event: "start", data: {} });
    s = agentReducer(s, { event: "try_start", data: { id: 1, note: "", moves: [] } });
    s = agentReducer(s, { event: "start", data: {} });
    expect(s.tries).toEqual([]);
  });

  it("records errors", () => {
    const s = agentReducer(AGENT_IDLE, { event: "error", data: { message: "boom" } });
    expect(s.status).toBe("error");
    expect(s.error).toBe("boom");
  });
});

describe("Sam's history (browser copy)", () => {
  it("keeps only the final analysis, labeled with prompt and date, newest first", async () => {
    const { rememberLocal, listHistory, openHistory, deleteHistory } = await import("./agent");
    localStorage.clear();
    const base = { agent: "Sam", goal: {}, date: "2026-11-13", chosen: 2, chosen_by: "agent", reasoning: "r", caveats: "",
      check: { met: false, checks: [], waste_pct: -18.4, protected_worse: [] }, holdout: {}, request: {}, config_hash: "h",
      baseline_hash: "b", label: "l", moves: [], changed_dishes: [], notices: [], source: "llm", model: "m", nights_simulated: 1 };
    rememberLocal({ ...base, goal_text: "cut waste 20%", history_id: "a1", created_at: "2026-11-14T10:00:00Z",
      tries: [{ id: 1 }, { id: 2 }], settings: ["x"] } as never);
    rememberLocal({ ...base, kind: "question", goal_text: "Which dishes ran out?", answer: "**Rice** ran out once.",
      history_id: "a2", created_at: "2026-11-14T11:00:00Z", tries: [] } as never);
    const items = await listHistory(false);
    expect(items.map((x) => x.prompt)).toEqual(["Which dishes ran out?", "cut waste 20%"]);
    expect(items[0].headline).toBe("Rice ran out once.");
    expect(items[1]).toMatchObject({ for_date: "2026-11-13", met: false, local: true });
    expect(items[1].headline).toBe("Closest plan · waste -18.4% · try 2 of 2");
    const f = await openHistory("a1", false);
    expect(f.tries).toEqual([]);
    expect(f.tries_run).toBe(2);
    await deleteHistory("a1", false);
    expect((await listHistory(false)).map((x) => x.run_id)).toEqual(["a2"]);
  });
});

describe("what-ifs in the Ask Sam box", () => {
  it("keeps the settings Sam read, live and in the browser's history copy", async () => {
    const { agentReducer, AGENT_IDLE, rememberLocal, openHistory, listHistory } = await import("./agent");
    let s = agentReducer(AGENT_IDLE, { event: "start", data: {} });
    s = agentReducer(s, { event: "whatif", data: { ok: true, settings: ["Grill Station servers: 2 -> 3"], explanation: "", unsupported: [], method: "function_calling" } });
    expect(s.whatif?.settings).toEqual(["Grill Station servers: 2 -> 3"]);
    localStorage.clear();
    rememberLocal({ kind: "whatif", agent: "Sam", goal_text: "what if one more Grill server", goal: {}, date: "2026-11-13", history_id: "w1",
      created_at: "2026-11-14T12:00:00Z", settings: ["Grill Station servers: 2 -> 3"], tries: [],
      summary: "Against tonight's plan: waste -3.0% (same), missed meals better.", ok: true } as never);
    const [item] = await listHistory(false);
    expect(item).toMatchObject({ kind: "whatif", met: null, headline: "waste -3.0% (same), missed meals better." });
    expect((await openHistory("w1", false)).settings).toEqual(["Grill Station servers: 2 -> 3"]);
  });
});
