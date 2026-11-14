# Web UI: test results and known issues

Status as of 2026-11-13, `web/` built from the Figma file (*DC What-If — Worcester*), first written for engine 0.3.4; updated for engine 0.6.2. The demo bundle was rebuilt on engine 0.6.2 (2026-11-14 04:05 UTC), so the pages show current numbers. A full re-test followed: see [Full test run after the 0.6.2 bundle](#full-test-run-after-the-062-bundle-2026-11-13). New engine outputs not shown on any page yet: kitchen wait and cook busy share, empty-pan wait, peak seated, `staffing` (U5) and per-minute `series` (`?include=series`, for line/pan-level charts). Since then (same day): the redesign (PR #1) and Hall fixes (PR #2) were merged, and **Insights › Similar nights** (`/similar-nights`) was added and tested (9 unit tests; Chrome at 1440 px and 390 px with the live API, no horizontal scroll, no console errors).
Second pass (same day): the UI now uses the engine API features it ignored before, and the back-test exports per-dish and per-station numbers. See [Second pass](#second-pass-connecting-the-ui-to-the-backend).

Severity: **High** = misleads a viewer or breaks the demo story · **Medium** = a designed feature that is missing or only partly works · **Low** = cleanup.

## How it was tested

| Check | Result |
| --- | --- |
| `npm run build` (TypeScript strict + Vite) | passes (JS 99.5 KB gzipped) |
| `npm test`: 17 unit tests on the real bundle (playback, lever mapping, plain-English settings → API request, grouped alerts, back-test per-station totals) | 17 passed |
| `node scripts/smoke.mjs`: headless Chrome, API **down**, 3 widths (1440 / 1024 / 390), 4 pages, plus interactions | 0 problems on the dev server and on the production build (`vite preview`) |
| `node scripts/smoke-api.mjs`: headless Chrome, API **up** (`make server`, Gemini configured) | 8 of 8 steps pass, 0 problems |
| Engine tests (`uv run pytest`) | 80 passed, 2 skipped |

What the unit tests check:
- Every manifest file exists.
- No student is drawn outside the building footprint at 6 sample times.
- Students are hidden before they enter and after they leave. "Inside" matches enter/exit events.
- Hands are empty until the first serving. Pan levels never go negative.
- Pan levels are 0 in more than 95% of the moments a student finds a pan empty.
- Lever-to-bundle mapping; menu edits and other API-only settings are never served from the bundle.
- Settings from `/api/ask` round-trip into the exact `/api/simulate` request.
- Long-line alerts collapse into one alert placed after the warnings.
- The back-test's per-station split adds up to each night's recorded total (and to the plan total within 5%).
- The UI's "missed first choice" coloring matches the engine metric: 21.2% vs 21.0% on Nov 13.

What `smoke.mjs` does (API down):
- Loads every page. Hall: play/pause, color modes, station click, 3D overview, keyboard scrubbing.
- Lab: switch presets; run a cached crowd lever; adopt a plan, then check the Dashboard shows it; Watch in 3D.
- Dashboard: switch the date (Nov 18 shows the Friendsgiving tag). The date now survives a reload, so "hall other date baseline" really opens Nov 18.
- Fails on any console error (other than the expected 4xx log), failed request or HTTP error, and on horizontal scroll at phone width.

What `smoke-api.mjs` does (API up):
1. Plain-English box: "add one more server at the Grill and 10% busier" → settings shown → Apply and run → simulated on the API.
2. "Why?" returns an explanation.
3. Menu: remove a dish, add "Butter Chicken Bowl" at Tandoor (suggest similar dishes, AI estimate) → run → "Where demand moves" card.
4. Adopt that API run → the Dashboard shows it as adopted.
5. The Saved list shows scenarios saved on the API.
6. Watch the API run in the Hall: replay comes from `GET /api/traces/{hash}`, highlight students from `/highlights`, narration appears.
7. Narrate a student who isn't a highlight.
8. A bundled crowd-lever scenario (numbers only in the bundle) replays from the API.

### Fixed during the first round of testing
- Station tags and map labels were too small at desktop width (now 1.3×; the Wok label no longer covers Seasons).
- The student card listed 60+ events for one student (engine issue E1). Runs of walk-aways and empty pans within 2 minutes now fold into one row.
- The default student wasn't on the map at the starting time.
- Diffs that round to zero showed as "−0.0" (now "±0.0").
- The staffing table overflowed its card; a KPI chip caused horizontal scroll on phones.
- In the Lab, dragging a slider re-created the accordion and lost focus.
- "Last 8 weeks" was clickable with only 42 recorded nights.
- Stockout "+1.4%" was labeled "no trade-off" (now "about the same").

## Backend changes made to fit this UI (2026-11-13)

- **Contract checked:** the Lab's request shapes go through `POST /api/simulate` → `/api/jobs/{id}` and return a `Result` with the bundle's fields, `vs_baseline` included (1.5–2.1 s for 30 nights).
- **Labels:** API and bundle results for lever runs are readable ("extra server at grill + Crowd: x1.10 …") instead of "custom". The label isn't part of the config hash.
- **Narration on every date:** every exported trace has a narration file (8 date baselines + 5 Nov 13 presets, 12 highlight students each).
- **Back-test per dish and per station (second pass):** `dcsim backtest` now also writes `dishes` (recorded vs. plan lb per night served, change %, $ saved per week) and `by_station` on every back-tested night. `web/public/cache/waste/backtest.json` was refreshed from it; the next `make demo-bundle` copies it too. Re-running the back-test moved the headline slightly: **−6.4% (−1.3 to −8.2%), ≈ $824 / week, wins 34 of 39** (was −6.3%, −1.0 to −8.4%, ≈ $812).

## Second pass: connecting the UI to the backend

| Issue | What changed |
| --- | --- |
| U3 replay traces | The Hall uses the bundle trace when there is one, otherwise `GET /api/traces/{hash}?seed=<median>` when the API is up (any bundled scenario or API run). Highlight students come from `/api/traces/{hash}/highlights`. Without the API, an availability panel explains the missing replay; saved comparison results remain usable, with retry and return-to-baseline actions. **Fixed (with API for unbundled animations).** |
| U8 adopt / watch / save | API runs become scenarios `{lever: "api", hash, label}`: Watch in 3D and Adopt work for them. The result is kept in `localStorage`, so an adopted API plan still shows on the Dashboard offline. Adopt and Save also call `POST /api/scenarios` (adopting logs a forecast to the Tiger ledger when configured), and the Lab's Saved list shows scenarios saved on the API. **Fixed.** |
| U10 narration | Any student can be narrated on demand (`/api/narrate/...?stream=false`); highlight students of an API replay are narrated automatically. **Fixed (with API).** |
| U7 Lab features | Built: plain-English box (`/api/ask`, shows the settings and what it couldn't express; Apply and run), menu edits (remove a dish, change popularity), the new-dish form (station, kind, diet tags, similar dishes with *suggest* via `/api/ai/similar-dishes`, popularity, **AI estimate** via `/api/ai/dish-prior`), "Where demand moves" card (served portions per dish vs. baseline), and "Why?" (`/api/explain`). **Mostly fixed;** Optimize and the uncertainty sweep remain (below). |
| U6 Waste per dish | "Top waste dishes" now has the designed columns: recorded lb, with plan, change, $ / week. Station filters now also change the nightly chart (recorded vs. plan for that station). The KPI tiles follow the selected window ("wins 22 of 25 back-tested nights" for the last 4 weeks) with the all-nights range alongside. **Fixed.** |
| U12 alerts | All long-line flags are grouped into one alert ("7 stations have more than 15 people in line at peak: Grill 36, Latino 33, …") on the Dashboard and in Lab side effects. **Fixed.** |
| U13 health 500 | The Vite dev and preview proxies answer `/api/health` with 200 `{ok:false}` when the API is down (other `/api` calls get a 503 with a readable message), so there's no console error. **Fixed.** |
| Smoke-test gap | The selected date is kept in `localStorage`, so a reload no longer resets it to Nov 13. **Fixed.** |

Also fixed while testing the second pass:
- Long API scenario names overflowed the top-bar chip and pushed the page title out; the chip is now capped with an ellipsis and a tooltip.
- Saved-scenario buttons with long names overlapped the Menu section; they now wrap.
- Lab side effects marked an alert as "new" when only its time moved (Paneer run-out 8:24 → 8:30); alerts are now compared by subject (dish / station / diet).
- The default student could be one who arrives hours after the starting time; it now picks the highlight who arrives closest to it.
- Negative dollar ranges showed as "$-11"; now "−$11".
- Missing favicon (404 in the console).

## Third pass: Ask Sam, the waste outlook and function calling (2026-11-14)

| Change | Where |
| --- | --- |
| **Ask Sam** page (`/sam`, top nav, original tricorn-hat avatar): one box for goals (planned in ≤ 5 simulated tries with a goal check and an unseen-night check), what-ifs (simulated as asked vs tonight's plan) and history questions (answered from Tiger through the Tiger MCP server); live tries with Sam's notes and pass/fail chips, Tiger lookups with their SQL, the plan / what-if / answer card with Adopt, Open in the Lab and Watch a night, and a **History** panel of final analyses labeled with prompt, menu date and time asked (API + `localStorage` copy) | `pages/Sam.tsx`, `lib/agent.ts`, `components/SamAvatar.tsx`, `sam.css` |
| The Lab accepts a plan from Sam (**Open in the Lab** passes the request as router state) | `pages/Lab.tsx` |
| The plain-English box shows which method read the question: Gemini function calling, AI JSON settings, or keyword matching | `pages/Lab.tsx` |
| **Insights › Waste history opens on the next 8 nights**: recorded nights, a "Recorded \| Simulated" divider, then each coming night's tonight's-plan bar and its plan point (maroon = adopted from the Lab or Sam, green = recommended, not adopted); KPI cards and a "Plan per night" table with **Change in the Lab**; Export. "Last 4 weeks" / "All recorded · 6 weeks" show the history as before | `pages/WasteOutlook.tsx`, `lib/outlook.ts`, `pages/Waste.tsx` |
| Removed: the synthetic-data banner on Insights (the "Synthetic waste data" chip and labels stay) and the rules line under Ask Sam's box | `pages/Waste.tsx`, `pages/Sam.tsx` |
| Browser test on a ledger-safe stack: `make server-test` + `make web-test-ui`, then `make web-test-api` (10 of 10, incl. two Ask Sam steps); the script refuses an API that would write to the Tiger ledger | `scripts/smoke-api.mjs`, `vite.config.ts` (`DCSIM_API_URL`) |

## Open issues

| ID | Sev | Page | Issue |
| --- | --- | --- | --- |
| U1 | High | All | Figma numbers are placeholders and differ from the real data |
| U2 | Medium | Hall | ~~Only the top-down view is live~~ **Partly fixed (PR #1):** the illustrated isometric view (Canvas) plays the full trace; no orbital 3D |
| U4 | ~~Medium~~ | Dashboard | ~~Dinner only~~ **Closed 2026-11-14:** late night (9 pm–12 am) shown since 0.4.1; breakfast and lunch not needed (ISSUES N4) |
| U5 | ~~Medium~~ | Dashboard | ~~Staffing "needed" isn't computed~~ **Fixed (0.6.2):** "Needed at peak" column + by-hour line for short stations, from `result.staffing`; **verified with data** on the 0.6.2 bundle (8 of 8 staffed stations; e.g. Grill has 2, needs 3 at 6 pm) |
| U7 | Medium | Lab | Optimize and the uncertainty sweep are not built (no engine support) |
| U15 | Medium | All | Several features need the API; offline they explain why and stay disabled |
| U9 | Low | Hall | Replay geometry is approximate |
| U11 | ~~Low~~ | Hall | ~~Engine issues E1/E2 are visible in the replay~~ **Fixed** with E1/E2 in engine 0.4.1 |
| U16 | ~~Low~~ | Waste | ~~Per-dish "$ / week" doesn't add up exactly to the headline~~ **Fixed (0.6.2):** dish rows use the same $ basis and add up to the average simulated week (shown under the table); the headline is the median week |
| U17 | ~~Low~~ | Hall | ~~After the frontend redesign (PR #1) two smoke steps fail~~ **Fixed 2026-11-13:** both were script selectors, not app bugs; smoke passes 23 of 23 |
| U18 | Low | Hall | Pan level in the replay is line pan + hot-hold cabinet, so during a pan swap (30 s) it shows food in a pan a student just found empty |
| U14 | Low | Repo | Stack differs from SPEC §9 (no Tailwind, no Recharts) |
| U19 | ~~Medium~~ | Waste | ~~"Last 8 weeks" never works: it is disabled with only 42 recorded nights and the reason is a hover tooltip~~ **Fixed 2026-11-14:** the period buttons offer only ranges that exist ("Next 8 nights", "Last 4 weeks", "All recorded · 6 weeks") |

Closed in the second pass: U3, U6, U8, U10, U12, U13 (see the table above). Closed later: U11 (engine 0.4.1), U4 and U19 (2026-11-14). Partly fixed: U7.

### U1 · High · Figma numbers are placeholders and differ from the real data
The design mock-ups use made-up values; the built pages show the bundle's real numbers:

| | Figma | Built (bundle) |
| --- | --- | --- |
| Expected students | 1,801 | 2,999 (2,932–3,066) |
| Portions to prepare | 6,580 | 11,005 |
| Peak wait p90 | 10.4 min | 17.4 min |
| Waste reduction, recommended plan | −18% (−11 to −24%) | −6.4% (−1.3 to −8.2%), wins 34 of 39 nights |
| Saved per week | ≈ 670 lb, ≈ $3,600 | ≈ 326 lb, ≈ $824 (range −$11 to $1,285) |
| Lab "smaller batches" vs baseline | −61 portions, stockouts −4.6 min | −46 portions (better, 30/30), stockouts +0.1 min (inconclusive) |

The first demo script (SPEC §12: "≈18% less waste (11–24%), with stockouts also down") didn't match the data. In the back-test, stockouts go **up** about 1.7% under the plan (74.4 → 75.7 min per night).
- **Done 2026-11-13:** SPEC §12 (demo flow) now uses the real numbers and dishes ("about 6% less waste (1–8%) … stockouts about the same"). The pitch slides, if any, need the same change.
- **Still open:** refresh the Figma frames from the bundle so design and app agree, and decide whether engine fixes E1–E3 (ISSUES.md) land before the numbers are final; they will move every figure.

### U2 · Medium · Hall: illustrated isometric replay, no orbital 3D
- **Updated 2026-11-13:** Guided Watch/Try/Compare now runs in the main app. Both the Canvas isometric view and SVG top-down view use the full trace, with continuous Play/Pause, scrubbing, all station menus and pan levels, whole-night queues, and student selection. Closed stations retain their dinner menus.
- Presets show paired 30-night comparisons on every bundled date, even without an animation. Their own replay loads separately when available; loading or failure never hides comparison results. The current bundle includes preset animations only for Nov 13 and baseline animations for all dates. Advanced controls remain in the Lab. Split-screen comparison (SPEC P2) isn't built.
- The scene is an illustrated isometric view. Orbital WebGL/React Three Fiber and the serving-line camera remain future work.

### U4 · Closed · Dashboard: dinner only
- **Partly fixed (engine 0.4.1):** the Dashboard shows dinner + late night (KPI chip, arrivals chart, "late" tags on dishes and in the production plan). Breakfast and lunch below are still open.
- Breakfast and lunch aren't simulated and `ref/arrivals` holds dinner windows only. The Breakfast and Lunch toggles are disabled; the arrivals chart covers 4:30–9:00 pm.
- Weather shows "not connected".
- Needs engine work (other meals), not a UI change.
- **Decided 2026-11-14:** dinner + late night is enough for the demo; breakfast and lunch stay out of scope (the snapshot has their menus if it's picked up later: N4).

### U5 · Medium · Dashboard: staffing "needed" isn't computed
- The design compares planned servers with servers needed for p90 wait under 8 min, by hour. The result only has servers and peak line length per station, so the page shows those two columns and a note.
- Needs an engine output (per-hour queue or needed servers). Computing it in the browser would be a guess, so it was left out.
- **Fixed (0.6.2):** the Staffing dialog has a "Needed at peak" column (servers needed in the busiest hour, hour shown, highlighted when above the plan; hover for every hour) and a by-hour line for each short station. With an older bundle it explains that the data comes with a rebuild. Tested in `src/pages/Staffing.test.ts`.
- **Update (engine 0.5.0):** results now carry `staffing`: servers needed per station and hour ("19" = 7–8 pm) for servers ≤ 85% busy, from the items each station actually served (ISSUES N3). Typed as `Result.staffing?` in `web/src/lib/types.ts`. Still to do: a "needed" column per hour in the Dashboard staffing card, after the bundle is rebuilt with 0.5.0. Shifts can already be tried through the API lever `stations.<id>.shifts`.

### U7 · Medium · What-If Lab: Optimize and uncertainty sweep
- **Optimize** stays disabled: the engine has no optimizer.
- **"Holds up if we're wrong? 27/30"** (uncertainty sweep) isn't shown: the engine has no sweep. A cheap version would re-run the scenario at crowd ×0.9 and ×1.1 through `/api/simulate`; not built yet.
- Dish **swap** is "remove one + add one" (two steps), not a single control.

### U15 · Medium · Several features need the API
Offline (bundle only), these are disabled with a message: the plain-English box, menu edits and new dishes, "Why?", combinations of levers, replays of scenarios without a bundled trace, narration of non-highlight students, Insights › Similar nights (no bundle fallback; it shows "Can't reach the search service" with Try again), and **Ask Sam** (says it needs the API; its History still opens from the browser copy). The waste outlook works offline from `waste/outlook.json`, except nights whose adopted plan ran on the API and was never loaded in this browser. An adopted API plan still shows on the Dashboard offline (its result is kept in the browser), but it can't be replayed.
- **For the demo:** run `make server` next to `make web`, or stick to presets and single levers.

### U9 · Low · Hall replay geometry is approximate
- **Wall/counter crossing fixed 2026-11-13:** both views use cached routes around shared counters, stairs, rooms, walls, and tables. Paths maintain body clearance and use the back-room door openings. Queue interpolation also follows safe paths and no longer jumps between neighboring slots. Recorded event times are preserved.
- The engine records only the seating zone. The exact seat is picked by the UI from the **assumed** table layout.
- Pan levels are rebuilt from `served` events. Since engine 0.6.0 they include the hot-hold cabinet (U18): on the Nov 13 baseline replay, 21 of 91 empty-pan events show level 0 and 68 more are pan swaps completed within 30 s (98% explained).
- "Missed 1st choice" coloring infers the first choice from the first line joined (21.2% vs the engine's 21.0%).

### U11 · Fixed (engine 0.4.1) · Engine issues were visible in the replay
- **Fixed:** E1 (touring lines) and E2 (activity after close) are fixed in engine 0.4.1; after 9 pm the replay now shows the late-night service instead of leftover dinner students.
- E1, students touring too many lines: one student walks past 20+ lines.
- E2, activity after close: dots stay on the map until about 9:30 pm, and some students arrive at 8:53 pm and leave at 10:24 pm. The scrubber runs to close + 30 min.
- These are engine issues (docs/ISSUES.md), not UI bugs, but a viewer will notice them.

### U16 · Low · Per-dish $ doesn't add up exactly to the headline
- The per-dish "$ / week" column uses the mean of the simulated nights and converts recorded pounds to dollars with the dish cost table. Summed over dishes it gives ≈ $718 / week, while the headline (median of seeds, recorded cost column) says ≈ $824. The table's note says the plan is an average; the gap is within the headline's range.
- **Fixed (0.6.2):** two causes. (1) The dish rows rebuilt dollars from rounded pounds; they now use portions × dish cost like the nightly totals. (2) The rows average the simulated weeks while the headline is the median week. The back-test now also exports `usd_per_week_mean` / `lb_per_week_mean`; the dish rows add up to it (engine 0.6.1 data: $1,695 vs $1,696), and the note under the table shows that total next to the median headline ($1,821). Tested in `src/pages/WasteTotals.test.ts`. Also fixed: the Waste tables listed dinner and late-night versions of a dish under the same name; late-night rows now say "(late night)".

### U14 · Low · Stack differs from SPEC §9
- No Tailwind or Recharts. Plain CSS with the Figma tokens and small SVG charts keep the JS at about 100 KB gzipped.
- `dist/` is about 17 MB because it copies `public/cache` (16.7 MB). The bundle is now committed to git (ISSUES T1, fixed).

### U17 · Low · Two Hall smoke steps fail after the redesign
- `node scripts/smoke.mjs` (2026-11-13, engine 0.6.2 bundle 0.4.1): "hall station click" fails because `getByRole('heading', { name: 'Grill' })` matches two headings in the redesigned Hall; "hall other date baseline" times out waiting for its locator. Both also fail on the redesign merge commit (0ba140e) before any later change.
- **Fixed 2026-11-13:** the first step now takes the first of the two "Grill" headings. The second waited for "Inspect Latino" after switching to Nov 18, but Friendsgiving has a Carving line instead of Latino; it now waits for "Inspect Carving". The Hall's date switch itself works (all 8 dates load 11 stations).

### U18 · Low · Hall pan level includes the hot-hold cabinet
- Found 2026-11-13 by `bundle.test.ts` on the 0.6.2 bundle. Since engine 0.6.0 a pan event's `portions` is `pan.level` = portions on the line **plus** the hot-hold cabinet (`model.py`). When the line pan runs out and the cabinet has food, staff swap in a full pan after `pan_swap_min` (0.5 min). A student arriving in that window gets an `empty_pan` event while the replay shows, say, 22 portions.
- **Effect:** for about 30 s per swap the station inspector and the "empty pans" counter disagree with a student's journey. Headline numbers are not affected (swaps are not stockouts by design, ISSUES R5).
- **Fix:** export the on-line level in trace pan events (`recorder.pan`, `metrics.build_trace`), use it in `playback.panTimelines`, then re-export the replays. The test now checks the real rule (level 0, or a `batch_ready` within the swap time) and can go back to "level 0" after the fix.

---

## Full test run after the 0.6.2 bundle (2026-11-13)

Run on `main` at `a392917` plus the rebuilt bundle, with a fresh API (port 8011) and two extra web servers (one with no API behind it), so the servers already running on :8000 and :5173 were not touched.

| Area | Result |
| --- | --- |
| Engine tests (`make test`) | 149 passed, 2 skipped |
| Bundle build (`make demo-bundle`, Gemini) | exit 0: 264 results (33 per date × 8 dates), 14 replays, 168 narrations all `llm` (no template fallbacks, no timeouts), 22 MB |
| Bundle data check (script) | all 264 results: engine 0.6.2, 30 nights, p10 ≤ p50 ≤ p90, no negative values, served ≤ prepared, a 30-pair comparison with a verdict on every non-baseline; all 14 replays have a student count inside the result's range |
| Web unit tests (`make web-test`) | 46 passed (after fixing two tests that hardcoded old-bundle values) |
| Typecheck + production build | clean |
| Browser smoke, no API (`scripts/smoke.mjs`) | 23 of 23 steps, 0 problems (desktop, tablet, phone) |
| Deep offline browser check (44 checks) | 44 passed, 0 console errors: Kitchen plan numbers equal the bundle on all 8 dates; production plan (54 batch-cooked dishes) + CSV; staffing "Needed at peak"; leftovers; alerts; Menu tab; Nov 18 carving; replay on all 8 dates; station inspector; play; top-down view; late night; 12 student stories with narration; three presets whose on-screen changes equal the bundle; Nov 17 comparison without an animation; all 6 Lab presets; Waste history; Similar nights offline message; calibration page; no horizontal scroll on 6 phone pages |
| Browser smoke with the API (`scripts/smoke-api.mjs`) | 8 of 8: ask → run, Why?, remove + new dish, adopt → Kitchen plan, saved list, API replay + narration, any-student narration, lever-grid replay. 2026-11-14: 10 of 10 with the two Ask Sam steps, run on the test stack (`make server-test` + `make web-test-ui`; ISSUES T8) |
| Similar nights with the API (browser) | 6 of 6: Tiger pgvector results in order, menu overlap, unknown menu, empty history, form validation, keyboard-only search |
| API (script) | 28 reference routes 200; a new uncached scenario runs in 2.6 s as a job; cache hit identical; `/compare` equals `vs_baseline`; per-minute series; replay is the median night; highlights; narration; `/ask`; `/explain`; similar dishes; bad requests → 404 / 422 |
| CLI | `dcsim run`, `compare`, `menu`, `bench` (30 nights in 2.2 s); numbers equal the bundle and the screens |

Findings: U18 (new, low); U17 closed; two brittle tests fixed; `backtest.json` per-dish values can differ by 0.1 lb between runs (rounding; headline identical). The API browser test adopts a scenario, which appends two rows to the Tiger forecast ledger each run (append-only); the saved scenario it created was deleted afterwards.
