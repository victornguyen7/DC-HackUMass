# Project status: what's done, what's open

One page that rolls up every tracker. The details stay in each doc; this page only says **done / partly done / open** and where to look.
Update it whenever you close something, in the same commit as the fix.

Last updated: 2026-11-13, engine 0.6.2.

**Markers:** ✅ done (fixed / closed / built) · ◐ partly done · ✗ open · ⚠️ done but **not committed yet**

## Before the demo
- ✗ **Rebuild the demo bundle once, at the end.** The committed bundle (`web/public/cache`) is engine **0.4.1**; the code is 0.6.2, so the web app shows older numbers until it's rebuilt. Run `make demo-bundle` (15–30 min with Gemini narrations) or `LLM_PROVIDER=mock make demo-bundle` (a few minutes, template text).
- ⚠️ Commit the narration timeout fix (L5) and the similar-night search (Tiger) once they're finished.

---

## Summary

| Tracker | ✅ Done | ◐ Partly | ✗ Open | Doc |
| --- | --- | --- | --- | --- |
| Engine, back-test, API, LLM, repo issues | 18 | 2 | 3 | [ISSUES.md](ISSUES.md) |
| Not built yet (N1–N9) | 2 | 1 | 6 | [ISSUES.md §7](ISSUES.md#7-not-built-yet-n) |
| Web UI issues | 9 | 3 | 5 | [UI_ISSUES.md](UI_ISSUES.md) |
| Sponsor prizes | 0 | 2 | 3 | [SPONSORS.md](SPONSORS.md) |

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
| L3 | Groq key pasted into a chat | ✗ Open — rotate after the hackathon |
| L4 | Narration check covered names only | ✅ Fixed (`narrate-v3`: times and numbers) |
| L5 | `dcsim bundle` narration could hang forever | ✅⚠️ Fixed 2026-11-13 (timeout + progress), **not committed yet** |
| D1 | ASSUMPTIONS.md has stale planner numbers | ✅ Fixed 2026-11-13 (0.5.0 numbers; golden test) |
| D2 | Version numbers disagree | ✅ Fixed 2026-11-13 (both 0.5.0; test keeps them equal) |
| T1 | Not a git repo; bundle size undecided | ✅ Fixed 2026-11-13 (GitHub; bundle committed) |
| T2 | Starlette deprecation warning | ✅ Fixed |

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
| N1 | Animated 3D Hall | ✗ (top-down 2D replay works) |
| N2 | Nav graph and table-level seats | ◐ (seating zones and door routes; 0.5.0 seat counts per zone, limits off until real seats are counted) |
| N3 | Servers by hour; "servers needed" output | ✅ Built 0.5.0 (`shifts`, lever, `staffing` in results; not on the Dashboard yet, U5) |
| N4 | Breakfast and lunch | ✗ (late night added in 0.4.1) |
| N5 | Survey calibration of personas | ✗ |
| N6 | Optimizer | ✗ (also the core of the agent prize, SPONSORS §2) |
| N7 | Uncertainty sweep | ✗ (Vultr prize, SPONSORS §4) |
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
| U7 | Lab: Optimize and uncertainty sweep | ◐ Ask box, menu edits, new dish, Why? built; Optimize and sweep open |
| U2 | Only the top-down view is live | ✗ |
| U5 | Staffing "needed" not shown | ✅ Fixed 0.6.2 ("Needed at peak" column; data after the bundle rebuild) |
| U9 | Replay geometry approximate | ✗ (needs N2) |
| U14 | Stack differs from SPEC §9 | ✗ (a decision, not a bug) |
| U15 | Several features need the API | ✗ (by design offline; run `make serve` for the demo) |
| U16 | Per-dish $ doesn't sum to the headline | ✅ Fixed 0.6.2 (same $ basis; rows add up to the average week) |
| U17 | Two Hall smoke steps fail after the redesign | ✗ Open (smoke selectors; owner of PR #1) |

## Sponsor prizes ([SPONSORS.md](SPONSORS.md))

| # | Prize | Status |
| --- | --- | --- |
| 1 | Gemini API | ◐ Ask box, narration, explain, dish prior, embeddings on Gemini; function calling and menu photo open |
| 2 | Agentic hack (Sentient) | ✗ Planning agent not built (needs tools + loop; optimizer N6 optional) |
| 3 | Tiger Data | ◐ Schema, calibration, forecast ledger, compression built; **Similar Nights page + `GET /api/similar-nights` (pgvector, [SIMILAR_NIGHTS.md](SIMILAR_NIGHTS.md)) built**; CLI `tsdb similar` (DiskANN) built ⚠️ not committed; 500-night demo query and MCP open |
| 4 | Vultr | ✗ Not deployed |
| 5 | GoDaddy Registry domain | ✗ Not registered |

## Other docs
- [IMPROVEMENTS.md](IMPROVEMENTS.md): Tier 0 and Tier 1 all ✅ (1.1 Container pans and 1.4 per-minute series in 0.6.0); 2.4 seats ◐ (built, limits off); model-realism table all ✅ (item-level popularity, swipe queue, groups, pan sizes, forecast-reorder preset, allergens in 0.6.0; staffing by hour, per-date crowd, carving earlier); calibration targets ✅; optimizer, sweep, sensitivity analysis ✗.
- [LLM.md](LLM.md) §5 and [GEMINI.md](GEMINI.md) §7: their own ~~done~~ lists are current.
- [TIGER.md](TIGER.md): "What is built on Tiger" table is current (items 0–3 and 7 built; 4–6 ideas; MCP not installed).

## How to keep this current
1. Fix something → mark it in its own doc (strike through + **Fixed** with the engine version or date).
2. Update the row here and the counts in the summary.
3. Commit both with the fix.
