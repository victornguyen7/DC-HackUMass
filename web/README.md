# DC What-If · web

The four pages from the Figma file (*DC What-If — Worcester*): **Hall**, **Dashboard**, **Waste Record**, **What-If Lab**.
React 18 + Vite + TypeScript, React Router, Zustand. Charts are small hand-written SVG; styles are plain CSS with the Figma tokens (`src/styles.css`).

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

| Route | Figma frame | Reads |
| --- | --- | --- |
| `/hall` | 01 Hall · Desktop 1440 (7:348) | `results/<hash>.json`, `traces/<hash>_s<seed>.json.gz`, `narration/…`, `public/plan/*.svg` |
| `/dashboard` | 02 Dashboard · Desktop 1440 (10:348) | result of the scenario adopted for the date (baseline if none), `ref/dates.json`, `ref/menu/<date>.json` |
| `/waste` | 03 Waste Record · Desktop 1440 (10:1386) | `waste/history.json`, `waste/backtest.json` (incl. per-dish and per-station back-test), `waste/facts.json` |
| `/lab` | 04 What-If Lab · Desktop 1440 (10:1827) | presets + lever grid from the manifest; `POST /api/simulate` for anything else |

Tablet (≤1200 px) collapses the sidebar to icons like the Figma *Tablet 1024* frames; phone width stacks everything.

## Hall playback

`src/lib/playback.ts` turns the engine trace (SPEC §8.3) into positions on the top-down plan (`public/plan/worcester-2f-plan.svg`, 849×465 px, 13.37 px/m, same frame as `data/worcester/layout`).

- A student dwells at an event's spot and walks to the next spot at the end of the gap (70 m/min, straight line).
- In a line, they move from their slot at join time to the counter at service time. Queue heads and directions come from the measured plan model (Tandoor's line runs east from the east frontage of the center island).
- Seats: deterministic 4-top seat in the zone the engine chose (`main` or `back`); table positions are the assumed ones in `src/lib/zones.json`.
- Pan levels: engine pan events carry the level after the event; single takes aren't exported, so the UI subtracts the trace's `served` events.

Known gaps and issues found while testing: **[docs/UI_ISSUES.md](../docs/UI_ISSUES.md)**.
