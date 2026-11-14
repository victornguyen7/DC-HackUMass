# Project status: what's done, what's open

One page that rolls up every tracker. The details stay in each doc; this page only says **done / partly done / open** and where to look.
Update it whenever you close something, in the same commit as the fix.

Last updated: 2026-11-13, engine 0.4.2.

**Markers:** ✅ done (fixed / closed / built) · ◐ partly done · ✗ open · ⚠️ done but **not committed yet**

---

## Summary

| Tracker | ✅ Done | ◐ Partly | ✗ Open | Doc |
| --- | --- | --- | --- | --- |
| Engine, back-test, API, LLM, repo issues | 10 | 2 | 11 | [ISSUES.md](ISSUES.md) |
| Not built yet (N1–N9) | 0 | 1 | 8 | [ISSUES.md §7](ISSUES.md#7-not-built-yet-n) |
| Web UI issues | 7 | 3 | 6 | [UI_ISSUES.md](UI_ISSUES.md) |
| Sponsor prizes | 0 | 2 | 3 | [SPONSORS.md](SPONSORS.md) |

---

## Engine, back-test, API, LLM, repo ([ISSUES.md](ISSUES.md))

| ID | Item | Status |
| --- | --- | --- |
| E1 | Students walked away from far too many lines | ✅ Fixed 0.4.1 |
| E2 | Students kept moving through the hall after close | ✅ Fixed 0.4.1 (late night 9 pm–12 am) |
| E3 | Stockouts higher than the team observes | ✗ Open (better since 0.4.1; needs R06 data) |
| E4 | Made-to-order dishes showed "empty pan" after close | ✅ Fixed 0.4.1 |
| E5 | Same crowd every date | ✅ Fixed 0.4.2 (placeholder multipliers; needs real swipe data) |
| E6 | Noodle Bowl popularity per station, not per dish | ✗ Open |
| E7 | Thanksgiving carving modeled as pre-made pans | ✅ Fixed 0.4.2 (confirm with the team) |
| E8 | Seats are zones; personas can't overlap; add-ons use no stock | ✗ Open |
| B1 | Back-test grades the engine against itself | ✗ Open (needs real data) |
| B2 | Plan wins on waste, loses on stockouts | ◐ Partly (UI shows the trade-off; optimizer objective open) |
| C1 | Tiny differences labeled "worse" | ✗ Open |
| A1 | No authentication on admin / LLM routes | ✗ Open — **do before hosting publicly** |
| A2 | Memory grows with jobs and cached traces | ✗ Open |
| A3 | Trace format differs from the spec | ◐ Partly (docs updated; `POST /warm`, `/backtest` job not built) |
| L1 | Persona generation paused | ✗ Open |
| L2 | Groq free-tier output limit | ✅ Mitigated (Gemini default, load gate, bundle narrations) |
| L3 | Groq key pasted into a chat | ✗ Open — rotate after the hackathon |
| L4 | Narration check covered names only | ✅ Fixed (`narrate-v3`: times and numbers) |
| L5 | `dcsim bundle` narration could hang forever | ✅⚠️ Fixed 2026-11-13 (timeout + progress), **not committed yet** |
| D1 | ASSUMPTIONS.md has stale planner numbers | ✗ Open |
| D2 | Version numbers disagree (0.1.0 vs 0.4.2) | ✗ Open |
| T1 | Not a git repo; bundle size undecided | ✅ Fixed 2026-11-13 (GitHub; bundle committed) |
| T2 | Starlette deprecation warning | ✅ Fixed |

## Not built yet ([ISSUES.md §7](ISSUES.md#7-not-built-yet-n))

| ID | Item | Status |
| --- | --- | --- |
| N1 | Animated 3D Hall | ✗ (top-down 2D replay works) |
| N2 | Nav graph and table-level seats | ◐ (seating zones and door routes built) |
| N3 | Servers by hour; "servers needed" output | ✗ |
| N4 | Breakfast and lunch | ✗ (late night added in 0.4.1) |
| N5 | Survey calibration of personas | ✗ |
| N6 | Optimizer | ✗ (also the core of the agent prize, SPONSORS §2) |
| N7 | Uncertainty sweep | ✗ (Vultr prize, SPONSORS §4) |
| N8 | Allergens | ✗ |
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
| U5 | Staffing "needed" not computed | ✗ (needs N3) |
| U9 | Replay geometry approximate | ✗ (needs N2) |
| U14 | Stack differs from SPEC §9 | ✗ (a decision, not a bug) |
| U15 | Several features need the API | ✗ (by design offline; run `make serve` for the demo) |
| U16 | Per-dish $ doesn't sum to the headline | ✗ |

## Sponsor prizes ([SPONSORS.md](SPONSORS.md))

| # | Prize | Status |
| --- | --- | --- |
| 1 | Gemini API | ◐ Ask box, narration, explain, dish prior, embeddings on Gemini; function calling and menu photo open |
| 2 | Agentic hack (Sentient) | ✗ Planning agent not built (needs tools + loop; optimizer N6 optional) |
| 3 | Tiger Data | ◐ Schema, calibration, forecast ledger, compression built; 500-night demo query and MCP open |
| 4 | Vultr | ✗ Not deployed |
| 5 | GoDaddy Registry domain | ✗ Not registered |

## Other docs
- [IMPROVEMENTS.md](IMPROVEMENTS.md): Tier 0 items 0.1–0.3 ✅ (0.4 golden test ✗); per-date crowd, carving, trade-off card, LLM rate-limit gate and narration checks ✅; seating as a Resource ◐; optimizer, sweep, cooks and equipment ✗.
- [LLM.md](LLM.md) §5 and [GEMINI.md](GEMINI.md) §7: their own ~~done~~ lists are current.
- [TIGER.md](TIGER.md): "What is built on Tiger" table is current (items 0–3 built, 4–7 ideas).

## How to keep this current
1. Fix something → mark it in its own doc (strike through + **Fixed** with the engine version or date).
2. Update the row here and the counts in the summary.
3. Commit both with the fix.
