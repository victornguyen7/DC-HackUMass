# Project status: what's done, what's open

One page that rolls up every tracker. The details stay in each doc; this page only says **done / partly done / open** and where to look.
Update it whenever you close something, in the same commit as the fix.

Last updated: 2026-11-14, engine 0.6.2, `main` at `8e7d9e0` + this docs pass: Ask Sam (5-try cap, Tiger lookups through the Tiger MCP server, history, setting functions for staffing and menu changes, what-ifs in the Sam box), Gemini function calling for the plain-English box, the waste outlook (Insights opens on the next 8 nights), Tiger track record, ledger-safe test stack, `make serve` → `make server`, image input and breakfast/lunch out of scope.

**Markers:** ✅ done (fixed / closed / built) · ◐ partly done · ✗ open · ⚠️ done but **not committed yet**

## Before the demo
- ✅ **Demo bundle rebuilt on engine 0.6.2** (2026-11-14 04:05 UTC: 264 results, 14 replays, 168 Gemini narrations, 22 MB; about 17 min). Rebuild again only if the engine or data changes: `make demo-bundle`, or `LLM_PROVIDER=mock make demo-bundle` for template text in a few minutes.
- ✅ **Full test run after the rebuild passed:** engine 149 tests, web 46 tests, browser smoke 23/23 offline and 8/8 with the API, 44 deep offline checks, Similar nights 6/6. Table in [UI_ISSUES.md](UI_ISSUES.md#full-test-run-after-the-062-bundle-2026-11-13).
- ✅ **Tests after the Tiger dashboard work (2026-11-14):** engine 161 passed, 2 skipped; web 46 passed; typecheck clean; the dashboard opened live from the API with no console errors. The browser smoke script passed 27/27 offline with the dashboard steps; those steps were then removed with the page, so it is back to the earlier 23.
- ✅ **Tests after Sam and the Tiger track record were merged (`main` at `8ef0de6`, 2026-11-14):** engine 179 passed, 2 skipped; web 50 passed; typecheck clean; production build OK; browser smoke 23/23 offline and 10/10 with the API (`make web-test-api`, now including Ask Sam: a question from Tiger, a plan in at most 5 tries, the history panel, Open in the Lab).
- ✅ **Latest full test run (`main` at `8e7d9e0`, 2026-11-14):** engine 191 passed, 2 skipped (`make test`); web 55 passed in 11 files (`make web-test`); typecheck clean; production build OK; browser smoke 23/23 (`scripts/smoke.mjs`) and 10/10 with the API (`make web-test-api` on the test stack: ask → run, Why?, menu edits, adopt, saved list, Hall replay + narration, lever-grid replay, Ask Sam question and plan); 0 problems, no Tiger ledger writes. Also checked in the browser: Sam what-if, a staffing-allowed plan, the waste outlook following a Lab preset and a Sam plan.
- ✅ **Waste outlook (2026-11-14):** Insights opens on the next 8 nights, simulated with each night's adopted plan (Lab or Ask Sam) vs. tonight's plan, next to the recorded past; `waste_lb` on every result, `GET /api/waste/outlook`, `waste/outlook.json` in the bundle. Outlook flow checked in the browser (Lab preset and Sam plan adopted, both nights updated). `waste/outlook.json` is in the demo bundle (refresh it with `cd engine && uv run dcsim bundle --waste-only` after new runs); plans adopted from API runs need the API or the browser's saved copy.
- ✅ **Gemini function calling and Sam's setting functions (2026-11-14):** the plain-English box reads questions through 13 lever functions (JSON and keyword fallbacks; the Lab shows which answered); Sam's plans can call the decision functions (servers, shifts, cooks, service time, batches, remove a dish, presets; not crowd, arrivals or popularity), and the Ask Sam box also runs what-ifs. Details: SPONSORS.md §1–2.
- ✗ Restart `make server` if it was started before the Sam merge (`/api/agent`, `/api/similar-nights`, `/tiger` and `/api/tiger/*` → 404 otherwise; Ask Sam then says "Not Found").
- ⚠️ **Browser-test the API only on the test stack:** `make server-test` (port 8001, a fresh copy of the cache in `cache-test/`, `DCSIM_LEDGER_WRITES=0`) + `make web-test-ui` (port 5174), then `make web-test-api`. The script now refuses an API that would write test forecasts to the append-only Tiger ledger (ISSUES T8).
- ✗ Restart `make server` to pick up the single similar-night search (ISSUES T5, 2026-11-14); until then the page still uses the old 48-dim search, whose table was dropped, so it answers "temporarily unavailable".
- ✗ Restart `make server` to get **Optimize** and **Plausible worlds** in the Lab (IMPROVEMENTS 2.1–2.2, 2026-11-14; `/api/optimize` and `/api/worlds` → 404 on an older server). `uv sync` first: the engine now needs `optuna`.
- ✗ **Rotate the tokens in `.dev.vars`** (ISSUES T6). Removed from the branches' git history and force-pushed on 2026-11-14, but GitHub still serves the old commit through PRs #4–#6 until GitHub Support purges it, so the tokens must be treated as leaked. **Everyone else: re-clone, or `git fetch` then `git reset --hard origin/main`.**

## Recently built (2026-11-14)
- ✅ **Tier 2 (IMPROVEMENTS 2.1–2.2):** Optimize plan (Optuna, waste-vs-stockouts frontier, re-checked on unseen seeds; Nov 13: −28.6% waste for +5.8 stockout min, nothing better within today's stockouts) and Plausible worlds (crowd, rush timing and dish popularity drawn per seed; win rate + waste tornado: crowd size matters most → ask for R01). Tests: engine 208 passed, 2 skipped; web 61 passed; typecheck clean; browser flow (Optimize → Load into builder → Run → Plausible worlds) at 1440 and 390 px on a private API, no console errors, no horizontal scroll.
- ✅ Tiger: two real-time continuous aggregates (`waste_daily_by_dish`, `pan_hourly`) with refresh policies, `dcsim tsdb bench` (raw vs aggregate), `dcsim tsdb query stack` (one query across the stack), columnstore on all six hypertables (76 MB → 9.6 MB). Details: [TIGER.md §8](TIGER.md).
- ✅ Live Tiger dashboard, served by the API at `http://127.0.0.1:8000/tiger` only (not part of the web app).
- ✅ Tiger items 4–6 (2026-11-14): weekly track record (`forecast_results` hypertable, nightly `score_forecasts` job, real-time aggregate `forecast_weekly`, `dcsim tsdb track`); weather/event crowd fit (`dcsim calibrate --situations-only`; not kept, held-out CRPS 205.8 vs 200.0); schema comments and a verified question set for the Tiger MCP. TIGER.md §4–6.
- ✅ Tiger loader fills the engine 0.4+ columns and category tables (`1ab7e16`, `4a5d6fb`, `094dbc4`); T3 fixed.

## Recently merged (2026-11-13)
All branches are merged into `main`; details in [README §14](../README.md#14-branches-and-merges).
- ✅ PR #1 `frontend/redesign`: top navigation, guided Watch → Try a change → Compare, isometric hall ([FRONTEND_REDESIGN_PLAN.md](FRONTEND_REDESIGN_PLAN.md)).
- ✅ PR #2 `fix-bug`: Hall comparison and replay-retry fixes.
- ✅ PR #3 `codex/similar-nights-docs`: Similar nights handoff, then the feature itself (`137c784`, [SIMILAR_NIGHTS.md](SIMILAR_NIGHTS.md)).
- ✅ `dcsim tsdb similar` (`9e01ec7`) and the narration timeout L5 (`dcf6772`).

---

## Summary

| Tracker | ✅ Done or closed | ◐ Partly | ✗ Open | Doc |
| --- | --- | --- | --- | --- |
| Engine, back-test, API, LLM, repo issues | 23 | 3 | 4 | [ISSUES.md](ISSUES.md) |
| Not built yet (N1–N9) | 3 | 2 | 4 | [ISSUES.md §7](ISSUES.md#7-not-built-yet-n) |
| Web UI issues | 11 | 3 | 4 | [UI_ISSUES.md](UI_ISSUES.md) |
| Sponsor prizes | 2 | 1 | 2 | [SPONSORS.md](SPONSORS.md) |

---

## Engine, back-test, API, LLM, repo ([ISSUES.md](ISSUES.md))

| ID | Item | Status |
| --- | --- | --- |
| E1 | Students walked away from far too many lines | ✅ Fixed 0.4.1 |
| E2 | Students kept moving through the hall after close | ✅ Fixed 0.4.1 (late night 9 pm–12 am) |
| E3 | Stockouts higher than the team observes | ✅ Fixed 0.6.1 vs the team targets (last dinner batch 15 min before close; late run-outs 5–15% of dinner dishes on all dates; Nov 13 ~38 stockout-min/night); confirm with R06 |
| E4 | Made-to-order dishes showed "empty pan" after close | ✅ Fixed 0.4.1 |
| E5 | Same crowd every date | ✅ Fixed 0.4.2 (`crowd_by_date.json`, placeholder multipliers; needs real swipe data) |
| E6 | Noodle Bowl popularity per station, not per dish | ✅ Fixed 0.6.0 (Hot Pot, Pho, Tom Yum, Taiwan Pork 2×; ramen 1×) |
| E7 | Thanksgiving carving modeled as pre-made pans | ✅ Fixed 0.4.2 (confirm with the team) |
| E8 | Seats are zones; personas can't overlap; add-ons use no stock | ◐ Partly (0.6.0: pescetarian diet and allergies; add-ons not modeled **by decision** (cheap, refilled constantly, would add guesses); seat zones and persona overlap open) |
| B1 | Back-test grades the engine against itself | ✗ Open (needs real data) |
| B2 | Plan wins on waste, loses on stockouts | ◐ Partly (0.6.1 back-test: waste 8.8% lower, but 53 stockout-min/night under the plan vs 48 baseline plan and 41 recorded; UI shows the trade-off; optimizer objective open) |
| C1 | Tiny differences labeled "worse" | ✅ Fixed (verdict "same" below a per-metric minimum: waste max(5, 1%), stockouts 1 min, missed first 0.5 pp, wait p90 0.5 min, missed meal max(2, 1%)) |
| A1 | No authentication on admin / LLM routes | ✅ Fixed (`DCSIM_ADMIN_TOKEN` guards DELETE scenarios, `/api/warm`, `/api/backtest`; AI routes rate-limited per IP, `DCSIM_AI_RATE`, default 20/min) |
| A2 | Memory grows with jobs and cached traces | ✅ Fixed (finished jobs expire after `DCSIM_JOB_TTL_S`=900 s, max 200 kept; trace cache 2, `DCSIM_TRACE_CACHE`) |
| A3 | Trace format differs from the spec | ✅ Fixed (`?include=series`, `POST/GET /api/warm`, `POST/GET /api/backtest` background jobs) |
| L1 | Persona generation paused | ✗ Open |
| L2 | Groq free-tier output limit | ✅ Mitigated (Gemini default, load gate, bundle narrations) |
| L3 | Groq key pasted into a chat | ✅ Closed 2026-11-14 (not needed: Groq isn't used) |
| L4 | Narration check covered names only | ✅ Fixed (`narrate-v3`: times and numbers) |
| L5 | `dcsim bundle` narration could hang forever | ✅ Fixed 2026-11-13 (timeout + progress lines) |
| D1 | ASSUMPTIONS.md has stale planner numbers | ✅ Fixed 2026-11-13 (0.5.0 numbers; golden test) |
| D2 | Version numbers disagree | ✅ Fixed 2026-11-13 (both 0.5.0; test keeps them equal) |
| T1 | Not a git repo; bundle size undecided | ✅ Fixed 2026-11-13 (GitHub; bundle committed) |
| T2 | Starlette deprecation warning | ✅ Fixed |
| T3 | Tiger's copy of the synthetic history was stale | ✅ Fixed 2026-11-14 (0 of 42 nights differ) |
| T4 | `codex-skills` was an empty submodule (no `.gitmodules`) | ✅ Closed 2026-11-14 (entry removed) |
| T5 | Two similar-night searches (page/API and CLI) | ✅ Fixed 2026-11-14: one search (`nightsearch.py`, DiskANN) behind the page, API and CLI |
| T6 | `.dev.vars` with app tokens pushed to GitHub (was `e926ea1`) | ◐ Partly fixed 2026-11-14 (untracked, ignored, removed from branch history); **high**: rotate the tokens, ask GitHub to purge the PR refs |
| T7 | `my-app/` (separate deepspace app) committed without docs | ✅ Fixed 2026-11-14 (folder deleted in `ab7bf71`) |
| T8 | 6 test forecasts in the Tiger ledger for 2026-11-13 (append-only, can't be deleted) | ✗ Open, low: guard added (`DCSIM_LEDGER_WRITES=0`, `make server-test`); exclude them when reading the scorecard |

## 0.6.1 review ([ISSUES.md §6b](ISSUES.md))

| ID | Item | Status |
| --- | --- | --- |
| R1 | Expiry while a student waits at the pan skipped lot bookkeeping | ✅ Fixed 0.6.1 |
| R2 | Late-night stockouts up with kitchen limits | ✅ Fixed 0.6.1 (taper 50%, 5 late cooks) |
| R3 | "Why?" / settings list missed kitchen staff, shifts, forecast | ✅ Fixed 0.6.1 |
| R4 | Plain-English box couldn't set cooks or shifts | ✅ Fixed 0.6.1 (`ask-v3`) |
| R5 | Pan swaps not counted as stockouts | ✗ By design, documented |
| R6 | Mock /ask matches preset names in questions | ✅ Fixed 0.6.2 |
| R7 | Forecast reorder trades waste for run-outs | ✗ Open (optimizer could tune it) |

## Not built yet ([ISSUES.md §7](ISSUES.md#7-not-built-yet-n))

| ID | Item | Status |
| --- | --- | --- |
| N1 | Animated 3D Hall | ◐ (isometric Canvas replay since PR #1; orbital 3D not built) |
| N2 | Nav graph and table-level seats | ◐ (seating zones and door routes; 0.5.0 seat counts per zone, limits off until real seats are counted) |
| N3 | Servers by hour; "servers needed" output | ✅ Built 0.5.0 (`shifts`, lever, `staffing` in results; not on the Dashboard yet, U5) |
| N4 | Breakfast and lunch | ✅ Closed 2026-11-14 (not needed: dinner + late night only) |
| N5 | Survey calibration of personas | ✗ |
| N6 | Optimizer | ✅ Built 2026-11-14 (IMPROVEMENTS 2.1): waste-vs-stockouts frontier, Lab › Optimize |
| N7 | Uncertainty sweep | ✅ Built 2026-11-14 (IMPROVEMENTS 2.2): plausible worlds + tornado; a 1,000-world run is the Vultr pitch (SPONSORS §4) |
| N8 | Allergens | ✅ Built 0.6.0 (survey allergy shares, pescetarian 2%) |
| N9 | Real data hookups (R01, R02, R06, R07) | ✗ |

## Web UI ([UI_ISSUES.md](UI_ISSUES.md))

| ID | Item | Status |
| --- | --- | --- |
| U3 | Replay traces from the API | ✅ Fixed (with API) |
| U6 | Waste per dish columns and filters | ✅ Fixed |
| U8 | Adopt / watch / save API runs | ✅ Fixed |
| U10 | Narration of any student | ✅ Fixed (with API) |
| U11 | Engine issues visible in the replay | ✅ Fixed (engine 0.4.1) |
| U12 | Long-line alerts grouped | ✅ Fixed |
| U13 | `/api/health` 500 when the API is down | ✅ Fixed |
| U1 | Figma numbers are placeholders | ◐ SPEC demo script fixed; Figma frames and slides open |
| U4 | Dashboard dinner only | ◐ Late night shown; breakfast and lunch open |
| U7 | Lab: Optimize and uncertainty sweep | ✅ Built 2026-11-14 (dish swap is still two steps) |
| U2 | Only the top-down view is live | ◐ Isometric replay plays the full trace (PR #1); orbital 3D open |
| U5 | Staffing "needed" not shown | ✅ Fixed 0.6.2 ("Needed at peak" column; verified with data on the 0.6.2 bundle) |
| U9 | Replay geometry approximate | ✗ (needs N2) |
| U14 | Stack differs from SPEC §9 | ✗ (a decision, not a bug) |
| U15 | Several features need the API | ✗ (by design offline; run `make server` for the demo) |
| U16 | Per-dish $ doesn't sum to the headline | ✅ Fixed 0.6.2 (same $ basis; rows add up to the average week) |
| U17 | Two Hall smoke steps fail after the redesign | ✅ Fixed 2026-11-13 (script selectors; the app was fine) |
| U18 | Hall pan level includes the hot-hold cabinet (wrong for 30 s during a pan swap) | ✗ Open (export the on-line level in the replay) |

## Sponsor prizes ([SPONSORS.md](SPONSORS.md))

| # | Prize | Status |
| --- | --- | --- |
| 1 | Gemini API | ✅ Ask box on **function calling** (JSON and keyword fallbacks), Sam's tool loop, narration, explain, dish prior, embeddings; image input out of scope |
| 2 | Agentic hack (Sentient) | ✅ Sam the Minuteman built 2026-11-14 (`ai/agent.py`, `POST /api/agent`, `dcsim agent`, `/sam`): goals in ≤ 5 tries with batch, staffing and menu setting functions, Tiger lookups via the Tiger MCP server, what-ifs and history questions in the same box, History; the optimizer (N6) is built separately in the Lab (Optuna), not inside Sam |
| 3 | Tiger Data | ◐ Schema, calibration, forecast ledger, compression built; **live dashboard built 2026-11-14** (served by the API at `/tiger`, not part of the web app; two real-time continuous aggregates, `dcsim tsdb bench`, one query across the stack; handoff in [TIGER.md §8](TIGER.md)); **Similar Nights page + `GET /api/similar-nights` (pgvector, [SIMILAR_NIGHTS.md](SIMILAR_NIGHTS.md)) built**; CLI `tsdb similar` (DiskANN) built; track record, weather/event fit and MCP question set built 2026-11-14 (TIGER.md §4–6); 500-night demo query open |
| 4 | Vultr | ✗ Not deployed |
| 5 | GoDaddy Registry domain | ✗ Not registered |

## Other docs
- [IMPROVEMENTS.md](IMPROVEMENTS.md): Tier 0 and Tier 1 all ✅ (1.1 Container pans and 1.4 per-minute series in 0.6.0); 2.4 seats ◐ (built, limits off); model-realism table all ✅ (item-level popularity, swipe queue, groups, pan sizes, forecast-reorder preset, allergens in 0.6.0; staffing by hour, per-date crowd, carving earlier); calibration targets ✅; 2.1 optimizer and 2.2 plausible worlds ✅ (2026-11-14); sensitivity analysis ✗.
- [LLM.md](LLM.md) §5 and [GEMINI.md](GEMINI.md) §7: their own ~~done~~ lists are current.
- [TIGER.md](TIGER.md): "What is built on Tiger" table is current (items 0–8 built; 5 built but not kept on this history). §8 ends with what is left for the dashboard work (restart the API, rehearsal reset).

## How to keep this current
1. Fix something → mark it in its own doc (strike through + **Fixed** with the engine version or date).
2. Update the row here and the counts in the summary.
3. Commit both with the fix.
