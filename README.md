# DC What-If · Worcester

**A "what-if" simulator for the Worcester Dining Commons at UMass Amherst** (HackUMass 2026).
It simulates hundreds of dinner nights on the **real menu**, student by student, so a kitchen manager can test a decision (smaller batches, an extra server, a new dish, a busier night) **before** trying it on real students. It then says how many pans to cook, and when.

> **The pitch in one line:** other projects simulate one lunch with AI students. We simulate 30+ nights per decision and hand the manager a cook sheet, with ranges and an honest "better / worse / can't tell" verdict.

This README is the catch-up guide. Each section links to the detailed doc.

---

## Contents
1. [What the app does (the 4 pages)](#1-what-the-app-does)
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

---

## 1. What the app does

A web app with four pages, designed in Figma (file *DC What-If — Worcester*) and built in `web/`.

| Page | What a manager sees | Main data |
| --- | --- | --- |
| **Hall** (`/hall`) | The simulated night played back on the Worcester 2nd-floor plan: every dot is a student entering, queuing, getting a plate or bowl, sitting, leaving. Play / pause, 1×–60× speed, a timeline to scrub, color by diet / wait / missed first choice, pan levels per station, "Right now" counters. Click a station for its dishes and line over time; click a student for their timeline and an AI-written story of their night. | Median-night **trace** (per-student events) + result |
| **Dashboard** (`/dashboard`) | Tonight's plan for the chosen date: expected students, peak time, portions to prepare, forecast waste, dishes at risk, peak wait, all with ranges. Arrivals chart (click a bar to jump the Hall there), alerts, menu by station, the **production plan** (Print, CSV), staffing, and what will be left at close for donation pickup. Shows the plan adopted for that date, or the baseline. | Result of the adopted scenario |
| **Waste Record** (`/waste`) | Recorded waste history (**synthetic**, clearly labeled) vs. what the recommended plan would have done on each past night: reduction with range, $ per week, stockout trade-off, where waste comes from, top waste dishes with "with plan" numbers, station filters, adopted-plan tracker, CSV export. | Synthetic history + back-test |
| **What-If Lab** (`/lab`) | Build a scenario: presets, batch-plan sliders per dish, crowd size, servers per station, menu edits (remove a dish, change popularity, **add a new dish with an AI estimate**), or **ask in plain English**. Run 30 paired nights and see results vs. baseline with verdicts, the cook sheet with changed rows, side effects, when pans run empty, where demand moves, and "Why?". Then **Adopt** it for the date or **Watch in 3D** (the Hall replay). | Bundle, or the API for anything not precomputed |

There is also a standalone **calibration page** (`web/public/calibration.html`, open `/calibration.html` on the dev server) that checks the forecasts against held-out nights (see `docs/TIGER.md`).

**Two ways to run it:**
- **Offline (demo-safe):** the pages read a precomputed **static bundle** in `web/public/cache` (results, replays, narrations, back-test). No backend, no network.
- **With the API** (`make serve`): everything above, plus any combination of settings, replays of any scenario, narration of any student, the plain-English box, menu edits, new dishes and "Why?". Features that need the API turn themselves off with a message when it isn't running.

---

## 2. Quick start

### Prerequisites
| Tool | Version used | Why |
| --- | --- | --- |
| Python | ≥ 3.13 | engine (managed by `uv`) |
| [uv](https://docs.astral.sh/uv/) | 0.13 | Python env + deps (`engine/uv.lock`) |
| Node.js | 24 (18+ should work) | web app |
| Google Chrome | any recent | only for the browser smoke tests |

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
make serve                               # FastAPI on http://127.0.0.1:8000, docs at /docs

# 3. web (terminal 2)
make web                                 # http://localhost:5173, proxies /api to :8000
```

Try this in the Lab with the API running: type *"one more server at Grill and 10% busier"* into **Ask in plain English**, press **Apply and run**, then **Watch in 3D**.

### Command-line only (no web)
```bash
cd engine
uv run dcsim run --date 2026-11-13                       # 30 nights: medians and 10–90% ranges
uv run dcsim compare --preset smaller_batches --date 2026-11-13
uv run dcsim menu --date 2026-11-13                      # what's on, by station
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
- **The LLM is never inside the simulation.** It writes student stories, turns plain English into settings, estimates how much each type of student would like a new dish (as numbers you can see), and explains results. Every LLM output is checked and has a non-AI fallback. See `docs/LLM.md`.

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
│   │   ├── reports.py       cook sheet, alerts, station table
│   │   ├── service.py, store.py, api.py   caching, SQLite, FastAPI
│   │   ├── synthetic.py, backtest.py      synthetic waste history + back-test
│   │   ├── calibration.py, tsdb.py        held-out calibration + Tiger Data ledger
│   │   ├── bundle.py        writes the static demo bundle
│   │   ├── cli.py           the `dcsim` command
│   │   └── ai/              LLM client (Gemini/Groq/mock), narration, ask, dish prior, explain, similar dishes
│   ├── tests/               pytest (engine, API, AI with fakes, calibration, bundle)
│   └── .env                 your keys (not committed)
├── web/                   React app (see web/README.md)
│   ├── src/pages/           Hall.tsx, Dashboard.tsx, Waste.tsx, Lab.tsx
│   ├── src/lib/             data.ts (bundle + API client), playback.ts (trace → dots), store.ts, format.ts, types.ts
│   ├── src/components/      Shell (sidebar, top bar), Charts
│   ├── public/cache/        STATIC DEMO BUNDLE (committed, ~17 MB)
│   ├── public/plan/         floor-plan SVGs (top-down + static 3D render)
│   ├── public/calibration.html   standalone forecast-calibration page
│   └── scripts/             smoke.mjs (API down), smoke-api.mjs (API up)
├── data/                  inputs (see data/README.md)
│   ├── raw/                 original uploads, never edited (menu snapshot, research, floor plan)
│   ├── sources/             research split by topic, with a citation registry
│   ├── worcester/           ENGINE INPUTS: dc.json, stations.json, personas.json, menus/, kitchen/, layout/, ASSUMPTIONS.md
│   └── synthetic/worcester/ SYNTHETIC "recorded" history + backtest.json
├── docs/                  design docs and issue lists (see §10)
├── survey/                student survey draft (feeds persona calibration)
└── cache/                 local results cache (SQLite + traces); not committed
```

---

## 5. Everyday tasks

All targets run from the repo root; each one has a short comment in the `Makefile`.

| I want to… | Run |
| --- | --- |
| Start the API | `make serve` (add `--warm` via `cd engine && uv run dcsim serve --warm` to precompute every date) |
| Start the web app | `make web` |
| Run engine tests | `make test` |
| Run web unit tests | `make web-test` |
| Build the web app | `make web-build` (output in `web/dist`) |
| Browser-test the API features | `make serve` + `make web`, then `make web-test-api` |
| Re-read the menu snapshot into per-date menus and plans | `make prepare` |
| Regenerate the synthetic waste history | `make waste` |
| Re-run the back-test | `make backtest` |
| **Rebuild the demo bundle** (after any engine or data change) | `make demo-bundle` (≈ 4 min + narration time; `make demo-bundle-quick` for presets only) |
| Drop cached results from old engine versions | `make cache-clean` |
| Narrate highlight students | `make narrate` |
| Tiger Data calibration | `make calibrate` (needs Tiger credentials, see `docs/TIGER.md`) |

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
| `GEMINI_API_KEY` | narration, plain-English box, AI estimate, Why?, similar dishes | Default provider when set. Free tier: 15 requests/min; the app falls back to templates when busy |
| `GROQ_API_KEY` | alternative LLM provider | use with `LLM_PROVIDER=groq` |
| `LLM_PROVIDER` | choose `gemini`, `groq` or `mock` | `mock` = no network, deterministic (tests always use it) |
| `DCSIM_LLM_MODEL` | override the model | see `docs/GEMINI.md` |
| `TIGER_SERVICE_ID` or `DCSIM_TSDB_URL` | Tiger Data forecast ledger + calibration | optional; adopting still works without it |
| `DCSIM_CACHE` | move the results cache | default `<repo>/cache` |
| `DCSIM_LLM_TIMEOUT` | how long an LLM request may stall before falling back to the template | seconds, default 60 (ISSUES L5; in the working tree, not committed yet) |

**Without any keys everything still runs:** the LLM features use templates or keyword matching, and the static bundle already contains AI narrations.

---

## 7. Testing

| Suite | Command | What it covers |
| --- | --- | --- |
| Engine | `make test` | determinism, conservation (cooked = served + expired + leftover), common random numbers, diet rules, all 8 menus load, every API route (mock LLM), AI checks with fake providers, calibration, bundle; **golden baseline** (`tests/golden/nov13.json`: any change that moves Nov 13's numbers fails until you run `DCSIM_UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py` on purpose) and **calibration targets** (the team's observations, `data/worcester/calibration_targets.json`) |
| Web unit | `make web-test` | playback against the real bundle (no dot outside the building, pan levels, "missed first choice" matches the engine), lever mapping, plain-English settings → API request, grouped alerts, back-test totals |
| Browser, API down | `cd web && node scripts/smoke.mjs http://localhost:5173 smoke-out` | all 4 pages at desktop / tablet / phone width, main clicks, no console errors, no horizontal scroll on phones; screenshots in `smoke-out/` |
| Browser, API up | `make web-test-api` | ask → run, Why?, new dish + AI estimate, adopt → Dashboard, saved list, API replay + narration |

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

*As of 2026-11-13, engine 0.5.0. The committed demo bundle was generated 2026-11-14 00:54 UTC with engine **0.4.1**, so the web app shows 0.4.1 numbers until the bundle is rebuilt (planned once, before the demo). One-page tracker: [`docs/STATUS.md`](docs/STATUS.md).*

**Works:** engine with dinner + late night on 8 real dates; 5 presets and the lever grid precomputed; FastAPI with jobs, caching, scenarios, traces, narration and the AI routes; all four web pages offline and with the API; synthetic history, back-test and Tiger calibration.

**New in 0.5.0 (simulation core):** cooks and equipment as limited resources (`kitchen_defaults.json → resources`, placeholders: 12 dinner / 4 late-night cooks, ovens, steamers, kettles, flat-tops, woks, fryers, tandoors); students wait up to 2 min at an empty pan when a batch is cooking; staffing by hour (`shifts`) with a "servers needed" table; seats counted per night (limits off); golden baseline and calibration-target tests. New result fields: `batch_wait_min`, `cook_busy_share`, `empty_wait_min`, `peak_seated`, `staffing`.

**New in 0.4.2:** late-night kitchen taper (late-night waste about 40% → about 20% of cooked portions), crowd size by weekday and event (placeholder multipliers in `data/worcester/crowd_by_date.json`), and a staffed carving line for the Nov 18 turkey. **In progress (not committed):** similar-night search on Tiger (`dcsim tsdb similar`, see `docs/TIGER.md` §7); timeout and progress lines for the bundle's narration step (ISSUES L5).

**Headline numbers right now** (they change whenever the engine changes, so check the app):
- Fri Nov 13 baseline (engine 0.5.0, median of 10 nights): about 3,720 students (2,710 dinner + 1,010 late night; Friday crowd ×0.9), wait p90 about 15 min, about 25% miss their first choice, about 74 stockout-minutes per night. The web app still shows 0.4.1 numbers until the bundle is rebuilt.
- Back-test of *Smaller batches of favorites* over 39 past nights (engine 0.5.0, `data/synthetic/worcester/backtest.json`): **waste about 8.5% lower (4.5–10.3%)**, wins 39 of 39 nights, about 497 lb and $1,592 per week. Stockouts: about 83 min per night under the plan vs 76 for the baseline plan and **69 recorded**, so the plan saves waste at some cost in run-outs.

**Biggest open items:**
| Item | Where |
| --- | --- |
| Figma frames and pitch use placeholder numbers (e.g. "−18% waste") that don't match the data | `docs/UI_ISSUES.md` U1 |
| No animated 3D yet: the Hall is a top-down 2D replay; "3D overview" is a still image | U2 / ISSUES N1 |
| Dashboard doesn't show "servers needed" yet (the engine computes it since 0.5.0); no breakfast/lunch | U4, U5 |
| No optimizer or uncertainty sweep (the Lab's Optimize button is disabled) | U7, ISSUES N6–N7 |
| Stockouts higher than the team observes | ISSUES E3 |
| API has no auth; don't host it publicly as is | ISSUES A1 |

Full lists: **`docs/ISSUES.md`** (engine, data, API, LLM) and **`docs/UI_ISSUES.md`** (web app). Ideas ranked by value: `docs/IMPROVEMENTS.md`.

---

## 10. Docs index

| Doc | Read it when |
| --- | --- |
| [`docs/SPEC.md`](docs/SPEC.md) | You want the product: pages, scope (P0/P1/P2), architecture, **3-minute demo script (§12)** |
| [`docs/ENGINE.md`](docs/ENGINE.md) | You touch the simulation, caching or API (§8 lists every route and which page uses it) |
| [`engine/README.md`](engine/README.md) | You use the `dcsim` CLI, levers, or the data flow |
| [`web/README.md`](web/README.md) | You work on the frontend |
| [`data/README.md`](data/README.md) | You need to find an input or a source |
| [`data/worcester/ASSUMPTIONS.md`](data/worcester/ASSUMPTIONS.md) | You need to know where a number comes from |
| [`docs/LLM.md`](docs/LLM.md), [`docs/GEMINI.md`](docs/GEMINI.md) | You touch anything AI: rules, prompts, fallbacks, rate limits |
| [`docs/TIGER.md`](docs/TIGER.md) | Forecast ledger, calibration, Tiger Data |
| [`docs/ISSUES.md`](docs/ISSUES.md), [`docs/UI_ISSUES.md`](docs/UI_ISSUES.md) | Before you demo, or to pick something to fix |
| [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md) | Planning what to build next |
| [`docs/SPONSORS.md`](docs/SPONSORS.md) | Picking up a sponsor-prize feature (Gemini, agent, Tiger Data, Vultr, domain): status and build steps |
| [`survey/student_survey.md`](survey/student_survey.md) | Running the student survey |

---

## 11. Glossary

| Term | Meaning |
| --- | --- |
| **Night / seed** | One simulated service (dinner + late night). Seed *n* fixes all randomness, so the same seed gives the same night. |
| **Baseline** | The date's menu cooked with the default batch plan, nothing changed. |
| **Preset** | A ready-made what-if: `smaller_batches`, `trim_slow`, `extra_server`, `theme_night`, `combined`. |
| **Lever** | One setting in the Lab: crowd multiplier, arrival shift, servers, batch plan per dish, popularity, remove/add dishes. |
| **Config hash** | ID of a full scenario config; the cache key for results and traces. |
| **Result** | Summary over N nights: `headline` ranges (p10/p50/p90), `cook_sheet`, `flags`, `stations`, `vs_baseline`. |
| **Median night** | The night whose results are closest to the median; it's the one replayed in the Hall. |
| **Trace** | Per-student events (enter, queue_join, balk, served, empty_pan, sit, …) and pan events for one night; gzipped JSON. |
| **Cook sheet** | Per dish: portions to prepare, batch size, starting batches, reorder point, last batch time, run-out risk. |
| **Verdict** | *better / worse / inconclusive*, from the paired win rate over the 30 nights. |
| **Back-test** | Re-simulating each past (synthetic) night with the recommended plan to estimate "what if we had used it". |
| **Bundle** | `web/public/cache`: everything the app needs to demo offline. |
| **Persona** | A type of student (regular, athlete, rushed, social, vegetarian, vegan, halal) with its own share, tastes and patience (`data/worcester/personas.json`). |

---

## 12. Rules of the road

- **Never present synthetic numbers as real.** Keep the badges, ranges and "simulated" labels.
- **The LLM never sets simulated numbers directly.** It only proposes inputs you can see (settings, affinities) or writes text about results, always with a check and a fallback.
- **Bump `ENGINE_VERSION`** when simulation logic changes, then rebuild the bundle.
- **Keep the demo offline-safe.** Anything new in the UI must work (or explain itself) without the API.
- **Secrets stay in `engine/.env`.** No keys in code, commits or chat.
- **Photos:** no faces; front-of-house only.
- **Docs:** when you change behavior or numbers, update the relevant doc and the issue lists.

---

## 13. Troubleshooting

| Symptom | Fix |
| --- | --- |
| Web app shows "Couldn't load … run `make demo-bundle`" | The bundle is missing or partial: `make demo-bundle` (or pull it from git). |
| Lab says "Start the API (`make serve`)" | That combination isn't precomputed. Run `make serve` in another terminal, then reload the page. |
| `make demo-bundle` seems stuck after "bundle --grid" | It is writing AI narrations (rate-limited to 15 calls/min, so this step can take 15–30 min). Wait, or run `LLM_PROVIDER=mock make demo-bundle` for template text in a few minutes. Don't press Ctrl+C. |
| Narration says "(template)" | No LLM key, or the free-tier limit was hit. Add `GEMINI_API_KEY` to `engine/.env`, or wait a minute. |
| Numbers in the app don't match the engine CLI | The bundle is older than the engine. `make cache-clean && make demo-bundle`. |
| `uv` complains about Python | Needs Python ≥ 3.13: `uv python install 3.13`. |
| Port 8000 or 5173 is busy | Stop the old server, or `uv run dcsim serve --port 8001` and change the proxy target in `web/vite.config.ts`. |
| Smoke test can't start a browser | It uses your installed Google Chrome (`playwright-core`, `channel: "chrome"`). Install Chrome. |
