import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Sam from "./Sam";
import { listHistory, openHistory, runAgent, type AgentEvent, type AgentFinal, type AgentTry, type Goal, type GoalCheck } from "../lib/agent";
import { useApp } from "../lib/store";
import { useApiUp } from "../lib/hooks";

vi.mock("../components/Shell", () => ({ TopBar: ({ title }: { title: string }) => <h1>{title}</h1> }));
vi.mock("../lib/hooks", async (original) => ({ ...await original<typeof import("../lib/hooks")>(), useApiUp: vi.fn(() => true) }));
vi.mock("../lib/agent", async (original) => ({ ...await original<typeof import("../lib/agent")>(),
  runAgent: vi.fn(), listHistory: vi.fn(), openHistory: vi.fn(), rememberLocal: vi.fn(), deleteHistory: vi.fn(),
}));

const goal: Goal = { kind: "plan", waste_cut_pct: 20, protect_diets: ["vegetarian"], protect_dishes: [], keep_missed_meals: true, keep_wait: true, summary: "Cut waste while keeping vegetarian dishes available", unsupported: [] };
const check: GoalCheck = { met: false, waste_pct: -25.8, checks: [
  { name: "waste", ok: true, text: "Waste −25.8% (goal −20%)" },
  { name: "protected", ok: false, text: "1 of 38 protected dishes runs out more than today" },
  { name: "missed_meal", ok: false, text: "Missed meals: worse (+5.5 students, median of paired nights)" },
], protected_worse: [{ dish_id: "curry", name: "Vegetable Curry", station: "tandoor", runout_share: [0.1, 0.4], empty_min_p50: [0, 4] }] };
const attempt: AgentTry = { id: 1, note: "Reduce batch_size and increase max_batches to protect availability.", moves: ["Smaller batches, with more refills"], settings: ["Vegetable Curry batch_size: 40 → 30"], config_hash: "sam-plan",
  check, waste: { now: 2480, baseline: 3326, median_change: -846, verdict: "better" }, missed_meal: { now: 236, baseline: 230, median_change: 5.5, verdict: "worse" }, wait_p90: { now: 16.3, baseline: 16, median_change: 0.2, verdict: "same" }, request: { date: "2026-11-14" } };
const final: AgentFinal = { kind: "plan", agent: "Sam", goal_text: "Cut waste 20% without any vegetarian dish running out", goal, date: "2026-11-14", chosen: 1, chosen_by: "agent", reasoning: "Smaller batches reduce waste, but availability still needs attention.", caveats: "Check vegetarian dishes before adopting this plan.", check,
  holdout: { ...check, seeds: [31, 60], waste_pct: -18 }, request: attempt.request, config_hash: "sam-plan", baseline_hash: "baseline", label: "Sam’s smaller batches", moves: attempt.moves, settings: attempt.settings,
  changed_dishes: [{ dish_id: "curry", name: "Vegetable Curry", station: "tandoor", period: "dinner", protected: true, diet_tags: ["vegetarian"], before: { batch_size: 40, starting_batches: 1, reorder_point: 10, max_batches: 8 }, after: { batch_size: 30, starting_batches: 1, reorder_point: 10, max_batches: 10 }, last_batch: "20:40", waste: [90, 50], runout_share: [0.1, 0.4] }],
  tries: [attempt], notices: [], source: "mock", model: null, nights_simulated: 120, history_id: "plan-1", created_at: "2026-11-14T21:00:00Z" };
let event: (e: AgentEvent) => void;
const stop = vi.fn();
const originalShowModal = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, "showModal");

beforeEach(() => {
  vi.clearAllMocks();
  useApp.setState({ date: "2026-11-14", scenario: { preset: null, lever: "" }, adopted: {} });
  vi.mocked(useApiUp).mockReturnValue(true);
  vi.mocked(listHistory).mockResolvedValue([]);
  vi.mocked(runAgent).mockImplementation(async (_goal, _date, onEvent) => { event = onEvent; return stop; });
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: function (this: HTMLDialogElement) { this.setAttribute("open", ""); } });
});
afterEach(() => {
  cleanup(); vi.restoreAllMocks();
  if (originalShowModal) Object.defineProperty(HTMLDialogElement.prototype, "showModal", originalShowModal);
  else Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");
});
const mount = () => render(<MemoryRouter initialEntries={["/sam"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}><Routes><Route path="/sam" element={<Sam />} /><Route path="/hall" element={<h1>Hall replay</h1>} /></Routes></MemoryRouter>);
const submit = async () => {
  fireEvent.change(screen.getByRole("textbox", { name: "Message Sam" }), { target: { value: final.goal_text } });
  fireEvent.click(screen.getByRole("button", { name: "Ask Sam" }));
  await waitFor(() => expect(runAgent).toHaveBeenCalledTimes(1));
};

it("uses the supplied artwork and lets users edit a suggestion before starting a run", async () => {
  mount();
  expect(screen.getByRole("img", { name: "Sam the Minuteman" }).getAttribute("src")).toBe("/sam-avatar.png");
  expect(screen.queryByRole("region", { name: "Sam's history" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Waste a little less/ }));
  const message = screen.getByRole("textbox", { name: "Message Sam" }) as HTMLTextAreaElement;
  expect(message.value).toBe(final.goal_text);
  expect(document.activeElement).toBe(message);
  expect(runAgent).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Ask Sam" }));
  await waitFor(() => expect(runAgent).toHaveBeenCalledWith(final.goal_text, "2026-11-14", expect.any(Function)));
  expect(screen.getByText(final.goal_text)).toBeTruthy();
  expect((screen.getByRole("button", { name: "Working…" }) as HTMLButtonElement).disabled).toBe(true);
});

it("keeps attempts compact, leads with the final recommendation, and leaves unmet goals visible", async () => {
  mount(); await submit();
  act(() => {
    event({ event: "goal", data: { goal, protected: [{ dish_id: "curry", name: "Vegetable Curry" }], baseline: { waste_total: 3326, missed_meal: 230, wait_p90: 16 } } });
    event({ event: "try_start", data: attempt }); event({ event: "try", data: attempt });
  });
  const attemptDetails = screen.getByText("Attempt 1").closest("details")!;
  expect(attemptDetails.open).toBe(false);
  expect(screen.getByText("Waste down 25.8% · 2 checks need attention")).toBeTruthy();
  fireEvent.click(screen.getByText("Attempt 1"));
  expect(attemptDetails.open).toBe(true);
  expect(within(attemptDetails).getByText("Reduce batch size and increase extra batches to protect availability.")).toBeTruthy();
  await act(async () => event({ event: "final", data: final }));
  const plan = screen.getByRole("region", { name: "Sam's plan" });
  expect(within(plan).getByRole("heading", { name: "Here’s the closest plan" })).toBeTruthy();
  expect(within(plan).getByText("Still needs attention")).toBeTruthy();
  expect(within(plan).getByText("Some goals didn’t hold on 30 extra nights")).toBeTruthy();
  const testDetails = screen.getByText("How Sam tested this").closest("details")!;
  expect(testDetails.open).toBe(false);
  expect(plan.compareDocumentPosition(testDetails) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const kitchen = screen.getByText("Kitchen changes").closest("details")!;
  expect(kitchen.open).toBe(false);
  fireEvent.click(screen.getByText("Kitchen changes"));
  expect(kitchen.open).toBe(true);
  expect(within(kitchen).getByText("Vegetable Curry")).toBeTruthy();
});

it("opens saved chats offline and watches a historical plan on its own date", async () => {
  vi.mocked(useApiUp).mockReturnValue(false);
  vi.mocked(listHistory).mockResolvedValue([{ run_id: "plan-1", kind: "plan", prompt: final.goal_text, for_date: final.date, created_at: final.created_at!, met: false, headline: "Closest plan · waste −25.8%" }]);
  vi.mocked(openHistory).mockResolvedValue({ ...final, tries: [], tries_run: 1 });
  useApp.setState({ date: "2026-11-15" });
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "Recent chats 1" }));
  const dialog = screen.getByRole("dialog", { name: "Recent chats" });
  fireEvent.click(within(dialog).getByRole("button", { name: /^Plan for/ }));
  await screen.findByRole("region", { name: "Sam's plan" });
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByText("−25.8%")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Watch a night" }));
  expect(screen.getByRole("heading", { name: "Hall replay" })).toBeTruthy();
  expect(useApp.getState().date).toBe(final.date);
  expect(useApp.getState().scenario.hash).toBe(final.config_hash);
});

it("shows what-if outcomes while keeping settings and the extra metric behind details", async () => {
  mount(); await submit();
  await act(async () => event({ event: "final", data: { ...final, kind: "whatif", explanation: "One extra server shortens waits at the Grill.", metrics: { waste_total: attempt.waste, missed_meal: attempt.missed_meal, wait_p90: attempt.wait_p90, stockout_min_total: { baseline: 20, now: 18, median_change: -2, verdict: "better" } }, settings: ["Grill servers: 2 → 3"], tries: [] } }));
  const whatif = screen.getByRole("region", { name: "Sam's what-if" });
  expect(within(whatif).getByRole("heading", { name: "Here’s what changes" })).toBeTruthy();
  expect(within(whatif).getByRole("button", { name: "Adopt this what-if" })).toBeTruthy();
  const details = within(whatif).getByText("Changes & simulation details").closest("details")!;
  expect(details.open).toBe(false);
  fireEvent.click(within(whatif).getByText("Changes & simulation details"));
  expect(details.open).toBe(true);
  expect(within(details).getByText("Grill servers: 2 → 3")).toBeTruthy();
  expect(within(details).getByText("Empty-pan time across dishes")).toBeTruthy();
});

it("keeps a history answer readable and recovers the composer after a failed request", async () => {
  mount(); await submit();
  await act(async () => event({ event: "final", data: { ...final, kind: "question", answer: "**Rice** ran out on two nights.\n\nThe recorded history is synthetic.", tries: [] } }));
  const answer = screen.getByRole("region", { name: "Sam's answer" });
  expect(within(answer).getByText("Rice ran out on two nights.")).toBeTruthy();
  expect(within(answer).getByText("The recorded history is synthetic.")).toBeTruthy();
  expect(within(answer).queryByRole("button", { name: /Adopt/ })).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Message Sam" }), { target: { value: "Try smaller batches" } });
  fireEvent.click(screen.getByRole("button", { name: "Ask Sam" }));
  await act(async () => event({ event: "error", data: { message: "The planning service disconnected." } }));
  expect(screen.getByRole("alert").textContent).toContain("The planning service disconnected.");
  expect((screen.getByRole("textbox", { name: "Message Sam" }) as HTMLTextAreaElement).disabled).toBe(false);
  expect((screen.getByRole("textbox", { name: "Message Sam" }) as HTMLTextAreaElement).value).toBe("Try smaller batches");
  expect(screen.queryByRole("region", { name: "Sam's answer" })).toBeNull();
});
