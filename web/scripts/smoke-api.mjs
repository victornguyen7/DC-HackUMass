// Browser test for the features that need the engine API (`make server`):
// plain-English box, menu edits + new dish, API runs, Why?, API replay + narration, adopt.
// Ask Sam: a question answered from Tiger, a plan, the history panel.
// Usage: node scripts/smoke-api.mjs [baseUrl] [outDir] [--allow-ledger]
// Run it with `make web-test-api` (needs `make server-test` and `make web-test-ui`): that API has ledger writes off
// and its own cache, so the test never logs forecasts to the append-only Tiger ledger or fills the real store.
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const args = process.argv.slice(2).filter((a) => !a.startsWith("--"));
const allowLedger = process.argv.includes("--allow-ledger");
const base = args[0] ?? "http://localhost:5174";
const out = args[1] ?? "smoke-api-out";
mkdirSync(out, { recursive: true });

const health = await (await fetch(`${base}/api/health`)).json();
if (!health.ok) {
  console.log("API not running; start it with `make server-test` (and `make web-test-ui`).");
  process.exit(2);
}
if (health.ledger_writes !== false && !allowLedger) {
  // Adopting and saving below would log forecasts to the append-only Tiger ledger (they can't be deleted).
  console.log("This API writes forecasts to the Tiger ledger. Test against `make server-test` (ledger writes off),\n" +
    "or pass --allow-ledger if you really want test forecasts in the ledger.");
  process.exit(3);
}

const browser = await chromium.launch({ channel: "chrome", headless: true });
const problems = [];
const p = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
p.on("console", (m) => m.type() === "error" && problems.push(`[console] ${p.url()} ${m.text()} ${m.location()?.url ?? ""}`));
p.on("pageerror", (e) => problems.push(`[pageerror] ${p.url()} ${e.message}`));
p.on("response", (r) => r.status() >= 400 && problems.push(`[http ${r.status()}] ${r.url()}`));
const step = async (name, fn) => {
  const t0 = Date.now();
  try { await fn(); console.log("ok  ", name, `${((Date.now() - t0) / 1000).toFixed(1)}s`); }
  catch (e) { problems.push(`[step] ${name}: ${e.message.split("\n")[0]}`); console.log("FAIL", name); await p.screenshot({ path: `${out}/fail-${name.replace(/\W+/g, "_")}.png`, fullPage: true }); }
};

await p.goto(`${base}/lab`);
await p.evaluate(() => localStorage.clear());
await p.goto(`${base}/lab`);
await p.getByText("Results vs. baseline").waitFor({ timeout: 20000 });

await step("ask box → apply → API run", async () => {
  await p.getByLabel("Ask in plain English").fill("add one more server at the Grill and 10% busier");
  await p.getByRole("button", { name: "Ask", exact: true }).click();
  await p.getByRole("button", { name: "Apply and run" }).waitFor({ timeout: 60000 });
  await p.screenshot({ path: `${out}/lab-ask.png`, fullPage: true });
  await p.getByRole("button", { name: "Apply and run" }).click();
  await p.getByText("simulated on the API").waitFor({ timeout: 120000 });
});

await step("why?", async () => {
  await p.getByRole("button", { name: "Why?" }).click();
  await p.getByText(/^Why ·/).waitFor({ timeout: 60000 });
});

await step("menu: remove + new dish → run → demand shift", async () => {
  await p.getByRole("button", { name: "Preset" }).click();
  await p.getByRole("button", { name: "Smaller batches of favorites" }).click();
  await p.getByText("smaller batches of favorites").first().waitFor();
  await p.getByRole("button", { name: /^Menu/ }).click();
  await p.getByLabel("Dish to remove").selectOption({ index: 1 });
  await p.getByRole("button", { name: "Remove", exact: true }).click();
  await p.getByRole("button", { name: "Add a new dish…" }).click();
  await p.getByLabel("Name").fill("Butter Chicken Bowl");
  await p.locator('[aria-label="New dish"] select').first().selectOption("tandoor");
  await p.getByRole("button", { name: "suggest" }).click();
  await p.locator('[aria-label="New dish"] .chip').first().waitFor({ timeout: 30000 });
  await p.getByRole("button", { name: "AI estimate" }).click();
  await p.getByText(/Likes it most/).waitFor({ timeout: 60000 });
  await p.screenshot({ path: `${out}/lab-new-dish.png`, fullPage: true });
  await p.getByRole("button", { name: "Add to scenario" }).click();
  await p.getByRole("button", { name: "Run 30 nights" }).click();
  await p.getByText("Where demand moves").waitFor({ timeout: 180000 });
  await p.screenshot({ path: `${out}/lab-menu-run.png`, fullPage: true });
});

await step("adopt API run → dashboard", async () => {
  await p.getByRole("button", { name: /^Adopt for/ }).click();
  await p.getByText(/Adopted/).first().waitFor();
  await p.goto(`${base}/dashboard`);
  await p.locator(".kitchen-model-note", { hasText: "Adopted plan" }).first().waitFor({ timeout: 20000 });
  const chip = await p.locator(".kitchen-model-note", { hasText: "Adopted plan" }).first().textContent();
  if (!/Butter Chicken|new dish|add/i.test(chip ?? "")) problems.push(`[check] dashboard chip doesn't name the API run: ${chip}`);
  await p.screenshot({ path: `${out}/dashboard-adopted-api.png` });
});

await step("saved list shows API scenarios", async () => {
  await p.goto(`${base}/lab`);
  await p.getByText("Results vs. baseline").waitFor({ timeout: 20000 });
  await p.getByRole("button", { name: "Saved", exact: true }).click();
  await p.getByText("· API").first().waitFor({ timeout: 10000 });
});

await step("watch API run in Hall (API replay + narration)", async () => {
  await p.getByRole("button", { name: "Saved", exact: true }).click();
  await p.getByText("· API").first().click();
  await p.getByText("simulated on the API").waitFor({ timeout: 30000 });
  await p.getByRole("button", { name: "Watch replay" }).click();
  await p.waitForURL(/\/hall/);
  await p.getByRole("img", { name: /^Isometric hall replay/ }).waitFor({ timeout: 120000 });
  await p.getByText("Student stories", { exact: true }).click();
  await p.locator(".student-directory .link").first().click();
  await p.getByRole("button", { name: "Top-down view" }).click();
  await p.locator(".narration").first().waitFor({ timeout: 90000 });
  await p.screenshot({ path: `${out}/hall-api.png`, fullPage: true });
});

await step("narrate a non-highlight student", async () => {
  await p.locator("g.student").nth(5).click({ force: true });
  const btn = p.getByRole("button", { name: "Narrate this student" });
  if (await btn.isVisible().catch(() => false)) {
    await btn.click();
    await p.locator(".narration").first().waitFor({ timeout: 90000 });
  }
});

await step("bundle lever-grid scenario replays from the API", async () => {
  await p.goto(`${base}/lab`);
  await p.getByText("Results vs. baseline").waitFor({ timeout: 20000 });
  await p.getByRole("button", { name: "Baseline", exact: true }).click();
  await p.getByRole("button", { name: /^Crowd/ }).click();
  await p.locator('input[type="range"]').first().fill("1.1");
  await p.getByRole("button", { name: "Run 30 nights" }).click();
  await p.getByText("from the offline bundle").waitFor();
  await p.getByRole("button", { name: "Watch replay" }).click();
  await p.waitForURL(/\/hall/);
  await Promise.race([
    p.getByRole("img", { name: /^Isometric hall replay/ }).waitFor({ timeout: 120000 }),
    p.getByText(/couldn't replay/).waitFor({ timeout: 120000 }).then(() => { throw new Error("API couldn't replay the lever-grid hash"); }),
  ]);
});

await step("Ask Sam: question from Tiger", async () => {
  await p.goto(`${base}/sam`);
  await p.locator("#sam-goal").fill("Which dishes ran out on rainy Fridays?");
  await p.getByRole("button", { name: "Ask Sam" }).click();
  await p.getByRole("region", { name: "Sam's answer" }).waitFor({ timeout: 180000 });
  await p.screenshot({ path: `${out}/sam-question.png`, fullPage: true });
});

await step("Ask Sam: plan (at most 5 tries) + history", async () => {
  await p.locator("#sam-goal").fill("Cut waste 10% without longer lines or missed meals");
  await p.getByRole("button", { name: "Ask Sam" }).click();
  const plan = p.getByRole("region", { name: "Sam's plan" });
  await Promise.race([
    plan.waitFor({ timeout: 400000 }),
    p.locator(".alert.bad").waitFor({ timeout: 400000 }).then(async () => { throw new Error(await p.locator(".alert.bad").innerText()); }),
  ]);
  const tries = await p.locator(".sam-try").count();
  if (tries < 1 || tries > 5) throw new Error(`Sam ran ${tries} tries`);
  await p.screenshot({ path: `${out}/sam-plan.png`, fullPage: true });
  await p.locator(".sam-history-open").nth(1).click(); // the question, from the history panel
  await p.locator(".sam-viewing").waitFor();
  await p.getByRole("region", { name: "Sam's answer" }).waitFor();
  await p.getByRole("button", { name: "Back to the current run" }).click();
  await p.getByRole("button", { name: "Open in the Lab" }).click();
  await p.waitForURL(/\/lab/);
  await p.getByText("simulated on the API").waitFor({ timeout: 120000 });
});

await browser.close();
console.log(`\n${problems.length} problem(s)`);
for (const x of problems) console.log(x);
