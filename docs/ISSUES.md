# Known issues

Status as of 2026-11-12, engine 0.3.3; updated 2026-11-13 for engine 0.6.1 (review of the simulation-core work: E3 fixed, late-night kitchen retune, an expiry bug, describer and /ask gaps), 0.6.0 (Container pans, per-minute series, E6, allergies and pescetarians, swipe queue, groups, pan sizes, forecast-reorder preset), 0.5.0 (simulation core: kitchen capacity, empty-pan waits, seats, staffing by hour; D1, D2), 0.4.2 (late-night waste taper, E5 crowd by date, E7 carving line) and 0.4.1 (E1 and E2 fixed, late night added) and earlier for engine 0.3.4 (seating zones, door routes), the web UI in `web/` and the per-dish back-test. Numbers marked *measured* come from Nov 13, night 11 under 0.3.3 (the median night: 3,059 students, close 9:00 pm) unless noted.

Severity: **High** = changes headline numbers or could mislead a viewer · **Medium** = wrong in some cases, or a gap the demo will show · **Low** = cleanup.

Status: a ~~struck-through~~ row marked **Fixed** or **Mitigated** is done; every other row is open. One-page roll-up: [STATUS.md](STATUS.md).

| ID | Sev | Area | Issue |
| --- | --- | --- | --- |
| E1 | ~~High~~ | Engine | ~~Students walk away from far too many lines~~ **Fixed in 0.4.1** |
| E2 | ~~High~~ | Engine | ~~Students keep moving through the hall after close~~ **Fixed in 0.4.1** (late night 9 pm–12 am) |
| E3 | ~~Medium~~ | Engine | ~~Stockouts are higher than the team observes~~ **Fixed in 0.6.1** against the team's targets (confirm with R06 data) |
| E4 | ~~Medium~~ | Engine | ~~Made-to-order dishes show "empty pan" events after close~~ **Fixed in 0.4.1** (`closed` event) |
| E5 | Low | Engine | ~~Same crowd every date~~ **Fixed in 0.4.2** with placeholder multipliers; needs real swipe data |
| E6 | ~~Medium~~ | Engine | ~~Noodle Bowl popularity is set per station, not per dish~~ **Fixed in 0.6.0** (per dish, team list) |
| E7 | ~~Medium~~ | Engine | ~~Thanksgiving carving (Nov 18) is modeled as pre-made pans~~ **Fixed in 0.4.2** (staffed carving line; confirm with the team) |
| E8 | Low | Engine | Seats are zones, not tables; personas can't overlap; add-ons use no stock |
| B1 | High | Back-test | The back-test grades the engine against itself |
| B2 | Medium | Back-test | The plan "wins" on waste but loses on stockouts |
| C1 | ~~Medium~~ | Compare | ~~Tiny differences get labeled "worse"~~ **Fixed 2026-11-13 (see STATUS.md)** |
| A1 | ~~Medium~~ | API | ~~Admin actions have no authentication~~ **Fixed 2026-11-13 (see STATUS.md)** |
| A2 | ~~Medium~~ | API | ~~Memory grows with jobs and cached traces~~ **Fixed 2026-11-13 (see STATUS.md)** |
| A3 | ~~Low~~ | API | ~~Trace format differs from the spec (`?include=series` built in 0.6.0; `POST /warm`, `/backtest` job not)~~ **Fixed 2026-11-13 (see STATUS.md)** |
| L1 | High | LLM | Persona generation is paused (bad output on Qwen 27B) |
| L2 | ~~Medium~~ | LLM | ~~Groq free-tier limit: 1,000 output tokens per minute~~ **Mitigated:** Gemini is the default provider; load gate + bundle narrations |
| L3 | Medium | LLM | The Groq key was pasted into a chat |
| L4 | ~~Low~~ | LLM | ~~Narration check covers names only~~ **Fixed** (`narrate-v3`: names, times, numbers) |
| L5 | ~~Medium~~ | LLM | ~~`dcsim bundle` narration could hang forever on a stalled Gemini request~~ **Fixed 2026-11-13** (timeout + progress lines; not committed yet) |
| D1 | ~~Medium~~ | Docs | ~~ASSUMPTIONS.md has stale planner numbers~~ **Fixed 2026-11-13** (engine 0.5.0 numbers; golden test guards them) |
| D2 | ~~Low~~ | Docs | ~~Version numbers disagree~~ **Fixed 2026-11-13** (both 0.5.0; a test keeps them equal) |
| T1 | ~~Low~~ | Repo | ~~Not a git repository; bundle size undecided~~ **Fixed 2026-11-13** (on GitHub; full bundle committed) |
| T2 | ~~Low~~ | Tests | ~~Starlette deprecation warning~~ **Fixed** (no warnings in `pytest`, checked 2026-11-13) |
| N1–N9 | — | Not built | See §7 |
| U1–U16 | — | Web UI | See [UI_ISSUES.md](UI_ISSUES.md) (U3, U6, U8, U10, U12, U13 closed) |

---

## 1. Engine

### E1 · Fixed (0.4.1) · Students walked away from far too many lines
- **Was (0.3.4):** a median of 3 walk-aways per visit, 25 at the 90th percentile and 46 at most; **33% of students walked away from 10 or more lines**. After walking away, a student walked on to the next station in their preference list, so a low-patience student toured almost every station in the 7:30 rush.
- **Fix:** students decide from where they stand (`model.py` `_choose`).
  - They look at the line and the pans for every dish they'd eat, best first.
  - Lines longer than their patience (× 3 for their favorite) and visibly empty pans are skipped **without walking over**. Each skip is one `balk` / `empty_pan` event, logged only for the dishes they came for.
  - They walk only to the first acceptable station and **stay in that line** until served; there is no walking away at the counter.
  - With an empty plate and every line too long, they join the shortest. Once they have something, they sit instead.
  - Seconds and the "retry the favorite" trip are decided from the seat the same way: they go only if the line is acceptable.
- **Measured (Nov 13, 5 nights, 0.4.1):**

  | | 0.3.4 | 0.4.1 |
  | --- | --- | --- |
  | Lines skipped per student (p50 / p90 / max) | 2–3 / 22–24 / 43–48 | 1 / 3 / 5 |
  | Students skipping 10+ lines | 30–33% | 0% |
  | Dinner wait for a plate (p50 / p90 min) | 8 / 17–18 | 6 / 17 |
  | Peak dinner lines (Grill, Latino, Sushi, Street Food) | 35, 33, 32, 26 | 36, 33, 33, 27 |
  | Dinner students who never got their first choice | 21% | 21% |
  | Dinner students who left with nothing | — | 2 |
  | Stockout-minutes per night, dinner + late night (after `make prepare`) | 100–160 (dinner only) | 76–138 (dinner 61–96) |
- At the chosen station a student takes the plate items they came for that are served there; a fallback station gives only its best dish. (An earlier draft took every dish the student would eat there, which lengthened lines 20–30% and pushed "never got first choice" to 33%.)
- Peak lines and "never got first choice" are back at the 0.3.4 calibration (team: peaks 30–40); waits are slightly shorter because nobody tours.
- `test_students_never_walk_away_from_a_line` guards this.

### E2 · Fixed (0.4.1) · Students kept moving through the hall after close
- **Was (0.3.4):** 247 students were still joining lines, walking away or finding empty pans after 9:00 pm; 17 were served after close.
- **Cause:** the engine ended the night at 9:00 pm, but Worcester runs **late night from 9:00 pm to 12:00 am** (audit fact F03), with its own menu (Grill, Display, Mediterranean; 10–16 items on every snapshot date).
- **Fix:** a night now has two service periods.
  - **Dinner 4:30–9:00 pm.** At 9:00 dinner dishes stop taking new students; lines already formed are served from what is left (up to 30 minutes), then the pans close.
  - **Late night 9:00 pm–12:00 am.** Late-night dishes (ids `late.<station>.<name>`) open with their own starting stock and kitchen plan. About **1,000 students** arrive with their own arrival curve and random stream (dinner arrivals are unchanged). Late-night plates are snacks: 1 item (60%) or 2 (40%). Arrivals peak around 9:15 and 9:55 pm and taper so that **fewer than 30 students are inside at 11:30 pm**.
  - **Midnight:** serving stops; anyone still in line gets a `closed` event and nothing more. Seated students finish eating and leave when they are done.
- **Measured (Nov 13, 5 nights, 0.4.1):** 992–1,027 late-night students, wait p50 3 / p90 14 min, 4–18 inside at 11:30 pm (below 30 on average on every date), 0 served after midnight. Late-night stockouts 15–32 minutes per night. The late Grill line still reaches about 60 at the 9:15 pm peak (2 servers).
- **Open:**
  - The 1,000 students, the plate sizes and the curve are team guesses; which days late night runs is not confirmed (F03 lists both Sun–Sat and Sun–Thu).
  - ~~Late-night waste is high (42%)~~ **Fixed in 0.4.2:** late-night demand collapses after ~10:30 pm but the plan kept cooking full batches until 11:40. The planner now tapers (`kitchen_defaults.json → planner_by_period.late_night`): once fewer than 1.5 batches are still to be eaten, the kitchen cooks top-up batches of 25% with a matching reorder point (`BatchPlan.taper_*`), and keeps no spare batches. Late-night waste fell from 38–42% to 18–23% of cooked portions on every date; late stockout-minutes are 17–48 a night (were 9–40). A tuning choice, not staff-confirmed; real late-night production numbers would settle it.
- Tests: `test_late_night_period`, `test_hall_nearly_empty_by_1130` (every date), `test_dinner_arrivals_unchanged_by_late_night`, `test_crowd_lever_scales_late_night`.

### E3 · Fixed (0.6.1; confirm with real data) · Stockouts are higher than the team observes
- Nov 13 baseline: about 131 stockout-minutes and 26% of cooked portions wasted (2,904 portions). The team says food *rarely* runs out late on a normal day. The planner's `reorder_factor` 1.2 ("setting A") was a compromise.
- Likely tied to E1 and E2: students who tour stations and stay after close drain and "see" pans that real students wouldn't.
- **Update (0.4.1):** with E1 and E2 fixed and the plans rebuilt, Nov 13 is about 61–96 dinner stockout-minutes per night (was 100–160). Re-check against the team's "rarely runs out" before closing this.
- **Update (0.5.0):** kitchen capacity makes batches that wait for a cook finish nearer 9 pm, so fewer pans run dry at the very end: Nov 13 dinner+late stockouts ~74 min/night (golden baseline), dinner dishes running out after 8:30 pm 12–16% on Nov 13 but ~21% on Nov 17 (team target ≤ 20%). Checked by `tests/test_calibration_targets.py::test_rarely_runs_out_late`, marked as a known borderline failure.
- **Fixed (0.6.1):** the dinner plan's last batch is now 15 min before close (was 20; `kitchen_defaults.json → planner_by_period.dinner`): dinner dishes running out after 8:30 pm are 5–15% on all 8 dates (team target ≤ 20%; 10 min would give 2–8% for 11% more waste). Late night was also retuned for kitchen limits (below, "0.6.1 review"). Nov 13 now: about 38 stockout-minutes per night (dinner 14, late night 20), dinner waste ~29% of cooked portions. `test_rarely_runs_out_late` is a regular test again.
- **Still open:** the targets are the team's observations, not data. R06 (planned vs actual production) would settle it.

### E4 · Fixed (0.4.1) · Made-to-order dishes show "empty pan" events after close
- **Measured:** 389 `empty_pan` events for Sushi rolls, all after close. Close sets every pan to 0, made-to-order ones included. The narration and 3D replay show "the California Roll pan was empty".
- **Fix:** made-to-order dishes should say "station closed" (a new event type) or never appear as pans. E2 removes most of these anyway.
- **Fixed (0.4.1):** a student still in line when a dish's service period ends gets a `closed` event, not `empty_pan`, so it no longer counts as an empty pan seen. Dinner lines already formed at 9:00 pm are served first (last call, up to 30 min).

### E5 · Fixed with placeholders (0.4.2) · Same crowd every date
- **Was:** 3,000 dinner students every night; weekends and events got no lift or drop.
- **Fix:** `data/worcester/crowd_by_date.json` multiplies the crowd by a weekday factor and a date event factor (`data.py` `crowd_factors`). **All values are guesses** until UMass Dining shares swipes by date (R01/R08):
  - Dinner: Mon–Thu 1.0, Fri 0.9, Sat 0.85, Sun 0.95.
  - Events: Nov 18 Friendsgiving ×1.15 (the same lift the synthetic history uses for event nights), Nov 20 TCSA dinner at Worcester ×1.1. The Nov 18 home football game has no kickoff time, so it isn't modeled.
  - Late night: no weekday or event effect yet (its days are unconfirmed, F03).
- Weekday factors also apply to the synthetic history (past nights reuse the snapshot menu with the same weekday); event factors apply only to their own date (`load_baseline(..., events=False)` for history). The planner plans for a typical night of that weekday, so an event's extra crowd shows up as pressure in that date's forecast.
- **Measured (0.4.2, 3 nights each):** dinner students Fri Nov 13 2,722 · Sat 2,571 · Sun 2,884 · Mon–Thu 3,037 · Wed Nov 18 3,492 · Fri Nov 20 3,011. Quieter nights waste 1–3 points more of what's cooked (minimum batch sizes don't shrink with the crowd).

### E6 · Medium · Noodle Bowl popularity is set per station, not per dish
- The team's 2× favorite-station boost applies to the whole Noodle Bowl station, but the team says its line depends on the dish. Hot Pot (Nov 13) may not draw a crowd.
- **Fix:** move the boost to specific dishes in `overrides.json → popularity`. Needs the team's list of crowd-drawing Noodle Bowl dishes.
- **Fixed (0.6.0):** team list (2026-11-13): Hot Pot, Pho, Shrimp Tom Yum and Taiwan Pork draw a crowd (2×); ramen does not (1×). `overrides.json → dish_popularity` (known to the kitchen plan, like the station factor it replaces).

### E7 · Fixed (0.4.2, confirm with the team) · Thanksgiving carving (Nov 18) was modeled as pre-made pans
- **Was:** the carved turkey sat in Display's self-serve pans (4 spots, 18 s an item), which made Nov 18 look like the best date.
- **Fix:** `overrides.json → station_by_date` moves a dish to another station on one date; `dcsim prepare` applies it. On Nov 18 "Carved Local Roast Turkey" goes to a new `carving` station beside Display: **staffed, 2 carvers, 0.5 min a plate (guesses)**. Roasts are still cooked in batches, so leftovers still count as waste (not made to order). The sides stay self-serve at Display.
- **Measured (Nov 18, 0.4.2 with E5's ×1.15 crowd):** carving line peaks at ~15; dinner stockout-minutes 56 → 115 and "never got first choice" 19% → 23%, so Nov 18 is no longer the best-looking date.

### E8 · Low · Smaller modeling gaps
- Seating has two zones since 0.3.4 (`main`, 284 seats, and the indoor `back` area, 120 seats, reached by turning left after check-in), and walks route through the doors. There are no individual tables or nav graph: the web replay picks a seat from an **assumed** table layout and walks in straight lines (UI_ISSUES U9).
- Persona shares must sum to 1, but "rushed" and "social" can overlap in real students. (Pescetarian diet and allergies were added in 0.6.0, N8.)
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
- **Engine 0.4.2 (taper, crowd by date, carving):** waste 7.3% lower (range 3.5–8.8%), plan wins 37 of 39; about 5.7 points from cooking to the forecast and 1.6 from the plan's changes; stockouts 99 min/night under the plan and the baseline plan vs 79 recorded.
- **Engine 0.4.1 (with late night):** the back-test read waste 5.9% lower (range 2.5–7.3%), plan wins 39 of 39 nights. About 4.5 points come from cooking to the forecast and 1.4 from the plan's changes; stockouts 93 min/night under the plan vs 77 recorded (91 for the baseline plan cooked to the forecast).
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

### L2 · Mitigated · Groq free-tier limit: 1,000 output tokens per minute
- **Mitigated 2026-11-13:** Gemini is now the default provider; Groq is only used with `LLM_PROVIDER=groq`. The live load gate (`DCSIM_LLM_MAX_LIVE`, `DCSIM_LLM_RPM`) and a backup model handle bursts, and the demo bundle ships pre-built narrations.
- Only `qwen/qwen3.8-27b` is enabled on the project; `gpt-oss-120b` and `gpt-oss-20b` are blocked.
- A burst of clicks in a live demo can hit the limit. The narration then falls back to the template, which is correct but less lively.
- **Fix:** an in-app token-bucket queue, enable a stronger model or the Dev tier, and use pre-built bundle narrations during the demo.

### L3 · Medium · The Groq key was pasted into a chat
- It is stored only in `engine/.env` (mode 600, in `.gitignore`).
- **Fix:** rotate it after the hackathon.

### L4 · Fixed (`narrate-v3`) · Narration check covered names only
- **Fixed:** `check` / `check_facts` in `ai/narrate.py` now also reject clock times that don't match an event (±1 min) and numbers that aren't in the student's events. Paraphrased dish names are still not recognized.
- The check catches unseen dishes and stations and claims of eating when the student got nothing. It does not check clock times or line lengths, and a paraphrased dish name ("grilled chicken") isn't recognized.
- **Fix:** see `LLM.md` §5.

### L5 · Fixed 2026-11-13 · `dcsim bundle` narration could hang forever
- **Was:** the Gemini client had no network timeout, so one stalled request blocked `make demo-bundle` with no output (a teammate's run looked frozen and was stopped with Ctrl+C).
- **Fixed:** requests time out after `DCSIM_LLM_TIMEOUT` seconds (default 60) and a narration stream is capped at the same time; the student falls back to the template, which is not cached. The bundle prints one progress line per student and a line every 15 s while waiting on the rate limit. Code: `ai/llm.py`, `ai/narrate.py`, `bundle.py` (**in the working tree, not committed yet**).
- **Tip:** `LLM_PROVIDER=mock make demo-bundle` skips the LLM entirely.

---

## 6. Docs, repo and tooling

### D1 · Medium · ASSUMPTIONS.md has stale planner numbers
- Row A16 says Nov 13 has "72 stockout-min, 30% of cooked portions wasted". The current baseline is about 131 min and 26%.
- **Fix:** refresh after the E1–E3 changes. Consider generating these numbers from the engine instead of writing them by hand.
- **Fixed 2026-11-13:** A16 now gives the engine 0.5.0 numbers (Nov 13: dinner 54 stockout-min, 29% wasted; late night 24 min, 21%). `tests/test_golden.py` pins the Nov 13 baseline, so a change that moves them fails until the golden file (and this row) is updated on purpose.

### D2 · Low · Version numbers disagree
- `pyproject.toml` said 0.1.0 while `ENGINE_VERSION` moved on.
- **Fixed 2026-11-13:** `pyproject.toml` is 0.5.0, and `test_package_version_matches_engine_version` fails if the two drift. Bump both together.

### T1 · Fixed 2026-11-13 · Not a git repository; 16.7 MB bundle undecided
- **Fixed:** the repo is on GitHub (`main`). `.gitignore` ignores only the root `/cache/`, and the full demo bundle in `web/public/cache` is committed, so `make web` works right after cloning.
- There is no history or rollback for any change. The demo bundle (`web/public/cache`: 254 results, 13 traces, 13 narration files with 156 narrations, back-test with per-dish rows) is about 17 MB, and `web/dist` copies it.
- **Fix:**
  - `git init` with a `.gitignore` (already has `.env`, `.venv/`, `__pycache__/`; add `cache/`).
  - Decide whether to commit the full bundle, or default to the presets-only bundle (about 5 MB) and build the grid in CI.

### T2 · Fixed · Starlette deprecation warning
- **Fixed:** `uv run pytest` shows no warnings (checked 2026-11-13).
- The FastAPI test client warns "Using `httpx` with `starlette.testclient` is deprecated; install `httpx2`". Harmless today.

---

## 6b. Review of the simulation-core work (engine 0.6.1, 2026-11-13)

Found while checking engines 0.5.0–0.6.0; all fixed in 0.6.1 unless noted.

| ID | Kind | Issue | Fix |
| --- | --- | --- | --- |
| R1 | Bug (rare) | A student waiting at an empty pan when a lot expired could take a portion without the lot bookkeeping (`PanStock.skip_lots` was on during the expiry) | `_expire` lets waiting students take their portions first, then expires the rest. `test_night_ends_clean` checks lots = pan level every night |
| R2 | Calibration | Late-night stockouts rose to ~49 min/night (Nov 13; 69 on Nov 17) once kitchen limits existed: the 0.4.2 taper cooks many 25% top-up batches and each costs a cook its full prep time; 4 late-night cooks queue | Taper batches 50% and 5 late-night cooks: Nov 13 late stockouts 19–20 min, Nov 17 38, late-night waste unchanged (~20–24%) |
| R3 | Gap | "Why?" and the /ask settings list described kitchen staff, shifts and forecast reordering as no change | `ai/describe.py` lists cooks, equipment, servers by hour, forecast reordering and a separate late-night crowd |
| R4 | Gap | The plain-English box couldn't set cooks or server shifts | `ai/ask.py` (prompt `ask-v3`): `cooks` and `shifts` in the plan (optional, so cached v2 replies stay valid); the mock understands "one more cook" |
| R5 | Model note (open) | Stockout-minutes count food on hand (line + hot-hold cabinet), so a 0.5-min pan swap isn't a stockout, though students see an empty pan and it counts in `empty_seen` | By design; documented in ENGINE.md |
| R6 | Model note (open) | The mock (no-AI) /ask parser matches preset names inside questions: "an extra server at grill" also picks the `extra_server` preset. Gemini doesn't | Low; only the offline fallback |
| R7 | Trade-off (open) | The `forecast_reorder` preset saves waste but can cost run-outs: Nov 13 waste −13%, stockouts 36 → 107 min; Nov 17 waste −6%, stockouts 66 → 57 | Shown as a choice in the Lab; an optimizer (N6) could tune its factor and safety stock per dish |

Speed: one Nov 13 night takes ~0.22 s serially with every 0.6 feature on (0.21 s without kitchen limits, pans, swipe and groups); the forecast preset ~0.26 s.

---

## 7. Not built yet (N)

| ID | Item | Spec |
| --- | --- | --- |
| N1 | Animated 3D Hall (React Three Fiber). The four pages are **built** in `web/` with a top-down 2D replay; remaining UI gaps are in UI_ISSUES.md | SPEC §3–6 |
| N2 | Nav graph and table-level seats. Seating zones (main / back) and door routes are built (0.3.4) | ENGINE §3 |
| N3 | ✅ **Built (engine 0.5.0):** `Station.shifts` / lever `stations.<id>.shifts` change servers during the night, and results carry `staffing` (servers needed per station and hour for ≤ 85% busy). ~~Servers by hour, and a "servers needed for p90 wait < 8 min" output~~. The Dashboard doesn't show it yet (UI_ISSUES U5) | SPEC §6.1 |
| N4 | Breakfast and lunch (full-day arrivals) | SPEC §7 |
| N5 | Survey adjustment of personas and dish popularity, held-out check, `POST /personas/calibrate` | ENGINE §7.3 |
| N6 | Optimizer ("Optimize plan"); the back-test uses `smaller_batches` until then | SPEC §6.1 |
| N7 | Uncertainty sweep, so ranges widen for guessed inputs and AI-estimated dishes (the Lab's "Holds up if we're wrong?" needs it) | ENGINE §7.3 |
| N8 | ✅ **Built (0.6.0):** allergies at the 2022–23 survey shares (drawn per student; dishes labeled with the allergen avoided) and a 2% pescetarian diet. ~~Allergen modeling (10% of students report an allergy or intolerance)~~ | ASSUMPTIONS context |
| N9 | Real data hookups: attendance (R01), staffing (R02), production and waste exports (R06, R07) | ASSUMPTIONS |

---

## Suggested order
1. ~~**E2, then E1.**~~ Done in 0.4.1 (with late night).
2. **E3 and D1:** re-tune the planner and refresh the docs.
3. **C1 and B2:** honest verdicts and trade-offs in the comparison and the back-test.
4. **L1:** the new persona approach.
5. ~~**T1:** git~~ (done), then N1 (the animated 3D Hall). The web pages are built; their open issues are in UI_ISSUES.md.
