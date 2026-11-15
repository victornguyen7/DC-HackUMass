# Dineamics · web

The main navigation is **Simulation**, **Kitchen plan**, **Insights** (tabs: **Waste history** and **Similar nights**) and **Ask Sam**. The simulation uses the Guided layout: **Watch the hall → Try a change → Compare**. The full What-If Lab remains available from advanced settings.
React 18 + Vite + TypeScript, React Router, Zustand. The interactive 3D hall uses Three.js; charts and the measured top-down plan use SVG. Styles are plain CSS: `src/styles.css` (tokens and primitives), `src/guided.css` (the redesign), `src/similar-nights.css` and `src/sam.css` (Ask Sam). New UI follows the "Visual system" in [`docs/FRONTEND_REDESIGN_PLAN.md`](../docs/FRONTEND_REDESIGN_PLAN.md).

The 3D hall supports drag to rotate, scroll/pinch to zoom, and a Move mode (or two-finger/right-button drag) to pan. Reset restores the whole floor; the label button hides station overlays. With the canvas focused, arrow keys rotate, +/− zoom, and Home resets. The scene shares the exact floor geometry and replay positions with 2D, and keeps the camera when playback or color filters change. Three.js loads in a separate chunk; instanced furniture/students, capped Retina resolution, and rendering only when needed limit GPU work. WebGL 2 is required for 3D; an explicit top-down fallback keeps the replay accessible if it is unavailable. After pulling this change, run `npm ci` inside `web` to install the new dependency; no separate 3D application is needed.

The app reads the static demo bundle in `public/cache` (written by `make demo-bundle`), so the demo runs with **no API and no network**.
If the engine API is running (`make server`, port 8000), Vite proxies `/api` to it and the What-If Lab can also run combinations that aren't in the bundle. `DCSIM_API_URL` points the proxy at another API (`make web-test-ui` serves the app on :5174 against the ledger-safe test API from `make server-test`).

```bash
cd web
npm install
npm run dev          # http://localhost:5173
npm test             # unit tests: playback + bundle checks against public/cache
npm run build        # typecheck + production build in dist/
node scripts/smoke.mjs http://localhost:5173 smoke-out   # headless-Chrome smoke test + screenshots (needs Google Chrome)
node scripts/smoke-api.mjs http://localhost:5174         # API-backed features + Ask Sam (needs `make server-test` + `make web-test-ui`)
```

## With the API (`make server`)

The pages check `/api/health` (the dev/preview proxy answers `{ok:false}` when the API is down) and turn these on when it answers:

- **Hall:** replays any scenario without a bundled trace (`/api/traces/{hash}`), highlight students (`/highlights`), and narrates any student on request (`/api/narrate`).
- **What-If Lab:** plain-English box (`/api/ask`; the answer says whether Gemini function calling, JSON settings or keyword matching read it), menu edits and the new-dish form with *suggest* and **AI estimate** (`/api/ai/similar-dishes`, `/api/ai/dish-prior`), lever combinations (`/api/simulate`), "Why?" (`/api/explain`), and save/adopt on the API (`/api/scenarios`). API runs can be watched and adopted; their results are kept in `localStorage` so an adopted plan still shows offline.
- **Ask Sam** (`/sam`, `src/pages/Sam.tsx`, `src/lib/agent.ts`): `POST /api/agent` and its SSE stream for goals, what-ifs and history questions. **Recent chats** opens the saved analyses in a dialog; it reads `GET /api/agent/history` and keeps a copy of each final analysis in `localStorage` (last 40) so it opens with the API down. Adopting a Sam plan or what-if also keeps its result locally, for the waste outlook. Without the API the page offers saved chats and connection details.

Ask Sam uses the supplied `avatar.png` artwork (served from `public/sam-avatar.png`) in navigation and messages. Three short prompt suggestions fill an editable message; **Ask Sam** starts the run. Attempts show a concise outcome and expand for metrics, settings, and affected dishes. The final recommendation appears before the attempt history, with unmet goals and extra-night validation visible. Cook sheets, history queries, and technical metadata stay behind labeled disclosures. The layout supports landscape iPads and narrow screens, with 44px primary controls and a 16px message field. No extra dependencies are required.
- **Insights › Waste history (next 8 nights):** `waste/outlook.json` from the bundle, else `GET /api/waste/outlook`; plans adopted from API runs (Lab, Sam) read `waste_lb` from `/api/results/{hash}` or the saved copy (`src/lib/outlook.ts`, `src/pages/WasteOutlook.tsx`).
- **Insights › Similar nights:** `GET /api/similar-nights`. This page has no bundle data: without the API it says the search service isn't answering and offers **Try again**.

## Pages

| Route | Workspace | Reads |
| --- | --- | --- |
| `/hall` | Guided simulation | `results/<hash>.json`, `traces/<hash>_s<seed>.json.gz`, `narration/…`, `public/plan/*.svg` |
| `/dashboard` | Kitchen plan | result of the scenario adopted for the date (baseline if none), `ref/dates.json`, `ref/menu/<date>.json` |
| `/waste` | Insights › Waste history: opens on the next 8 nights (each night's adopted plan, else the recommended plan, vs tonight's plan, next to the last recorded nights); "Last 4 weeks" / "All recorded" show the history and back-test | `waste/outlook.json` (or `GET /api/waste/outlook`), `waste/history.json`, `waste/backtest.json` (incl. per-dish and per-station back-test), `waste/facts.json`; adopted plans from the store |
| `/sam` | Ask Sam: goal, what-if or history question; live tries, Tiger lookups, plan / what-if / answer, History | `POST /api/agent` + SSE, `GET /api/agent/history[/{id}]`, `localStorage` copy |
| `/lab` | Advanced scenario settings | presets + lever grid from the manifest; `POST /api/simulate` for anything else |
| `/similar-nights` | Insights › Similar nights | `manifest.json` (menu dates), `GET /api/similar-nights` |

Landscape tablets use a viewport-height Guided workspace: station details scroll independently, and the hall, playback controls, and scrubber stay together. **Replay options & legend** opens the scenario selector and metadata without adding a permanent panel. The scene fits its actual floor bounds and keeps station labels apart in short viewports. Touch controls are at least 44px for primary actions; touch selects use 16px text to avoid focus zoom.

Kitchen plan uses three-column summaries, a separate Menu tab, and scrollable detail dialogs with a persistent Close button. Long production dish names wrap; wide tables scroll inside their card. Insights switches the station pill list to a native select on tablets. Safe-area insets and dynamic viewport height support browser chrome and rotation. Phone width places the hall and playback controls above station details; the full station directory remains available when map labels are hidden.

## Hall playback

The main app is at `http://localhost:5173/hall`. `work/design/dining-simulation.html` is the earlier standalone concept, with limited snapshots; it is not the full app.

- Play advances through the complete selected trace at 1×, 10×, 30×, or 60×. Pause, scrub, and restart are supported; arrow keys move five minutes and Space toggles playback on the slider. An ended replay restarts when Play is pressed.
- Both hall views use the same student poses. All 12 stations remain accessible (Desserts and Breads share one inspector), with complete dish names, pan levels, and whole-night queues. Closed stations retain their dinner menus for inspection.
- Selecting a student pauses playback and opens their recorded journey. Choosing a scenario opens its saved, paired 30-night comparison and loads its own animation when available. **Compare works on every bundled date without the API.** The current bundle includes baseline animations for every date, but preset animations only for Nov 13. Other dates show the saved comparison alongside a replay-availability panel, with actions to retry the API or watch the current plan; a missing animation never blocks the results.
- During playback, long queues and empty pans trigger one dismissible, temporary notification at a time. Dismissed conditions remain quiet until they clear; alerts are rate limited.

`src/lib/playback.ts` turns the engine trace (SPEC §8.3) into positions on the top-down plan (`public/plan/worcester-2f-plan.svg`, 849×465 px, 13.37 px/m, same frame as `data/worcester/layout`).

- A student dwells at an event's spot and follows a cached path to the next spot at the end of the gap (normally 70 m/min, fitted to the recorded event times). `hallGeometry.ts` shares counters, rooms, stairs, partitions, and tables with both views; `navigation.ts` routes around them with body clearance and uses the three back-room doorways. No straight-line fallback crosses a blocked area.
- A student dwells at an event's spot and walks to the next spot at the end of the gap (70 m/min, straight line).
- A `balk` or `empty_pan` at the same instant as the previous event is a glance from where the student stands (engine 0.4.1: students choose lines from a distance), so it keeps their position instead of jumping to that station.
- Late night (engine 0.4.1+): the timeline runs to `result.close` (12 am); station cards and empty-pan flags show only the dishes of the period on at that time (`dishWindow` in `lib/format.ts`). The Nov 18 `carving` line has an assumed queue spot beside Display.
- In a line, they move from their slot at join time to the counter at service time. Queue heads and directions come from the measured plan model (Tandoor's line runs east from the east frontage of the center island).
- Seats: deterministic 4-top seat in the zone the engine chose (`main` or `back`); table positions are the assumed ones in `src/lib/zones.json`.
- Pan levels: engine pan events carry the level after the event; single takes aren't exported, so the UI subtracts the trace's `served` events.

## Kitchen plan and Insights

Kitchen plan opens on Overview, with one service-hours line. Its Menu tab (`/dashboard?view=menu`) contains the station menu. Production plan, Staffing, and Left at close open in accessible dialogs; the full tables, print, and CSV controls are retained. Expected arrivals includes **View alerts**, with links into the replay at the relevant time. Insights' Top waste dishes table has three columns: Dish, Change, and $ / week.
## Engine fields the pages don't show yet

Typed in `src/lib/types.ts` (optional, so an older bundle still works): `headline.batch_wait_min / cook_busy_share / empty_wait_min / seat_wait_min / peak_seated` (engine 0.5.0), `staffing` (servers needed per station and hour, UI_ISSUES U5) and `series` (per-minute bands, only from `GET /api/results/{hash}?include=series`, engine 0.6.0). The preset list includes **Forecast reorder**; it has data in the Lab since the 0.6.2 bundle.

The bundle in `public/cache` was rebuilt on engine **0.6.2** (2026-11-14 04:05 UTC: 264 results, 14 replays, 168 narrations). Rebuild it with `make demo-bundle` after any engine or data change; two tests read values from it (`bundle.test.ts`, `Hall.test.tsx`) and adapt to a new bundle.

Known gaps and issues found while testing: **[docs/UI_ISSUES.md](../docs/UI_ISSUES.md)**.

## Similar nights

`/similar-nights` (Insights › Similar nights) finds recorded nights before a date with the most similar conditions. API contract, vector design and verification: [`docs/SIMILAR_NIGHTS.md`](../docs/SIMILAR_NIGHTS.md).

- Files: `src/pages/SimilarNights.tsx` (page), `src/lib/similarNights.ts` (types, query builder, error classification), `src/similar-nights.css`, `InsightsTabs` in `src/components/Shell.tsx` (also shown on `/waste`). The Insights link stays highlighted on both routes.
- Defaults: the date selected elsewhere in the app, 4,000 students, rain, not an event night, that date's prepared menu when there is one (the manager can switch to Unknown menu, which omits `menu_date`), 5 results.
- States: before a search, loading (skeleton, button disabled), results (summary of recorded outcomes with median and range, then ranked match cards), no earlier nights, invalid input (422 message), history unavailable (the API's fixed 503 detail), and API not answering (proxy 503, network error or 404), each with **Try again** and the inputs kept. No terminal commands in user-facing text.
- Tests: `src/pages/SimilarNights.test.tsx` (9).
