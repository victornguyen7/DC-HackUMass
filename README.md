# Dineamics · Worcester

**A "what-if" simulator for the Worcester Dining Commons at UMass Amherst** (HackUMass 2026).
It simulates hundreds of dinner nights on the **real menu**, student by student, so a kitchen manager can test a decision (smaller batches, an extra server, a new dish, a busier night) **before** trying it on real students. It then says how many pans to cook, and when.

> **The pitch in one line:** other projects simulate one lunch with AI students. We simulate 30+ nights per decision and hand the manager a cook sheet, with ranges and an honest "better / worse / can't tell" verdict.

This README is the catch-up guide. Each section links to the detailed doc.

---

## Contents
1. [What the app does (3 destinations)](#1-what-the-app-does)
2. [Quick start](#2-quick-start)
3. [How it works](#3-how-it-works)
4. [Repository map](#4-repository-map)
5. [Everyday tasks](#5-everyday-tasks)
6. [Configuration and secrets](#6-configuration-and-secrets)
7. [Testing](#7-testing)
8. [What's real and what's made up](#8-whats-real-and-whats-made-up)
9. [Current status and known issues](#9-current-status-and-known-issues)
10. [Docs index](#10-docs-index)
11. [Glossary](#11-glossary)
12. [Rules of the road](#12-rules-of-the-road)
13. [Troubleshooting](#13-troubleshooting)
14. [Branches and merges](#14-branches-and-merges)

---

## 1. What the app does

A web app in `web/`. It was first built from the Figma file (*DC What-If — Worcester*), then redesigned (PR #1, [`docs/FRONTEND_REDESIGN_PLAN.md`](docs/FRONTEND_REDESIGN_PLAN.md)) around **three destinations** in a slim top navigation. The routes didn't change.

| Destination | Route | What a manager sees | Main data |
| --- | --- | --- | --- |
| **Simulation** | `/hall` | A guided workspace: **Watch the hall → Try a change → Compare**. The night plays back on an illustrated isometric hall (or the measured top-down plan): every dot is a student entering, queuing, getting a plate or bowl, sitting, leaving. Play / pause, 1×–60× speed, a scrubber, one temporary alert at a time for long lines and empty pans. Select a station for its dishes, pan levels and queue over the night; select a student for their journey and an AI-written story. **Try a change** offers presets (smaller batches, an extra Grill server, a busier night); **Compare** shows waste, waiting and dish availability against the baseline with ranges and verdicts, and works on every bundled date without the API. | Median-night **trace** (per-student events) + result |
| ↳ **What-If Lab** | `/lab` | The advanced settings behind Simulation: batch-plan sliders per dish, crowd size, servers per station, menu edits (remove a dish, change popularity, **add a new dish with an AI estimate**), or **ask in plain English** (with a Gemini key the box reads the question through **Gemini function calling**: one function per setting, real station and dish ids only; JSON and keyword fallbacks). Run 30 paired nights and see results vs. baseline with verdicts, the cook sheet with changed rows, side effects, when pans run empty, where demand moves, and "Why?". **Optimize** searches 80 kitchen plans and shows the waste-vs-stockouts trade-off (each plan re-checked on nights the search never saw; **Load into builder**), and **Plausible worlds** tests a plan when the crowd, rush timing and dish popularity are off, with a tornado of which guess matters most. With Tiger set up, every Optimize run is kept there: the Optimize card lists past searches for the date (open one, Load into builder) and the levers that keep showing up on the frontier, and the new-dish form shows what look-alike dishes really sold. Then **Adopt** it for the date or watch the replay. | Bundle, or the API for anything not precomputed (Optimize and Plausible worlds need the API) |
| **Kitchen plan** | `/dashboard` | Tonight's plan for the chosen date: expected students, peak time, portions to prepare, forecast waste, dishes at risk, peak wait, all with ranges. Arrivals chart with **View alerts** (links into the replay), a Menu tab (`?view=menu`), and the **production plan** (Print, CSV), staffing and what will be left at close in dialogs. Shows the plan adopted for that date, or the baseline. | Result of the adopted scenario |
| **Insights › Waste history** | `/waste` | Opens on **the next 8 nights**: simulated waste per night with the plan adopted for that night (from the Lab or Ask Sam; else the recommended plan) against tonight's plan as written, next to the last recorded nights, in the same units (lb, expired + left at close). "Last 4 weeks" / "All recorded" show the recorded history (**synthetic**, clearly labeled) vs. what the recommended plan would have done on each past night: reduction with range, $ per week, stockout trade-off, where waste comes from, top waste dishes with "with plan" numbers, station filters, adopted-plan tracker, CSV export. | Synthetic history + back-test |
| **Ask Sam** | `/sam` | **Sam the Minuteman**, a Gemini planning agent (the UMass mascot as an original tricorn-hat avatar). Give a **goal** (*"cut waste 20% without any vegetarian dish running out"*): Sam looks up the recorded history in Tiger Data (through the Tiger MCP server), proposes batch, staffing and menu changes, simulates each try (**at most 5**), reads a fixed goal check (code, not AI), adjusts, then hands back a plan with its reasoning, re-checked on 30 unseen nights. Or type a **what-if** (*"what if we add a Grill server and it's 10% busier?"*): simulated as asked against tonight's plan. Or ask a **question** about past nights (*"which dishes ran out on rainy Fridays?"*), answered from Tiger. Live steps, Adopt / Open in the Lab / Watch a night, and a **History** of final analyses labeled with prompt and date. Needs the API. | `POST /api/agent` (SSE steps), `GET /api/agent/history` |
| **Insights › Similar nights** | `/similar-nights` | Describe a night (date, expected students, weather, event night, optionally a prepared menu) and get the recorded nights before it with the most similar conditions, most similar first, with the waste and stockouts each one actually had (recorded outcomes, not a forecast). Searches Tiger with pgvector, or the local synthetic files when Tiger isn't set up. | `GET /api/similar-nights` ([`docs/SIMILAR_NIGHTS.md`](docs/SIMILAR_NIGHTS.md)) |

There is also a standalone **calibration page** (`web/public/calibration.html`, open `/calibration.html` on the dev server) that checks the forecasts against held-out nights (see `docs/TIGER.md`). The live Tiger dashboard is not part of the web app: the API serves it at `http://127.0.0.1:8000/tiger` (`docs/TIGER.md` §8).

**Two ways to run it:**
- **Offline (demo-safe):** the pages read a precomputed **static bundle** in `web/public/cache` (results, replays, narrations, back-test). No backend, no network.
- **With the API** (`make server`): everything above, plus any combination of settings, replays of any scenario, narration of any student, the plain-English box, menu edits, new dishes, "Why?" and **Ask Sam**. Similar nights always needs the API. Features that need the API turn themselves off, or offer a retry, when it isn't running.

---

## 2. Quick start

### Prerequisites
| Tool | Version used | Why |
| --- | --- | --- |
| Python | ≥ 3.13 | engine (managed by `uv`) |
| [uv](https://docs.astral.sh/uv/) | 0.13 | Python env + deps (`engine/uv.lock`) |
| Node.js | 24 (18+ should work) | web app |
| Google Chrome | any recent | only for the browser smoke tests |

### Live demo
**https://155-138-211-252.sslip.io**: the app and API on a Vultr server (since 2026-11-14). How it's deployed and how to update it: [`deploy/README.md`](deploy/README.md).

### Just look at the app (2 minutes, no backend)
```bash
cd web
npm install
npm run dev            # http://localhost:5173  (or `make web` from the repo root)
```
The demo bundle is committed in `web/public/cache`, so this works straight after cloning.

### Full setup (engine + API + app)
```bash
# 1. engine
cd engine
uv sync                                  # creates engine/.venv
touch .env                               # optional: add API keys, see §6
uv run pytest -q                         # should pass
cd ..

# 2. API (terminal 1)
make server                               # FastAPI on http://127.0.0.1:8000, docs at /docs

# 3. web (terminal 2)
make web                                 # http://localhost:5173, proxies /api to :8000
```

Try this in the What-If Lab (`/lab`, the advanced settings behind Simulation) with the API running: type *"one more server at Grill and 10% busier"* into **Ask in plain English**, press **Apply and run**, then watch the replay. Or open **Insights › Similar nights** and press **Find similar nights**.

### Command-line only (no web)
```bash
cd engine
uv run dcsim run --date 2026-11-13                       # 30 nights: medians and 10–90% ranges
uv run dcsim compare --preset smaller_batches --date 2026-11-13
uv run dcsim menu --date 2026-11-13                      # what's on, by station
uv run dcsim ask "one more server at Grill and 10% busier"                 # plain English -> settings (function calling)
uv run dcsim agent "cut waste 20% without any vegetarian dish running out" # Sam plans (--tries 1-5)
uv run dcsim agent "what if we add a Grill server and it's 10% busier?"    # Sam runs a what-if
uv run dcsim agent "Which dishes ran out on rainy Fridays?"                # Sam answers from Tiger
```

---

## 3. How it works

```
 real menu snapshot (umassdining.com)          team answers + public sources
 data/raw/menu/*.json                          data/worcester/*.json, ASSUMPTIONS.md
          │  dcsim prepare                               │
          ▼                                              ▼
 per-date menus + baseline batch plans ─────►  ScenarioConfig (frozen, hashed)
 data/worcester/menus, kitchen                           │   + preset / levers from the Lab
                                                         ▼
                                   ┌────────── SimPy engine (engine/src/dcsim) ──────────┐
                                   │ arrivals in class-change waves → personas pick dishes │
                                   │ → lines per station → pans that run out, expire,      │
                                   │ get re-cooked → seats → exit. One night per seed.     │
                                   └──────────────────────────────────────────────────────┘
                                                         │ 30 seeds (common random numbers)
                                                         ▼
       results (headline ranges, cook sheet, flags, vs-baseline verdicts) + traces (per-student events)
                    │                                    │
          cache/results.sqlite  ◄── FastAPI (/api/…) ──► │──► dcsim bundle ──► web/public/cache (static)
                                                         ▼
                                      React app (web/) plays back, never simulates
```

Key ideas:
- **The browser never simulates.** It shows results and replays traces. All numbers come from the engine.
- **Paired comparisons.** A scenario and its baseline run on the same seeds (same students, same arrival times), so a difference is the decision's effect, not luck. Each metric gets a win rate and a verdict: *better*, *worse* or *inconclusive*.
- **Everything is cached by config hash.** The hash covers the config, the engine version and the data version, so a stale result can't be served. Slider values snap to steps (crowd 5%, popularity 0.1) so nearby positions reuse cached results.
- **One night = dinner 4:30–9:00 pm + late night 9:00 pm–12:00 am** (engine 0.4.1). At 9 pm dinner pans stop taking new students, lines already formed are served, and the late-night menu starts.
- **Students decide from where they stand** (0.4.1): they skip long lines and visible empty pans without walking over, then stay in the line they pick.
- **The kitchen has limits** (0.5.0): batches wait for a free cook and a free oven, kettle, fryer, etc. (placeholder counts). Servers can change by hour (`shifts`), and results include "servers needed" per station and hour. Seats are counted every night (`peak_seated`); seat limits are off until someone counts the real seats.
- **The LLM is never inside the simulation.** It writes student stories, turns plain English into settings (Gemini function calling), estimates how much each type of student would like a new dish (as numbers you can see), and explains results. Every LLM output is checked and has a non-AI fallback. See `docs/LLM.md`.
- **Ask Sam proposes, the engine decides.** Sam (Gemini function calling over the simulator and the Tiger MCP server) only proposes settings; every number comes from the engine or a read-only Tiger query, and a fixed check in code says whether the goal is met. Without a key, a rule-based search takes the same steps. See `docs/SPONSORS.md` §2.
- **The waste page looks ahead.** Insights opens on the next 8 nights, simulated with each night's adopted plan, in the recorded history's units (`outlook.py`), with the recorded past one click away.

Deeper design: `docs/ENGINE.md` (engine and API), `docs/SPEC.md` (product).

---

## 4. Repository map

```
HackUMass2026/
├── README.md              ← you are here
├── Makefile               ← shortcuts for everything (run `make <target>` from the root)
├── engine/                Python package `dcsim`: simulation, API, CLI, LLM layer
│   ├── src/dcsim/
│   │   ├── config.py        ScenarioConfig, hashing, ENGINE_VERSION
│   │   ├── data.py          loads a date's config from data/worcester
│   │   ├── arrivals.py      arrival waves (dinner + late night)
│   │   ├── population.py    personas, diets, dish preferences
│   │   ├── model.py         the SimPy night: lines, pans, kitchen, seating
│   │   ├── recorder.py, metrics.py   events → per-night metrics + traces
│   │   ├── runner.py        many nights, summaries, paired comparison
│   │   ├── planner.py       baseline batch plans
│   │   ├── presets.py       the 5 preset what-ifs
│   │   ├── levers.py        What-If Lab request → config
│   │   ├── optimize.py      Optimize plan: Optuna search, waste-vs-stockouts frontier, unseen-seed check
│   │   ├── worlds.py        plausible worlds (uncertain crowd, rush, popularity) + waste tornado
│   │   ├── searchlog.py     plan searches kept in Tiger (plan_searches, search_trials, search_worlds)
│   │   ├── nightsweep.py    the 500-night question: many simulated nights of one date in Tiger, minutes out of stock per dish (`dcsim tsdb sweep`)
│   │   ├── dishsearch.py    dish look-alikes (DiskANN), what they sold, dish-popularity check (`dcsim tsdb dishes`)
│   │   ├── fleet.py         Vultr fleet: temporary servers run every date's optimizer at once (`dcsim fleet`)
│   │   ├── reports.py       cook sheet, alerts, station table
│   │   ├── service.py, store.py, api.py   caching, SQLite, FastAPI
│   │   ├── synthetic.py, backtest.py      synthetic waste history + back-test
│   │   ├── outlook.py       future waste (lb) per coming night and scenario, for the Insights outlook
│   │   ├── calibration.py, tsdb.py        held-out calibration, Tiger Data ledger, aggregates, benchmark
│   │   ├── tiger_sql.py     generates engine/sql/tiger_website.sql (the whole Tiger schema as one console script)
│   │   ├── static/tiger.html  live Tiger dashboard, served by the API at /tiger (not part of the web app)
│   │   ├── similar_nights.py  Similar nights for the page and API (nightsearch's vectors; Tiger DiskANN or local files)
│   │   ├── nightsearch.py   the similar-night search: 77-dim vectors, DiskANN index, held-out check (`dcsim tsdb similar`)
│   │   ├── bundle.py        writes the static demo bundle
│   │   ├── cli.py           the `dcsim` command
│   │   └── ai/              LLM client (Gemini/Groq/mock), narration, ask (function calling), dish prior, explain,
│   │                        similar dishes, agent.py (Ask Sam), tiger_mcp.py (Sam's Tiger MCP client + SQL guard)
│   ├── tests/               pytest (engine, API, AI with fakes, calibration, bundle, golden baseline, team targets)
│   └── .env                 your keys (not committed)
├── web/                   React app (see web/README.md)
│   ├── src/pages/           Hall.tsx (Simulation), Lab.tsx, Dashboard.tsx (Kitchen plan), Waste.tsx + WasteOutlook.tsx +
│   │                        SimilarNights.tsx (Insights), Sam.tsx (Ask Sam)
│   ├── src/lib/             data.ts (bundle + API client), playback.ts (trace → dots), navigation.ts + hallGeometry.ts (walking paths),
│   │                        useReplayClock.ts, similarNights.ts, agent.ts (Sam stream + history), outlook.ts (plan per coming night),
│   │                        store.ts, format.ts, types.ts
│   ├── src/components/      Shell (top navigation, Insights tabs, top bar), HallScene (isometric canvas), DetailDialog, ReplayAlert,
│   │                        Charts, SamAvatar
│   ├── src/*.css            styles.css (tokens), guided.css (redesign), similar-nights.css, sam.css
│   ├── public/cache/        STATIC DEMO BUNDLE (committed, ~22 MB)
│   ├── public/plan/         floor-plan SVGs (top-down + static 3D render)
│   ├── public/calibration.html   standalone forecast-calibration page
│   └── scripts/             smoke.mjs (API down), smoke-api.mjs (API up)
├── data/                  inputs (see data/README.md)
│   ├── raw/                 original uploads, never edited (menu snapshot, research, floor plan)
│   ├── sources/             research split by topic, with a citation registry
│   ├── worcester/           ENGINE INPUTS: dc.json, stations.json, personas.json, menus/, kitchen/, layout/, ASSUMPTIONS.md
│   └── synthetic/worcester/ SYNTHETIC "recorded" history + backtest.json
├── docs/                  design docs and issue lists (see §10)
├── deploy/                Vultr server: setup.sh (server), push-secrets.sh (laptop), tiger-password.sh, dcsim.service, Caddyfile
├── survey/                student survey draft (feeds persona calibration)
├── work/design/           earlier standalone hall concept (dining-simulation.html), not the app
├── cache/                 local results cache (SQLite + traces + Sam's history); not committed
└── cache-test/            copy of the cache for `make server-test` (git-ignored, recreated on each start)
```

---

## 5. Everyday tasks

All targets run from the repo root; each one has a short comment in the `Makefile`.

| I want to… | Run |
| --- | --- |
| Start the API | `make server` (add `--warm` via `cd engine && uv run dcsim serve --warm` to precompute every date) |
| Start the web app | `make web` |
| Run engine tests | `make test` |
| Run web unit tests | `make web-test` |
| Build the web app | `make web-build` (output in `web/dist`) |
| Browser-test the API features | `make server-test` + `make web-test-ui` (test API on :8001 with its own cache copy and no Tiger ledger writes; app on :5174), then `make web-test-api` |
| Re-read the menu snapshot into per-date menus and plans | `make prepare` |
| Regenerate the synthetic waste history | `make waste` |
| Re-run the back-test | `make backtest` |
| **Rebuild the demo bundle** (after any engine or data change) | `make demo-bundle` (≈ 4 min + narration time; `make demo-bundle-quick` for presets only) |
| Drop cached results from old engine versions | `make cache-clean` |
| Narrate highlight students | `make narrate` |
| Tiger Data calibration | `make calibrate` (needs Tiger credentials, see `docs/TIGER.md`) |
| Load the current synthetic history into Tiger | `cd engine && uv run dcsim tsdb load` (after `make waste`) |
| Live Tiger dashboard | `make server`, then open `http://127.0.0.1:8000/tiger` (needs Tiger; `docs/TIGER.md` §8) |
| Raw table vs continuous aggregate timings | `make tsdb-bench` |
| One SQL query across the Tiger stack | `cd engine && uv run dcsim tsdb query stack` |
| Ask Sam for a plan, a what-if or a history answer | the **Ask Sam** page (needs `make server`), or `cd engine && uv run dcsim agent "<goal, what-if or question>"` |
| Refresh the waste outlook in the bundle (after new runs or adopting presets) | `cd engine && uv run dcsim bundle --waste-only` (seconds) |
| Search kitchen plans for less waste (frontier + unseen-seed check) | Lab › **Optimize** (needs `make server`), or `cd engine && uv run dcsim optimize --date 2026-11-13` |
| Test a plan when the crowd, rush and popularity guesses are off | Lab › **Plausible worlds**, or `cd engine && uv run dcsim worlds --date 2026-11-13 --preset smaller_batches` |
| Keep an optimizer or worlds run in Tiger, and see which levers keep showing up | automatic from the Lab when Tiger is set up (not on `make server-test`); CLI: `uv run dcsim optimize --date 2026-11-13 --save`, then `uv run dcsim tsdb searches` |
| Optimize every menu date at once on temporary Vultr servers (live on `/tiger`) | `cd engine && uv run dcsim fleet optimize --dry-run`, then without `--dry-run` (needs `VULTR_API_KEY`; deploy/README.md "Fleet") |
| See what dishes like a new one really sold, and check dish popularity | Lab › Menu › Add a new dish (needs `make server` + Tiger), or `cd engine && uv run dcsim tsdb dishes --name "Butter Chicken" --role entree` |
| Find past nights like a given one | Insights › Similar nights (needs `make server`), or `cd engine && uv run dcsim tsdb similar --date 2026-11-14 --weather rain` |

### Changing the simulation (important)
1. Change the logic in `engine/src/dcsim/` or an input in `data/worcester/`.
2. If you changed **logic**, bump `ENGINE_VERSION` in `engine/src/dcsim/config.py`. Input files are covered automatically by the data version in the hash.
3. `make test`, then `make cache-clean` and `make demo-bundle` so the web app shows the new numbers.
4. Update `data/worcester/ASSUMPTIONS.md` if you changed an assumption, and note any headline change in `docs/ISSUES.md` / `docs/UI_ISSUES.md`.

### Adding a preset
Write a pure function `ScenarioConfig → ScenarioConfig` in `engine/src/dcsim/presets.py`, register it in `PRESETS`, add a label to `PRESET_LABELS` in `web/src/lib/format.ts`, rebuild the bundle.

### Working on the UI
`cd web && npm run dev`. Pages read the bundle through `src/lib/data.ts`; API calls are in the same file and are always optional. Run `npm test` and `node scripts/smoke.mjs http://localhost:5173 smoke-out` before pushing. Details: `web/README.md`.

---

## 6. Configuration and secrets

Put keys in **`engine/.env`** (git-ignored, keep it that way; never paste keys into chats or commits).

| Variable | Needed for | Notes |
| --- | --- | --- |
| `GEMINI_API_KEY` | narration, plain-English box (function calling), Ask Sam, AI estimate, Why?, similar dishes | Default provider when set. Free tier: 15 requests/min; the app falls back to templates, keyword matching or Sam's rule-based search when busy |
| `GROQ_API_KEY` | alternative LLM provider | use with `LLM_PROVIDER=groq` |
| `LLM_PROVIDER` | choose `gemini`, `groq` or `mock` | `mock` = no network, deterministic (tests always use it) |
| `DCSIM_LLM_MODEL` | override the model | see `docs/GEMINI.md` |
| `TIGER_SERVICE_ID` or `DCSIM_TSDB_URL` | Tiger Data forecast ledger + calibration; Sam's history lookups use `TIGER_SERVICE_ID` with the Tiger CLI login (`tiger mcp start`) | optional; adopting still works without it |
| `DCSIM_AGENT_MODEL`, `DCSIM_AGENT_EFFORT` | Sam's model and thinking level | default `strong` (`gemini-4-hydrogen`; falls back to the main model) and `low` |
| `DCSIM_AGENT_MAX_RUNNING` | how many Sam runs the API allows at once | default 2; more → 429 |
| `DCSIM_TIGER_MCP`, `DCSIM_TIGER_CMD` | Sam's Tiger lookups | `0` turns them off; `DCSIM_TIGER_CMD` = path to the Tiger CLI (default `tiger`) |
| `DCSIM_LEDGER_WRITES` | `0` stops adopting/saving from logging forecasts to the append-only Tiger ledger (reads keep working) | set by `make server-test`; `/api/health` reports `ledger_writes` |
| `DCSIM_API_URL` (web) | which API the Vite dev server proxies `/api` to | default `http://127.0.0.1:8000`; `make web-test-ui` points it at :8001 |
| `DCSIM_CACHE` | move the results cache | default `<repo>/cache` |
| `DCSIM_ADMIN_TOKEN` | protect deleting scenarios and the admin jobs (`/api/warm`, `/api/backtest`) | **set before hosting publicly** (ISSUES A1). Unset = open (local). Send `Authorization: Bearer <token>` or `X-Admin-Token` |
| `DCSIM_AI_RATE` | AI requests per minute per client IP (ask, narrate, explain, dish prior, similar dishes) | default 20; `0` = no limit; over it → 429 with `Retry-After` |
| `DCSIM_JOB_TTL_S`, `DCSIM_TRACE_CACHE` | API memory: how long finished jobs are kept, how many decompressed traces narration keeps | defaults 900 s (max 200 jobs) and 2 (ISSUES A2) |
| `DCSIM_LLM_TIMEOUT` | how long an LLM request may stall before falling back to the template | seconds, default 60 (ISSUES L5) |

**Without any keys everything still runs:** the LLM features use templates or keyword matching, and the static bundle already contains AI narrations.

---

## 7. Testing

| Suite | Command | What it covers |
| --- | --- | --- |
| Engine | `make test` | determinism, conservation (cooked = served + expired + leftover), common random numbers, diet rules, all 8 menus load, every API route (mock LLM), AI checks with fake providers (incl. function calling for the plain-English box), Ask Sam (tool loop, 5-try cap, goal check, setting-function rules, what-ifs, Tiger SQL guard, history), the waste outlook, the ledger-writes switch, calibration, bundle, Similar nights search and route, the optimizer and plausible worlds (tiny runs and their API jobs), plan searches kept in Tiger (rows, schema, writes-off switch), the dish search and dish-popularity check (recovers a known spread from a fixture); **golden baseline** (`tests/golden/nov13.json`: any change that moves Nov 13's numbers fails until you run `DCSIM_UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py` on purpose) and **calibration targets** (the team's observations, `data/worcester/calibration_targets.json`) |
| Web unit | `make web-test` | playback against the real bundle (no dot outside the building, pan levels, "missed first choice" matches the engine), lever mapping, plain-English settings → API request, grouped alerts, back-test totals, Hall playback and walking paths, Similar nights page states (defaults, menu, empty, 422, API down + retry), Ask Sam's stream and history (incl. what-ifs), the waste outlook (plan per coming night, adopted vs recommended), Optimize and Plausible worlds (stream → progress → frontier and tornado, Load into builder, past searches from Tiger, saved chip), the new-dish Tiger evidence line |
| Browser, pages | `cd web && node scripts/smoke.mjs http://localhost:5173 smoke-out` | Simulation, Kitchen plan, Insights and Lab at desktop / tablet / phone width, main clicks, no console errors, no horizontal scroll on phones; screenshots in `smoke-out/` |
| Browser, API up | `make web-test-api` (on the test stack above) | ask → run, Why?, new dish + AI estimate, adopt → Dashboard, saved list, API replay + narration, Ask Sam (question from Tiger, plan in ≤ 5 tries, history, Open in the Lab) |

---

## 8. What's real and what's made up

Be precise about this in the demo; judges will ask.

| Real (sourced) | Team-confirmed (a Worcester regular) | Guessed (in `ASSUMPTIONS.md`) | **Synthetic** |
| --- | --- | --- | --- |
| The menus (umassdining.com snapshot, Nov 13–20, 2026), dinner/late-night hours, diet shares from the 2022 UMass Dining survey, floor plan, campus waste facts | dinner 4:30 pm start, busiest 7–8 pm, longest lines 30–40, Street Food is pre-cooked, Sushi made to order, ~3,000 dinner students, ~1,000 late night | cook and hold times, persona mix, patience, seat layout, servers per station, late-night days | **All "recorded" waste history** (generated by this engine + noise, shaped like MetaFoodX / LeanPath exports). There is no real UMass waste data in this repo |

Consequences:
- The **back-test is circular** (the engine grades data it generated). Most of its reduction comes from "cooking to the forecast", an assumption of the generator. Say so (`docs/ISSUES.md` B1).
- Every simulated number is shown with a range; every synthetic number carries a "Synthetic" badge. Keep it that way.
- What we'd ask UMass Dining for: data requests R01–R08 in `data/sources/audit/data_requests.json`.

---

## 9. Current status and known issues

*As of 2026-11-14, engine 0.6.2. The demo bundle was rebuilt 2026-11-14 04:05 UTC on engine **0.6.2** (264 results, 14 replays, 168 Gemini narrations, 22 MB), so the web app shows current numbers; rebuild it again only if the engine or data changes. A full test run after the rebuild passed (see `docs/STATUS.md`). One-page tracker: [`docs/STATUS.md`](docs/STATUS.md).*

**Works:** engine with dinner + late night on 8 real dates; 5 presets and the lever grid precomputed; FastAPI with jobs, caching, scenarios, traces, narration and the AI routes; the redesigned web app (Simulation, Kitchen plan, Insights with Waste history and Similar nights) offline and with the API; synthetic history, back-test and Tiger calibration.

**New on 2026-11-14 (later; the engine version didn't change):** **Ask Sam** (`/sam`): a Gemini planning agent with Tiger lookups through the Tiger MCP server, at most 5 tries, the setting functions for staffing and menu changes, what-ifs and history questions in the same box, and a History of final analyses; the plain-English box answers through **Gemini function calling**; Insights opens on **the next 8 nights** with each night's adopted plan (`waste/outlook.json`, `GET /api/waste/outlook`, `waste_lb` on every result); the "Last 8 weeks" button that could never work is gone; a ledger-safe test stack (`make server-test`, `make web-test-ui`, `DCSIM_LEDGER_WRITES`); `make serve` is now `make server`; image input is out of scope. Details: `docs/SPONSORS.md` §1–2, `docs/STATUS.md`.

**New since 0.6.2 (merged 2026-11-13; the engine version didn't change):** the frontend redesign (PR #1) and Hall fixes (PR #2); **Similar nights** (`/similar-nights` page + `GET /api/similar-nights`, Tiger pgvector with a local fallback; handoff in PR #3); `dcsim tsdb similar` from the command line (DiskANN, held-out check); `dcsim bundle` narration times out after `DCSIM_LLM_TIMEOUT` seconds and prints one progress line per student (ISSUES L5). Branch by branch: §14.

**New on 2026-11-14 (Tiger Data; the engine version didn't change):** two real-time continuous aggregates with refresh policies (`waste_daily_by_dish`, `pan_hourly`); `dcsim tsdb bench` times the same questions on raw hypertables and aggregates (557 ms → 82 ms on pans by hour; the columnstore wins the per-dish totals, and the output says so); `dcsim tsdb query stack` joins aggregates, nights, plan, forecasts and ledger in one statement; all six hypertables in the columnstore (76 MB → 9.6 MB); the Tiger loader fills every engine 0.4+ column; a live dashboard served by the API at `/tiger` (not linked from the web app). Details: `docs/TIGER.md` §8.

**New in 0.6.2 (API and UI fixes):** optional admin token and per-IP AI rate limit (A1); job expiry and a smaller trace cache (A2); `POST /api/warm` and `/api/backtest` admin jobs (A3); verdict "no real difference" below a per-metric minimum (C1); a "Needed at peak" servers column on the Dashboard (U5, needs a 0.5.0+ bundle); per-dish $ / week now add up to the average simulated week (U16); the offline plain-English parser no longer mixes "an extra server at Grill" with the extra-server preset (R6).

**New in 0.6.1 (review fixes):** late run-outs fixed against the team's targets (last dinner batch 15 min before close, ISSUES E3); late-night kitchen retuned for the cook limit (50% top-up batches, 5 late cooks); a rare expiry bookkeeping bug fixed; "Why?" and the plain-English box now handle cooks, server shifts and forecast reordering. All calibration targets pass. Details: `docs/ISSUES.md` §6b.

**New in 0.6.0:** pans are SimPy Containers (same results); per-minute series via `GET /api/results/{hash}?include=series`; Noodle Bowl popularity per dish (Hot Pot, Pho, Tom Yum, Taiwan Pork draw a crowd; ramen doesn't); allergies at survey shares and a 2% pescetarian diet; an entrance swipe queue, friends who arrive and sit together, and hotel-pan size limits with a hot-hold cabinet (placeholders); a **Forecast reorder** preset (cooks to the expected rush; Nov 13 waste −15% but more stockouts, Nov 17 better on both).

**New in 0.5.0 (simulation core):** cooks and equipment as limited resources (`kitchen_defaults.json → resources`, placeholders: 12 dinner / 5 late-night cooks since 0.6.1, ovens, steamers, kettles, flat-tops, woks, fryers, tandoors); students wait up to 2 min at an empty pan when a batch is cooking; staffing by hour (`shifts`) with a "servers needed" table; seats counted per night (limits off); golden baseline and calibration-target tests. New result fields: `batch_wait_min`, `cook_busy_share`, `empty_wait_min`, `peak_seated`, `staffing`.

**New in 0.4.2:** late-night kitchen taper (late-night waste about 40% → about 20% of cooked portions), crowd size by weekday and event (placeholder multipliers in `data/worcester/crowd_by_date.json`), and a staffed carving line for the Nov 18 turkey.

**Headline numbers right now** (they change whenever the engine changes, so check the app):
- Fri Nov 13 baseline (engine 0.6.2 bundle, median of 30 nights): about 3,720 students (2,710 dinner + 1,010 late night; Friday crowd ×0.9), wait p90 about 15 min, about 25% miss their first choice, about 38 stockout-minutes per night (dinner 14, late night 20), about 3,350 portions wasted (28% of cooked). The web app, the CLI and the API show the same numbers.
- Back-test of *Smaller batches of favorites* over 39 past nights (engine 0.6.1, `data/synthetic/worcester/backtest.json`): **waste about 8.8% lower (5.6–10.4%)**, wins 38 of 39 nights, about 542 lb and $1,821 per week. Stockouts: about 53 min per night under the plan vs 48 for the baseline plan and **41 recorded**, so the plan saves waste at some cost in run-outs.

**Biggest open items:**
| Item | Where |
| --- | --- |
| Figma frames and pitch use placeholder numbers (e.g. "−18% waste") that don't match the data | `docs/UI_ISSUES.md` U1 |
| No orbital 3D: the Hall plays on an illustrated isometric view and the top-down plan | U2 / ISSUES N1 |
| Optimize finds no plan with less waste and no more stockouts on Nov 13: the cooks are the limit, so it shows the trade-off instead | IMPROVEMENTS 2.1 |
| Hosted on Vultr: admin token and AI rate limit are set there, ledger writes off until the demo; the timed 1,000-world sweep is still to do | `deploy/README.md`, SPONSORS §4 |
| **`.dev.vars` app tokens were on GitHub** (was `e926ea1`): removed from branch history 2026-11-14, but PRs #4–#6 still expose the old commit until GitHub purges it: rotate the tokens | ISSUES T6 |
| 6 test forecasts for Nov 13 are in the append-only Tiger ledger; read the Nov 13 scorecard without them | ISSUES T8 |
| The Hall's pan level counts the hot-hold cabinet too, so for the 30 s of a pan swap it shows food in a pan a student just found empty | UI_ISSUES U18 |
| Many inputs are labeled guesses (cooks and equipment, pan sizes, swipe readers, groups, late-night crowd, seat counts); seat limits stay off until seats are counted | `data/worcester/ASSUMPTIONS.md` |

Full lists: **`docs/ISSUES.md`** (engine, data, API, LLM) and **`docs/UI_ISSUES.md`** (web app). Ideas ranked by value: `docs/IMPROVEMENTS.md`.

---

## 10. Docs index

| Doc | Read it when |
| --- | --- |
| [`docs/STATUS.md`](docs/STATUS.md) | You want one page of what's done and open across every tracker (start here) |
| [`docs/SPEC.md`](docs/SPEC.md) | You want the product: pages, scope (P0/P1/P2), architecture, **3-minute demo script (§12)** |
| [`docs/ENGINE.md`](docs/ENGINE.md) | You touch the simulation, caching or API (§8 lists every route and which page uses it) |
| [`engine/README.md`](engine/README.md) | You use the `dcsim` CLI, levers, or the data flow |
| [`web/README.md`](web/README.md) | You work on the frontend |
| [`data/README.md`](data/README.md) | You need to find an input or a source |
| [`data/worcester/ASSUMPTIONS.md`](data/worcester/ASSUMPTIONS.md) | You need to know where a number comes from |
| [`docs/LLM.md`](docs/LLM.md), [`docs/GEMINI.md`](docs/GEMINI.md) | You touch anything AI: rules, prompts, fallbacks, rate limits |
| [`docs/TIGER.md`](docs/TIGER.md) | Forecast ledger, calibration, Tiger Data, `dcsim tsdb similar`, continuous aggregates, the `/tiger` dashboard and its demo script (§8), dish search and popularity check (§9), plan searches (§10), the 500-night question (§11) |
| [`docs/SIMILAR_NIGHTS.md`](docs/SIMILAR_NIGHTS.md) | The Similar nights page and API: contract, vector design, UI states, verification |
| [`docs/FRONTEND_REDESIGN_PLAN.md`](docs/FRONTEND_REDESIGN_PLAN.md) | The redesign's navigation, interaction stages and visual system (follow it for new UI) |
| [`docs/ISSUES.md`](docs/ISSUES.md), [`docs/UI_ISSUES.md`](docs/UI_ISSUES.md) | Before you demo, or to pick something to fix |
| [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md) | Planning what to build next |
| [`docs/SPONSORS.md`](docs/SPONSORS.md) | Sponsor-prize features: Gemini function calling (§1), **Ask Sam** as built (§2), Tiger Data, Vultr (deployed, §4), domain |
| [`deploy/README.md`](deploy/README.md) | You deploy or update the Vultr server, change its settings, or something breaks there (troubleshooting from the first deploy) |
| [`survey/student_survey.md`](survey/student_survey.md) | Running the student survey |

---

## 11. Glossary

| Term | Meaning |
| --- | --- |
| **Night / seed** | One simulated service (dinner + late night). Seed *n* fixes all randomness, so the same seed gives the same night. |
| **Baseline** | The date's menu cooked with the default batch plan, nothing changed. |
| **Preset** | A ready-made what-if: `smaller_batches`, `trim_slow`, `extra_server`, `theme_night`, `combined`, `forecast_reorder`. |
| **Ask Sam / Sam** | The planning agent page (`/sam`): goals, what-ifs and history questions, at most 5 simulations per plan. |
| **Goal check** | Sam's pass/fail test in code: waste cut, protected dishes not running out more, missed meals and the long wait not worse. |
| **Setting function** | One Gemini function per lever (`set_servers`, `scale_batch`, …), used by the plain-English box and by Sam. |
| **Waste outlook** | The coming nights' simulated waste with each night's adopted plan, on Insights › Waste history. |
| **Lever** | One setting in the Lab: crowd multiplier, arrival shift, servers (and shifts), cooks, batch plan per dish, popularity, remove/add dishes. |
| **Config hash** | ID of a full scenario config; the cache key for results and traces. |
| **Result** | Summary over N nights: `headline` ranges (p10/p50/p90), `cook_sheet`, `flags`, `stations`, `vs_baseline`. |
| **Median night** | The night whose results are closest to the median; it's the one replayed in the Hall. |
| **Trace** | Per-student events (enter, queue_join, balk, served, empty_pan, sit, …) and pan events for one night; gzipped JSON. |
| **Cook sheet** | Per dish: portions to prepare, batch size, starting batches, reorder point, last batch time, run-out risk. |
| **Verdict** | *better / worse / inconclusive*, from the paired win rate over the 30 nights. |
| **Back-test** | Re-simulating each past (synthetic) night with the recommended plan to estimate "what if we had used it". |
| **Bundle** | `web/public/cache`: everything the app needs to demo offline. |
| **Similar night** | A recorded night before the target date whose conditions (weekday, weather, event, attendance, menu) are closest by cosine similarity. Shown with what actually happened on it; not a forecast. |
| **Persona** | A type of student (regular, athlete, rushed, social, vegetarian, vegan, halal) with its own share, tastes and patience (`data/worcester/personas.json`). |

---

## 12. Rules of the road

- **Never present synthetic numbers as real.** Keep the badges, ranges and "simulated" labels.
- **The LLM never sets simulated numbers directly.** It only proposes inputs you can see (settings, affinities) or writes text about results, always with a check and a fallback.
- **Bump `ENGINE_VERSION`** when simulation logic changes, then rebuild the bundle.
- **Keep the demo offline-safe.** Anything new in the UI must work (or explain itself) without the API.
- **Secrets stay in `engine/.env`.** No keys in code, commits or chat.
- **Don't test against the real ledger.** The Tiger forecast ledger is append-only: Adopt / Save on an API with Tiger set up logs forecasts that can't be deleted. Browser-test with `make server-test` + `make web-test-ui` (ledger writes off, a copy of the cache); `smoke-api.mjs` refuses a ledger-writing API (ISSUES T8).
- **Photos:** no faces; front-of-house only.
- **Docs:** when you change behavior or numbers, update the relevant doc and the issue lists.

---

## 13. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Web app shows "Couldn't load … run `make demo-bundle`" | The bundle is missing or partial: `make demo-bundle` (or pull it from git). |
| Lab says "Start the API (`make server`)" | That combination isn't precomputed. Run `make server` in another terminal, then reload the page. |
| `make demo-bundle` seems stuck after "bundle --grid" | It is writing AI narrations (rate-limited to 15 calls/min, so this step can take 15–30 min). Wait, or run `LLM_PROVIDER=mock make demo-bundle` for template text in a few minutes. Don't press Ctrl+C. |
| Narration says "(template)" | No LLM key, or the free-tier limit was hit. Add `GEMINI_API_KEY` to `engine/.env`, or wait a minute. |
| Numbers in the app don't match the engine CLI | The bundle is older than the engine. `make cache-clean && make demo-bundle`. |
| `uv` complains about Python | Needs Python ≥ 3.13: `uv python install 3.13`. |
| Port 8000 or 5173 is busy | Stop the old server, or run the API on another port (`cd engine && uv run dcsim serve --port 8002`) and start the app with `DCSIM_API_URL=http://127.0.0.1:8002 npx vite` in `web/`. |
| The Optimize card shows no past searches, or the new-dish form no "In Tiger" line | Tiger isn't set up or reachable (the routes answer 503 and the UI stays quiet), nothing was saved yet (test servers never save), or the API predates this (`/api/searches` → 404: restart `make server`). |
| Optimize or Plausible worlds says "Not Found" | The API was started before they were pulled (`/api/optimize` → 404). Restart `make server`. |
| Ask Sam says "Not Found" | The API was started before Ask Sam was pulled (`/api/agent` → 404). Restart `make server`. |
| Ask Sam says "Sam is already planning for someone" | Two runs are already going (`DCSIM_AGENT_MAX_RUNNING`). Wait for one to finish. |
| Sam's history lookups fail ("Tiger isn't reachable") | Set `TIGER_SERVICE_ID` in `engine/.env` and log in once with `tiger auth login`; plans still work without Tiger. |
| Insights says "The coming nights aren't available yet" | No `waste/outlook.json` in the bundle and no API: `cd engine && uv run dcsim bundle --waste-only`, or start `make server`. |
| Insights › Similar nights says "Can't reach the search service" | The API isn't running, or it was started before you pulled this feature (`/api/similar-nights` → 404). Restart `make server`. |
| `http://127.0.0.1:8000/tiger` is 404, or says "Tiger not configured" | 404: the API was started before 2026-11-14, restart `make server`. "Not configured": set `DCSIM_TSDB_URL` in `engine/.env` (`docs/TIGER.md`). |
| The live site (Vultr) misbehaves: no page, certificate error, Sam's Tiger lookups fail | See the troubleshooting table in [`deploy/README.md`](deploy/README.md); logs: `journalctl -u dcsim -f` on the server. |
| `git pull` says "You have divergent branches" | You committed locally while someone pushed or merged a PR. Run `git pull --rebase` (or set `git config pull.rebase true` once). See §14. |
| Smoke test can't start a browser | It uses your installed Google Chrome (`playwright-core`, `channel: "chrome"`). Install Chrome. |

---

## 14. Branches and merges

Everything lives on `main`. As of 2026-11-14 every branch below is merged, so they can be deleted on GitHub.

| Branch | By | What it brought | Merged |
| --- | --- | --- | --- |
| `frontend/redesign` | Eric | The redesigned web app: top navigation (Simulation / Kitchen plan / Insights), guided **Watch → Try a change → Compare**, illustrated isometric hall (`HallScene.tsx`), walking paths around counters and tables (`navigation.ts`, `hallGeometry.ts`), replay clock and alerts, detail dialogs, tablet and phone layouts. Plan: `docs/FRONTEND_REDESIGN_PLAN.md` | PR #1 → `0ba140e` |
| `fix-bug` | Eric | Hall fixes after the redesign: Compare keeps its results while a replay is retried, a "replay unavailable" panel with retry for dates without a bundled animation, more Hall tests | PR #2 → `2662dc8` |
| `codex/similar-nights-docs` | Ky | `docs/SIMILAR_NIGHTS.md`: the Similar nights API contract and vector design, plus links. Docs only; the code was never pushed, so it was rebuilt from this contract (`137c784`) | PR #3 → `3c62eff` |
| `tigerdata` | Victor | Points at the sponsor-guide commit (`bb86eab`); nothing beyond `main` | already in `main` |

Pushed straight to `main` on 2026-11-13 (not through a branch):

| Commit | What |
| --- | --- |
| `c936f81`, `4609597` | Engine 0.6.2: admin token, AI rate limit, job expiry, admin jobs, "no real difference" verdict, Dashboard servers-needed column, per-dish $ fix |
| `137c784` | Similar nights: `similar_nights.py`, `GET /api/similar-nights`, the Insights › Similar nights page, 30 tests |
| `dcf6772` | Narration timeout and progress lines in `dcsim bundle` (ISSUES L5) |
| `9e01ec7` | `dcsim tsdb similar` (`nightsearch.py`, DiskANN index, held-out check) |

Pushed straight to `main` on 2026-11-14:

| Commit | What |
| --- | --- |
| `36493f1` | Demo bundle rebuilt on engine 0.6.2; bundle-dependent tests and smoke script fixed |
| `1ab7e16`, `4a5d6fb`, `094dbc4` | Tiger console script (`engine/sql/tiger_website.sql`), new columns and category tables, loader fills them |
| `78d9239` | Tiger aggregates, benchmark, `/api/tiger/*`, the dashboard page; also added `my-app/` (ISSUES T7) |
| `9160ee5` | Dashboard linked from Insights and smoke-tested (undone the same day: the page moved to the API at `/tiger`) |
| `01e5e6d` | `.gitignore` (`node_modules`); it also added `.dev.vars` with tokens, removed from history 2026-11-14 (was `e926ea1`, ISSUES T6) |

**Working with branches**
- Branch from an up-to-date `main` (`git pull --rebase` first), open a PR, merge on GitHub.
- Set `git config pull.rebase true` once. Then a pull replays your local commits on top of the remote ones instead of stopping with "divergent branches".
- Before merging UI work: `make web-test`, `make web-build` and the smoke test. Before merging engine work: `make test`, and bump `ENGINE_VERSION` if the numbers move.
- Don't rebuild or commit the demo bundle on a branch. It is rebuilt once, on the final `main`, before the demo.
- New UI follows `docs/FRONTEND_REDESIGN_PLAN.md` (navigation and "Visual system").
