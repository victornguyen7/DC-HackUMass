# DC What-If · web

The main navigation is **Simulation**, **Kitchen plan**, and **Insights**. The simulation uses the Guided layout: **Watch the hall → Try a change → Compare**. The full What-If Lab remains available from advanced settings.
React 18 + Vite + TypeScript, React Router, Zustand. The illustrated isometric hall uses Canvas; charts and the measured top-down plan use SVG. Styles are plain CSS (`src/styles.css` and `src/guided.css`).

The app reads the static demo bundle in `public/cache` (written by `make demo-bundle`), so the demo runs with **no API and no network**.
If the engine API is running (`make serve`, port 8000), Vite proxies `/api` to it and the What-If Lab can also run combinations that aren't in the bundle.

```bash
cd web
npm install
npm run dev          # http://localhost:5173
npm test             # unit tests: playback + bundle checks against public/cache
npm run build        # typecheck + production build in dist/
node scripts/smoke.mjs http://localhost:5173 smoke-out   # headless-Chrome smoke test + screenshots (needs Google Chrome)
node scripts/smoke-api.mjs http://localhost:5173         # same, for the API-backed features (needs `make serve`)
```

## With the API (`make serve`)

The pages check `/api/health` (the dev/preview proxy answers `{ok:false}` when the API is down) and turn these on when it answers:

- **Hall:** replays any scenario without a bundled trace (`/api/traces/{hash}`), highlight students (`/highlights`), and narrates any student on request (`/api/narrate`).
- **What-If Lab:** plain-English box (`/api/ask`), menu edits and the new-dish form with *suggest* and **AI estimate** (`/api/ai/similar-dishes`, `/api/ai/dish-prior`), lever combinations (`/api/simulate`), "Why?" (`/api/explain`), and save/adopt on the API (`/api/scenarios`). API runs can be watched and adopted; their results are kept in `localStorage` so an adopted plan still shows offline.

## Pages

| Route | Workspace | Reads |
| --- | --- | --- |
| `/hall` | Guided simulation | `results/<hash>.json`, `traces/<hash>_s<seed>.json.gz`, `narration/…`, `public/plan/*.svg` |
| `/dashboard` | Kitchen plan | result of the scenario adopted for the date (baseline if none), `ref/dates.json`, `ref/menu/<date>.json` |
| `/waste` | Insights | `waste/history.json`, `waste/backtest.json` (incl. per-dish and per-station back-test), `waste/facts.json` |
| `/lab` | Advanced scenario settings | presets + lever grid from the manifest; `POST /api/simulate` for anything else |

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

Typed in `src/lib/types.ts` (optional, so the 0.4.1 bundle still works): `headline.batch_wait_min / cook_busy_share / empty_wait_min / seat_wait_min / peak_seated` (engine 0.5.0), `staffing` (servers needed per station and hour, UI_ISSUES U5) and `series` (per-minute bands, only from `GET /api/results/{hash}?include=series`, engine 0.6.0). The preset list includes **Forecast reorder**; it has data in the Lab once the bundle is rebuilt (or with the API).

The committed bundle in `public/cache` is from engine **0.4.1**; run `make demo-bundle` once on the final engine.

Known gaps and issues found while testing: **[docs/UI_ISSUES.md](../docs/UI_ISSUES.md)**.
