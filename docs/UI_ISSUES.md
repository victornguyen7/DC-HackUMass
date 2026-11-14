# Web UI: test results and known issues

Status as of 2026-11-13, `web/` built from the Figma file (*DC What-If — Worcester*), engine 0.3.4.
Second pass (same day): the UI now uses the engine API features it ignored before, and the back-test exports per-dish and per-station numbers. See [Second pass](#second-pass-connecting-the-ui-to-the-backend).

Severity: **High** = misleads a viewer or breaks the demo story · **Medium** = a designed feature that is missing or only partly works · **Low** = cleanup.

## How it was tested

| Check | Result |
| --- | --- |
| `npm run build` (TypeScript strict + Vite) | passes (JS 99.5 KB gzipped) |
| `npm test`: 17 unit tests on the real bundle (playback, lever mapping, plain-English settings → API request, grouped alerts, back-test per-station totals) | 17 passed |
| `node scripts/smoke.mjs`: headless Chrome, API **down**, 3 widths (1440 / 1024 / 390), 4 pages, plus interactions | 0 problems on the dev server and on the production build (`vite preview`) |
| `node scripts/smoke-api.mjs`: headless Chrome, API **up** (`make serve`, Gemini configured) | 8 of 8 steps pass, 0 problems |
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
| U3 replay traces | The Hall uses the bundle trace when there is one, otherwise `GET /api/traces/{hash}?seed=<median>` when the API is up (any bundled scenario or API run). Highlight students come from `/api/traces/{hash}/highlights`. Without the API, the banner says why there's no replay. **Fixed (with API).** |
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

## Open issues

| ID | Sev | Page | Issue |
| --- | --- | --- | --- |
| U1 | High | All | Figma numbers are placeholders and differ from the real data |
| U2 | Medium | Hall | Only the top-down view is live; no animated 3D |
| U4 | Medium | Dashboard | ~~Dinner only~~ **Partly fixed:** late night (9 pm–12 am) shown since 0.4.1; breakfast and lunch still missing |
| U5 | Medium | Dashboard | Staffing "needed" isn't computed |
| U7 | Medium | Lab | Optimize and the uncertainty sweep are not built (no engine support) |
| U15 | Medium | All | Several features need the API; offline they explain why and stay disabled |
| U9 | Low | Hall | Replay geometry is approximate |
| U11 | ~~Low~~ | Hall | ~~Engine issues E1/E2 are visible in the replay~~ **Fixed** with E1/E2 in engine 0.4.1 |
| U16 | Low | Waste | Per-dish "$ / week" doesn't add up exactly to the headline |
| U14 | Low | Repo | Stack differs from SPEC §9 (no Tailwind, no Recharts) |

Closed in the second pass: U3, U6, U8, U10, U12, U13 (see the table above). Closed later: U11 (engine 0.4.1). Partly fixed: U4, U7.

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

### U2 · Medium · Hall: only the top-down view is live
- **3D overview** shows the static render with a note. **Serving line** is disabled. **Compare with…** opens the Lab; split-screen compare (SPEC P2) isn't built.
- The animated React Three Fiber scene (SPEC §3.4) is not started.

### U4 · Partly fixed · Dashboard: dinner only
- **Partly fixed (engine 0.4.1):** the Dashboard shows dinner + late night (KPI chip, arrivals chart, "late" tags on dishes and in the production plan). Breakfast and lunch below are still open.
- Breakfast and lunch aren't simulated and `ref/arrivals` holds dinner windows only. The Breakfast and Lunch toggles are disabled; the arrivals chart covers 4:30–9:00 pm.
- Weather shows "not connected".
- Needs engine work (other meals), not a UI change.

### U5 · Medium · Dashboard: staffing "needed" isn't computed
- The design compares planned servers with servers needed for p90 wait under 8 min, by hour. The result only has servers and peak line length per station, so the page shows those two columns and a note.
- Needs an engine output (per-hour queue or needed servers). Computing it in the browser would be a guess, so it was left out.

### U7 · Medium · What-If Lab: Optimize and uncertainty sweep
- **Optimize** stays disabled: the engine has no optimizer.
- **"Holds up if we're wrong? 27/30"** (uncertainty sweep) isn't shown: the engine has no sweep. A cheap version would re-run the scenario at crowd ×0.9 and ×1.1 through `/api/simulate`; not built yet.
- Dish **swap** is "remove one + add one" (two steps), not a single control.

### U15 · Medium · Several features need the API
Offline (bundle only), these are disabled with a message: the plain-English box, menu edits and new dishes, "Why?", combinations of levers, replays of scenarios without a bundled trace, and narration of non-highlight students. An adopted API plan still shows on the Dashboard offline (its result is kept in the browser), but it can't be replayed.
- **For the demo:** run `make serve` next to `make web`, or stick to presets and single levers.

### U9 · Low · Hall replay geometry is approximate
- Walking is a straight line between spots (no nav graph), so a walker can cut across a counter for a moment.
- The engine records only the seating zone. The exact seat is picked by the UI from the **assumed** table layout.
- Pan levels are rebuilt from `served` events. They match the engine's empty-pan events more than 95% of the time.
- "Missed 1st choice" coloring infers the first choice from the first line joined (21.2% vs the engine's 21.0%).

### U11 · Fixed (engine 0.4.1) · Engine issues were visible in the replay
- **Fixed:** E1 (touring lines) and E2 (activity after close) are fixed in engine 0.4.1; after 9 pm the replay now shows the late-night service instead of leftover dinner students.
- E1, students touring too many lines: one student walks past 20+ lines.
- E2, activity after close: dots stay on the map until about 9:30 pm, and some students arrive at 8:53 pm and leave at 10:24 pm. The scrubber runs to close + 30 min.
- These are engine issues (docs/ISSUES.md), not UI bugs, but a viewer will notice them.

### U16 · Low · Per-dish $ doesn't add up exactly to the headline
- The per-dish "$ / week" column uses the mean of the simulated nights and converts recorded pounds to dollars with the dish cost table. Summed over dishes it gives ≈ $718 / week, while the headline (median of seeds, recorded cost column) says ≈ $824. The table's note says the plan is an average; the gap is within the headline's range.

### U14 · Low · Stack differs from SPEC §9
- No Tailwind or Recharts. Plain CSS with the Figma tokens and small SVG charts keep the JS at about 100 KB gzipped.
- `dist/` is about 17 MB because it copies `public/cache` (16.7 MB). The bundle is now committed to git (ISSUES T1, fixed).
