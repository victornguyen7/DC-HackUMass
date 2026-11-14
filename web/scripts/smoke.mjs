// Browser smoke test: opens each page in headless Chrome, records console errors,
// runs a few interactions and saves screenshots. Usage: node scripts/smoke.mjs [baseUrl] [outDir]
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const base = process.argv[2] ?? "http://localhost:5173";
const out = process.argv[3] ?? "smoke-out";
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ channel: "chrome", headless: true });
const problems = [];
async function page(width, height) {
  const p = await browser.newPage({ viewport: { width, height } });
  p.on("console", (m) => m.type() === "error" && !m.text().includes("status of") && problems.push(`[console] ${p.url()} ${m.text()}`));
  p.on("pageerror", (e) => problems.push(`[pageerror] ${p.url()} ${e.message}`));
  p.on("response", (r) => r.status() >= 400 && !r.url().includes("/api/") && problems.push(`[http ${r.status()}] ${r.url()}`));
  p.on("requestfailed", (r) => problems.push(`[request] ${r.url()} ${r.failure()?.errorText}`));
  return p;
}
const step = async (name, fn) => {
  try { await fn(); console.log("ok  ", name); } catch (e) { problems.push(`[step] ${name}: ${e.message.split("\n")[0]}`); console.log("FAIL", name); }
};

for (const [w, h, tag] of [[1440, 1024, "desktop"], [1024, 1300, "tablet"], [390, 844, "phone"]]) {
  const p = await page(w, h);
  await step(`${tag} hall loads`, async () => {
    await p.goto(`${base}/hall`);
    await p.getByRole("img", { name: /^Isometric hall replay/ }).waitFor({ timeout: 20000 });
    await p.getByRole("button", { name: "Inspect Latino" }).waitFor({ timeout: 20000 });
    await p.screenshot({ path: `${out}/hall-${tag}.png`, fullPage: true });
  });
  if (tag === "desktop") {
    await step("hall play 2s", async () => {
      const before = await p.getByTestId("playback-clock").textContent();
      await p.getByRole("button", { name: "Play", exact: true }).click();
      await p.waitForTimeout(2000);
      await p.getByRole("button", { name: "Pause", exact: true }).click();
      const after = await p.getByTestId("playback-clock").textContent();
      if (before === after) throw new Error(`clock did not advance (${before})`);
    });
    await step("hall color by wait", async () => { await p.getByLabel("Color by").selectOption("wait"); });
    await step("hall station click", async () => {
      await p.getByRole("button", { name: /^Grill:/ }).click();
      await p.getByRole("heading", { name: "Grill" }).waitFor();
    });
    await step("hall camera views", async () => {
      await p.getByRole("button", { name: "Top-down view" }).click();
      await p.getByRole("img", { name: /^Top-down plan/ }).waitFor();
      await p.getByRole("button", { name: "Isometric view" }).click();
    });
    await step("hall scrub keyboard", async () => {
      await p.getByRole("slider", { name: "Playback time" }).focus();
      await p.keyboard.press("ArrowRight");
    });
  }
  for (const [path, text] of [["dashboard", "Production plan"], ["waste", "Adopted-plan tracker"], ["lab", "Results vs. baseline"]]) {
    await step(`${tag} ${path} loads`, async () => {
      await p.goto(`${base}/${path}`);
      await p.getByText(text).first().waitFor({ timeout: 20000 });
      await p.waitForTimeout(400);
      await p.screenshot({ path: `${out}/${path}-${tag}.png`, fullPage: true });
    });
  }
  if (tag === "desktop") {
    await step("lab preset → trim slow", async () => {
      await p.getByRole("button", { name: "Trim slow sellers" }).click();
      await p.getByText("trim slow sellers").first().waitFor({ timeout: 10000 });
    });
    await step("lab crowd lever (cached)", async () => {
      await p.getByRole("button", { name: "Baseline", exact: true }).click();
      await p.getByRole("button", { name: /^Crowd/ }).click();
      const s = p.locator('input[type="range"]').first();
      await s.fill("1.1");
      await p.getByRole("button", { name: "Run 30 nights" }).click();
      await p.getByText(/vs/).first().waitFor();
    });
    await step("lab adopt → dashboard shows it", async () => {
      await p.getByRole("button", { name: "Smaller batches of favorites" }).click().catch(() => {});
      await p.getByRole("button", { name: "Preset" }).click();
      await p.getByRole("button", { name: "Smaller batches of favorites" }).click();
      await p.getByText("smaller batches of favorites").first().waitFor();
      await p.getByRole("button", { name: /^Adopt for/ }).click();
      await p.goto(`${base}/dashboard`);
      await p.locator(".kitchen-model-note", { hasText: "Adopted plan" }).first().waitFor({ timeout: 10000 });
    });
    await step("lab watch replay", async () => {
      await p.goto(`${base}/lab`);
      await p.getByText("Results vs. baseline").waitFor();
      await p.getByRole("button", { name: "Watch replay" }).click();
      await p.waitForURL(/\/hall/);
      await p.getByRole("img", { name: /^Isometric hall replay/ }).waitFor({ timeout: 20000 });
    });
    await step("dashboard date switch", async () => {
      await p.goto(`${base}/dashboard`);
      await p.getByLabel("Date").selectOption("2026-11-18");
      await p.getByLabel("Date").locator("option:checked", { hasText: "Friendsgiving" }).waitFor({ state: "attached", timeout: 10000 });
      await p.screenshot({ path: `${out}/dashboard-nov18.png`, fullPage: true });
    });
    await step("hall other date baseline", async () => {
      await p.goto(`${base}/hall`);
      await p.getByRole("button", { name: "Inspect Latino" }).waitFor({ timeout: 20000 });
    });
  }
  if (tag === "phone") {
    for (const path of ["hall", "dashboard", "waste", "lab"]) {
      await p.goto(`${base}/${path}`);
      await p.waitForTimeout(1500);
      const sw = await p.evaluate(() => document.documentElement.scrollWidth);
      if (sw > 391) problems.push(`[layout] phone ${path}: page is ${sw}px wide (horizontal scroll)`);
    }
  }
  await p.close();
}
await browser.close();
console.log(`\n${problems.length} problem(s)`);
for (const x of problems) console.log(x);
