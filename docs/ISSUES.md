# Known issues

Status as of 2026-11-12, engine 0.3.3; updated 2026-11-13 for engine 0.3.4 (seating zones, door routes), the web UI in `web/` and the per-dish back-test. Numbers marked *measured* come from Nov 13, night 11 under 0.3.3 (the median night: 3,059 students, close 9:00 pm) unless noted.

Severity: **High** = changes headline numbers or could mislead a viewer · **Medium** = wrong in some cases, or a gap the demo will show · **Low** = cleanup.

| ID | Sev | Area | Issue |
| --- | --- | --- | --- |
| E1 | High | Engine | Students walk away from far too many lines |
| E2 | High | Engine | Students keep moving through the hall after close |
| E3 | Medium | Engine | Stockouts are higher than the team observes |
| E4 | Medium | Engine | Made-to-order dishes show "empty pan" events after close |
| E5 | Medium | Engine | Same crowd every date |
| E6 | Medium | Engine | Noodle Bowl popularity is set per station, not per dish |
| E7 | Medium | Engine | Thanksgiving carving (Nov 18) is modeled as pre-made pans |
| E8 | Low | Engine | Seats are zones, not tables; personas can't overlap; add-ons use no stock |
| B1 | High | Back-test | The back-test grades the engine against itself |
| B2 | Medium | Back-test | The plan "wins" on waste but loses on stockouts |
| C1 | Medium | Compare | Tiny differences get labeled "worse" |
| A1 | Medium | API | Admin actions have no authentication |
| A2 | Medium | API | Memory grows with jobs and cached traces |
| A3 | Low | API | Trace format differs from the spec |
| L1 | High | LLM | Persona generation is paused (bad output on Qwen 27B) |
| L2 | Medium | LLM | Groq free-tier limit: 1,000 output tokens per minute |
| L3 | Medium | LLM | The Groq key was pasted into a chat |
| L4 | Low | LLM | Narration check covers names only |
| D1 | Medium | Docs | ASSUMPTIONS.md has stale planner numbers |
| D2 | Low | Docs | Version numbers disagree |
| T1 | Medium | Repo | Not a git repository; 16.7 MB bundle undecided |
| T2 | Low | Tests | Starlette deprecation warning |
| N1–N9 | — | Not built | See §7 |
| U1–U16 | — | Web UI | See [UI_ISSUES.md](UI_ISSUES.md) (U3, U6, U8, U10, U12, U13 closed) |

---

## 1. Engine

### E1 · High · Students walk away from far too many lines
- **Measured:** a student walks away from a median of 3 lines per visit, 25 at the 90th percentile and 46 at most. **1,000 of 3,059 students (33%) walk away from 10 or more lines.** Example: Student #11103 (vegetarian) walked away from about 20 lines between 7:40 and 7:44 pm.
- **Cause:** after walking away, `_student` moves on to the next dish in the preference list, often at another station. During the 7:30 rush every staffed line is 13–34 long, so a low-patience student tours almost every station. Each check costs only walking time, a few seconds.
- **Effect:** inflates `balks` and `empty_seen`. Also likely inflates "never got first choice" and missed meals, makes the 3D replay look frantic, and doesn't match how students behave.
- **Fix ideas:**
  - Cap stations tried per trip (e.g. 3), then take the shortest line or a self-serve item.
  - Let a student see line lengths from a distance and pick a station, instead of walking up to each one.
  - Add a per-station check time.
  - Re-check the line-length calibration afterwards. Peak lines currently match the team's 30–40.

### E2 · High · Students keep moving through the hall after close
- **Measured:**
  - 69 students arrive in the last 10 minutes.
  - **247 students are still joining lines, walking away or finding empty pans after 9:00 pm.**
  - 17 are served after close.
  - 171 leave more than 30 minutes after close.
  - Example: Student #15730 arrives at 8:54 pm, finds every pan empty, and is still walking between stations at 9:05 pm.
- **Fix:** stop arrivals some minutes before close, and send anyone still in the serving area to seating or the exit at close. Decide whether 9:00 is last entry or doors closing, and whether lines already formed get served. Ask the team.

### E3 · Medium · Stockouts are higher than the team observes
- Nov 13 baseline: about 131 stockout-minutes and 26% of cooked portions wasted (2,904 portions). The team says food *rarely* runs out late on a normal day. The planner's `reorder_factor` 1.2 ("setting A") was a compromise.
- Likely tied to E1 and E2: students who tour stations and stay after close drain and "see" pans that real students wouldn't.
- **Fix:** re-tune after E1 and E2 are fixed. Needs R06 (planned vs actual production) to settle.

### E4 · Medium · Made-to-order dishes show "empty pan" events after close
- **Measured:** 389 `empty_pan` events for Sushi rolls, all after close. Close sets every pan to 0, made-to-order ones included. The narration and 3D replay show "the California Roll pan was empty".
- **Fix:** made-to-order dishes should say "station closed" (a new event type) or never appear as pans. E2 removes most of these anyway.

### E5 · Medium · Same crowd every date
- 3,000 students every night. Weekend and event dates are known from `calendar.json`, but there is no lift or drop for them. Nov 18 looks best only because of E7, not because of attendance.
- **Fix:** add a per-date crowd multiplier from the calendar. Needs R01 or R08 attendance data to set real values.

### E6 · Medium · Noodle Bowl popularity is set per station, not per dish
- The team's 2× favorite-station boost applies to the whole Noodle Bowl station, but the team says its line depends on the dish. Hot Pot (Nov 13) may not draw a crowd.
- **Fix:** move the boost to specific dishes in `overrides.json → popularity`. Needs the team's list of crowd-drawing Noodle Bowl dishes.

### E7 · Medium · Thanksgiving carving (Nov 18) is modeled as pre-made pans
- Display is treated as self-serve pans, but carving is likely served to order that night. This makes Nov 18 look like the best date.
- **Fix:** a per-date station override, made to order with servers for the carving item. Confirm with the team.

### E8 · Low · Smaller modeling gaps
- Seating has two zones since 0.3.4 (`main`, 284 seats, and the indoor `back` area, 120 seats, reached by turning left after check-in), and walks route through the doors. There are no individual tables or nav graph: the web replay picks a seat from an **assumed** table layout and walks in straight lines (UI_ISSUES U9).
- Persona shares must sum to 1, but "rushed" and "social" can overlap in real students.
- Add-ons (sauces, toppings) don't use up stock.
- Pescetarian (2.0%) has no diet rule.
- "Make Your Own Waffles" is modeled as a dessert pan.
- Display station position is a guess (`layout/floor_plan.json`, marked `assumed`).
- Guessed values still in use: crowd 3,000, wave shares and widths, favorite-line patience 3×, halal 7%, athlete 10%, cook and hold times. Full list in `data/worcester/ASSUMPTIONS.md` with data requests R01–R08.

---

## 2. Back-test and synthetic history

### B1 · High · The back-test grades the engine against itself
- The "recorded" history (`data/synthetic/worcester/`) is generated by the **same engine** plus noise. The back-test then re-simulates it with the same engine. Its 6.4% headline reduction mostly measures the generator's own over-prep assumption: about 4.7 points come from "cooking to the forecast" and 1.6 from the plan's changes (back-test re-run 2026-11-13).
- It is labeled synthetic everywhere, but a viewer could still read it as evidence.
- **Fix:**
  - In the UI, lead with the 1.7-point "plan changes" part, and say plainly that the over-prep share is an input of the generator, not a finding.
  - Replace it with real MetaFoodx or LeanPath exports when available (R06, R07).
- **Partly addressed (Nov 13):** `dcsim calibrate` (docs/TIGER.md) fits crowd error and the production ratio on weeks 1–4 only and scores *forecasts* on held-out weeks 5–6 by interval coverage and CRPS. It reads its targets from Tiger, so real exports in the same shape need no code change. On synthetic data it can still only show that the pipeline recovers the generator's settings. It cannot show the engine matches a real kitchen.

### B2 · Medium · The plan "wins" on waste but loses on stockouts
- The `smaller_batches` plan wins 34 of 39 nights on waste but adds about 16 stockout-minutes per night (75.7 vs 59.6 recorded). Against the baseline plan cooked to the forecast it is +1.3 minutes (74.4 → 75.7). The waste headline alone overstates the plan.
- **Partly addressed:** the Waste Record page shows the stockout change next to the waste reduction, and the What-If Lab shows waste, stockouts, missed first choice and wait together with a verdict each.
- **Still open:** SPEC §12's demo line was updated to the real numbers on 2026-11-13. Consider an optimizer objective that limits stockout minutes.

---

## 3. Comparison

### C1 · Medium · Tiny differences get labeled "worse"
- `runner.compare` labels a metric "worse" when it loses in ≥60% of nights and the median difference is above 0, however small. Example: a 0.1-minute wait difference can be "worse".
- **Fix:** add a minimum meaningful difference per metric (e.g. 1% of baseline, or 5 portions, or 1 minute) below which the verdict is "no real difference".

---

## 4. Backend API

### A1 · Medium · Admin actions have no authentication
- Anyone who can reach the API can save, adopt or delete scenarios, and trigger LLM calls that spend the Gemini (or Groq) quota (`/api/ask`, `/api/narrate`, `/api/explain`, `/api/ai/dish-prior`, `/api/ai/similar-dishes`). The web UI now calls all of these from buttons, so a busy demo spends quota faster. CORS only limits browsers.
- **Fix:** before hosting publicly, add a shared admin token for write routes and a per-IP rate limit on LLM routes.

### A2 · Medium · Memory grows with jobs and cached traces
- `Jobs.by_id` keeps every job forever. The narration trace cache (`TRACES`) holds 4 decompressed traces (4.7 MB of JSON each, much more as Python objects).
- **Fix:** expire finished jobs after N minutes. Keep 1–2 traces, or store a per-student index instead of the whole trace.

### A3 · Low · Trace format differs from the spec
- Traces are gzipped JSON (347 KB per night), not msgpack.gz as first planned. `?include=series` on `GET /results`, `POST /warm` and `POST /backtest` (as a job) are not built; the back-test is the `dcsim backtest` command.
- **Done 2026-11-13:** ENGINE.md §8 now lists the routes as built. The web app reads traces with `fetch` (the browser decodes the gzip).

---

## 5. LLM layer (details in `LLM.md`)

### L1 · High · Persona generation is paused (bad output on Qwen 27B)
- Near-duplicate personas, broken JSON in the vegetarian batch, two empty personas. The rejected file is in `data/llm_drafts/rejected/`, and the commands need `DCSIM_PERSONAS_LLM=1`.
- The engine uses the hand-written `personas.json`, so nothing else is affected.
- **Fix:** the new persona approach (planned for tomorrow) must write a file in the format given in `LLM.md` §4.

### L2 · Medium · Groq free-tier limit: 1,000 output tokens per minute
- Only `qwen/qwen3.8-27b` is enabled on the project; `gpt-oss-120b` and `gpt-oss-20b` are blocked.
- A burst of clicks in a live demo can hit the limit. The narration then falls back to the template, which is correct but less lively.
- **Fix:** an in-app token-bucket queue, enable a stronger model or the Dev tier, and use pre-built bundle narrations during the demo.

### L3 · Medium · The Groq key was pasted into a chat
- It is stored only in `engine/.env` (mode 600, in `.gitignore`).
- **Fix:** rotate it after the hackathon.

### L4 · Low · Narration check covers names only
- The check catches unseen dishes and stations and claims of eating when the student got nothing. It does not check clock times or line lengths, and a paraphrased dish name ("grilled chicken") isn't recognized.
- **Fix:** see `LLM.md` §5.

---

## 6. Docs, repo and tooling

### D1 · Medium · ASSUMPTIONS.md has stale planner numbers
- Row A16 says Nov 13 has "72 stockout-min, 30% of cooked portions wasted". The current baseline is about 131 min and 26%.
- **Fix:** refresh after the E1–E3 changes. Consider generating these numbers from the engine instead of writing them by hand.

### D2 · Low · Version numbers disagree
- `pyproject.toml` says 0.1.0; `ENGINE_VERSION` is 0.3.4.
- **Fix:** read one from the other.

### T1 · Medium · Not a git repository; 16.7 MB bundle undecided
- There is no history or rollback for any change. The demo bundle (`web/public/cache`: 254 results, 13 traces, 13 narration files with 156 narrations, back-test with per-dish rows) is about 17 MB, and `web/dist` copies it.
- **Fix:**
  - `git init` with a `.gitignore` (already has `.env`, `.venv/`, `__pycache__/`; add `cache/`).
  - Decide whether to commit the full bundle, or default to the presets-only bundle (about 5 MB) and build the grid in CI.

### T2 · Low · Starlette deprecation warning
- The FastAPI test client warns "Using `httpx` with `starlette.testclient` is deprecated; install `httpx2`". Harmless today.

---

## 7. Not built yet (N)

| ID | Item | Spec |
| --- | --- | --- |
| N1 | Animated 3D Hall (React Three Fiber). The four pages are **built** in `web/` with a top-down 2D replay; remaining UI gaps are in UI_ISSUES.md | SPEC §3–6 |
| N2 | Nav graph and table-level seats. Seating zones (main / back) and door routes are built (0.3.4) | ENGINE §3 |
| N3 | Servers by hour (staffing changes during the meal), and a "servers needed for p90 wait < 8 min" output for the Dashboard (UI_ISSUES U5) | SPEC §6.1 |
| N4 | Breakfast and lunch (full-day arrivals) | SPEC §7 |
| N5 | Survey adjustment of personas and dish popularity, held-out check, `POST /personas/calibrate` | ENGINE §7.3 |
| N6 | Optimizer ("Optimize plan"); the back-test uses `smaller_batches` until then | SPEC §6.1 |
| N7 | Uncertainty sweep, so ranges widen for guessed inputs and AI-estimated dishes (the Lab's "Holds up if we're wrong?" needs it) | ENGINE §7.3 |
| N8 | Allergen modeling (10% of students report an allergy or intolerance) | ASSUMPTIONS context |
| N9 | Real data hookups: attendance (R01), staffing (R02), production and waste exports (R06, R07) | ASSUMPTIONS |

---

## Suggested order
1. **E2, then E1.** These are engine behavior fixes and will move every headline number. Bump `ENGINE_VERSION`, run `make cache-clean`, rebuild the bundle.
2. **E3 and D1:** re-tune the planner and refresh the docs.
3. **C1 and B2:** honest verdicts and trade-offs in the comparison and the back-test.
4. **L1:** the new persona approach.
5. **T1:** git, then N1 (the animated 3D Hall). The web pages are built; their open issues are in UI_ISSUES.md.
