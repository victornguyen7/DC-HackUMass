# dcsim: DC What-If simulation engine

SimPy model of a Worcester night, **dinner 4:30–9:00 pm + late night 9:00 pm–12:00 am**, on the **real menu** (see `../docs/SPEC.md` §7 and `../docs/ENGINE.md`). Engine 0.6.2.

What one night includes: arrivals by date (weekday and event multipliers), an entrance swipe queue, friends who arrive and sit together, students with diets (incl. pescetarian) and allergies, who pick lines from where they stand and stay in them; staffed and self-serve stations (servers can change by hour); pans as SimPy Containers with hold times, hotel-pan size limits and a hot-hold cabinet; a kitchen with limited cooks and equipment; seats counted per zone.

```bash
cd engine
uv sync
uv run dcsim prepare                     # real menu snapshot -> per-date menus + baseline kitchen plans
uv run dcsim menu --date 2026-11-13      # simulated items by station, roles, diet tags, plan
uv run dcsim run --date 2026-11-13       # 30 nights, medians [p10-p90]; --all lists every dish
uv run dcsim compare --preset smaller_batches --date 2026-11-13
uv run dcsim trace --date 2026-11-13 --out trace.json
uv run dcsim bench
uv run pytest -q                          # incl. golden Nov 13 baseline + the team's calibration targets
DCSIM_UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py   # after an intended change to the numbers
uv run dcsim tsdb similar --date 2026-11-14 --weather rain   # past nights most like this one, on Tiger (docs/TIGER.md §7)
uv run dcsim tsdb bench                                      # raw hypertable vs continuous aggregate timings (docs/TIGER.md §8)
uv run dcsim tsdb query stack                                # one SQL query across aggregates, nights, plan, forecasts, ledger
uv run dcsim ask "one more server at Grill and 10% busier"   # plain English -> settings (Gemini function calling)
uv run dcsim agent "cut waste 20% without any vegetarian dish running out" --tries 5   # Ask Sam: plan (1-5 tries)
uv run dcsim agent "what if we add a Grill server and it's 10% busier?"               # Ask Sam: what-if
uv run dcsim agent "Which dishes ran out on rainy Fridays?"                           # Ask Sam: answered from Tiger
uv run dcsim bundle --waste-only                             # refresh calibration, ledger snapshot and the waste outlook (seconds)
uv run dcsim optimize --date 2026-11-13                      # search kitchen plans: waste-vs-stockouts frontier (--save keeps it in Tiger)
uv run dcsim worlds --date 2026-11-13 --preset smaller_batches   # a plan in plausible worlds + waste tornado (--save)
uv run dcsim tsdb searches                                   # saved plan searches, levers that keep showing up (docs/TIGER.md §10)
uv run dcsim tsdb dishes --name "Butter Chicken" --role entree   # dish-popularity check + look-alikes on Tiger (§9)
```

Presets: `smaller_batches`, `trim_slow`, `extra_server`, `theme_night`, `combined`, `forecast_reorder` (cook to the expected rush, with safety stock).

## Backend API

```bash
uv run dcsim serve                 # http://127.0.0.1:8000, docs at /docs
uv run dcsim serve --warm          # also precompute baseline + presets for every date in the background
make server-test                   # test API on :8001: a copy of the cache, Tiger ledger writes off (DCSIM_LEDGER_WRITES=0)
uv run dcsim cache stats|prune     # results live in <repo>/cache (DCSIM_CACHE to move it)
```

| Endpoint | What it does |
| --- | --- |
| `POST /api/simulate` | Body `{date, preset?, levers?, label?, n_nights=30}`. `200 {status: done, result}` if every night is cached, else `202 {job_id, stream}` |
| `GET /api/jobs/{id}/stream` | Server-Sent Events: `progress` every 8 nights, `provisional` results, then `final` (or `error`) |
| `GET /api/jobs/{id}` | Job status and final result (polling fallback) |
| `GET /api/results/{hash}` | Result from cached nights (`?n_nights=`); `?include=series` adds per-minute p10/p50/p90 of students inside, lines and pan levels (re-runs the nights, ~1–2 s) |
| `POST /api/compare` | `{baseline_hash, scenario_hash, n_nights}` → paired diff and win rate |
| `GET /api/traces/{hash}` | One night's per-student events + pan events for 3D (median night, or `?seed=`), gzipped JSON |
| `GET /api/dates`, `/api/menu/{date}`, `/api/arrivals/{date}`, `/api/layout`, `/api/presets`, `/api/facts/waste` | Reference data for the pages |
| `GET/POST /api/scenarios`, `POST /api/scenarios/{id}/adopt`, `DELETE /api/scenarios/{id}` | Saved and adopted what-ifs (one adopted per date); DELETE needs the admin token when `DCSIM_ADMIN_TOKEN` is set |
| `POST /api/warm`, `GET /api/warm` | Admin job: precompute baseline (+ presets) for dates (`{dates?, presets, n_nights}`); GET polls progress |
| `GET /tiger` | the live Tiger dashboard page (`src/dcsim/static/tiger.html`); not linked from the web app ([docs/TIGER.md §8](../docs/TIGER.md)) |
| `GET /api/tiger/dashboard` | `?night=` → what that page shows: waste by night and pans by hour from the continuous aggregates, the one-query join, ledger, hypertable and columnstore facts; 503 without Tiger ([docs/TIGER.md §8](../docs/TIGER.md)) |
| `GET /api/tiger/bench` | `?repeat=` (1–10) → the same questions timed on the raw hypertables and on their aggregates |
| `GET /api/similar-nights` | `?date=&students=&weather=&event_night=&menu_date=&limit=` → recorded nights before `date` most like it, with their waste and stockouts. Tiger pgvector, else the local synthetic files; 422 on bad input, 503 if Tiger fails after connecting ([docs/SIMILAR_NIGHTS.md](../docs/SIMILAR_NIGHTS.md)) |
| `POST /api/backtest`, `GET /api/backtest` | Admin job: re-run the back-test (`{plan?, regenerate_history}`) → `backtest.json`; GET polls |
| `GET /api/health` | `{ok, engine_version, cache, ledger_writes}`; `ledger_writes` is false when Tiger isn't set up or `DCSIM_LEDGER_WRITES=0` |
| `POST /api/ask` | `{question, date?}` → settings (not run). With Gemini: **function calling** (13 lever functions, ids as enums, one re-ask), then JSON schema, then keywords; `method` says which answered |
| `POST /api/agent` | **Ask Sam**: `{goal, date?, max_tries 1-5 (default 5), n_nights 3-60}` → `202 {job_id, stream}`. Goals are planned (≤ 5 simulations, goal check in code, re-checked on unseen seeds), what-ifs simulated as asked, questions answered from Tiger. At most `DCSIM_AGENT_MAX_RUNNING` runs at once (429) |
| `GET /api/agent/{id}/stream`, `GET /api/agent/{id}` | SSE steps: `start`, `goal`, `tiger`, `try_start`, `progress`, `try`, `whatif`, `notice`, `thought`, `validate_start`, `final` (or `error`); GET = status + result |
| `GET /api/agent/history`, `GET /api/agent/history/{id}`, `DELETE …/{id}` | Sam's final analyses (prompt, menu date, time asked, plan/answer/what-if; no per-try details); DELETE needs the admin token when set |
| `GET /api/waste/outlook` | `?n_nights=` → simulated pre-consumer waste (lb, $, stockouts) of every bundled scenario on the coming dates, keyed like the bundle manifest (`outlook.py`) |

AI routes (`/api/ask`, `/api/narrate`, `/api/explain`, `/api/ai/dish-prior`, `/api/ai/similar-dishes`, `POST /api/agent`) are limited to `DCSIM_AI_RATE` requests per minute per client IP (default 20 → 429). Comparisons (`vs_baseline`, `/api/compare`) give each metric a verdict: `better`, `worse`, `inconclusive`, or `same` when the median difference is below a per-metric minimum (`runner.min_meaningful`; e.g. 0.5 min of wait).

A result holds `open`/`close`/`dinner_close`/`late_close`, `headline` (median and 10–90% range of waste, stockout-minutes, missed first choice, wait p90, missed meal, students, plus kitchen wait, cook busy share, empty-pan wait, seat wait, peak seated), `cook_sheet` (per dish incl. `period`: portions to prepare, batch plan, run-out window), `flags` (run-outs, long lines, diet coverage), `stations`, `arrivals` (expected per 10 min), `staffing` (servers needed per station and hour), `median_seed`, `waste_lb` (pre-consumer waste in lb, $ and portions with stockout minutes, as 10/50/90% ranges, in the recorded history's units; `outlook.py`), and `vs_baseline` for anything that isn't the baseline. Per night (`NightMetrics`) there are also swipe wait, group students, and allergy students / allergy students with no meal.

Levers (`dcsim/levers.py`): `crowd_multiplier` (snapped to 5%; scales dinner and late night), `crowd_shift_min`, `stations.{id}.servers / service_median_min / shifts` (`[[from_min, servers], ...]`), `kitchen.{dish_id}.{batch_size, starting_batches, reorder_point, max_batches, last_batch_min}`, `kitchen_staff.{cooks, equipment}`, `popularity.{dish_id}` (snapped to 0.1), `remove_dishes`, `add_dishes`. Snapping keeps nearby slider positions on the same cached result.

Storage is one SQLite file (`cache/results.sqlite`: configs, per-seed nights, scenarios, LLM responses, Sam's history `agent_history`) plus gzipped traces. Nights are stored per (config hash, seed), so 30 → 60 nights only runs the new seeds and every comparison reuses the date's baseline. The hash includes the engine and data versions, so editing any input file or engine logic can never serve a stale result. Measured on this laptop: cold preset vs. baseline (60 nights) 2.5 s with a provisional result at 1.7 s; cache hit 60 ms; new preset with cached baseline 1.5 s; trace 0.26 s.

## Data flow

```
data/raw/menu/worcester_menus_*.json   (real umassdining.com snapshot)
        │  dcsim prepare: classify roles, map diet labels, drop add-ons from the sim
        ▼
data/worcester/menus/<date>_dinner.json + <date>_late_night.json ──► planner (pilot nights, neutral popularity,
        │                                        │                     each dish over its own period; late-night taper)
        │                                        ▼
        │                         data/worcester/kitchen/<date>_dinner.json   (plans for dinner and "late." dishes)
        ▼
load_baseline(date) + dc.json + stations.json + personas.json + overrides.json
                    + kitchen_defaults.json + crowd_by_date.json ──► ScenarioConfig
```

Edit `data/worcester/overrides.json` to fix a role (then re-run `prepare`) or to set a staff popularity multiplier (applies at load time; the baseline plan intentionally ignores it, so favorites run out).
`station_popularity` in the same file multiplies every dish at a station and the baseline plan does know it (well-known busy stations); `dish_popularity` does the same for single dishes (Noodle Bowl, ISSUES E6); `station_by_date` moves a dish to another station on one date (Nov 18 carving line). `favorite_patience_mult` in `dc.json` lets students wait longer in a line that serves their first choice. Every guessed input is listed in `data/worcester/ASSUMPTIONS.md`.

## Layout

| Module | Role |
| --- | --- |
| `config.py` | `ScenarioConfig` (Pydantic, frozen), validation, canonical JSON, `config_hash` |
| `menu_import.py` | Real menu → roles (entrée/starch/side/soup/dessert/bread/add-on), diet tags, vessels |
| `planner.py` | Baseline production plan from pilot demand |
| `data.py` | Load a date's config; popularity = role prior ÷ station dilution × overrides; `data_version` |
| `rng.py` | Counter-based random numbers keyed by (seed, stream, ids) for common random numbers |
| `arrivals.py` | Class-wave arrival intensity per service period (dinner, late night), each on its own random stream; thinning from a fixed candidate pool |
| `population.py` | Persona assignment, attributes, allergies, groups, Gumbel taste ranking (logit choice) over the arrival period's dishes, diet eligibility |
| `model.py` | SimPy night: swipe queue, decide-from-a-distance line choice, pans as `PanStock` Containers with lots / pan size / hot-hold cabinet, event-driven kitchen with cooks and equipment, taper and forecast reordering, empty-pan waits, seats, staffing shifts, to-order stations |
| `recorder.py`, `metrics.py` | Change logs → `NightMetrics` (incl. servers needed per hour); `build_trace` for 3D; `night_series` per-minute |
| `runner.py` | `run_night`, `run_scenario` (process pool), `summarize`, `compare`, `median_night`, `series_summary` |
| `presets.py` | Preset what-ifs as pure config transforms |
| `levers.py` | What-If Lab request → config (presets + snapped levers) |
| `reports.py` | Cook sheet, alert flags, arrivals, station table from a summary |
| `store.py` | SQLite + trace files cache, saved/adopted scenarios |
| `service.py` | Cache-aware orchestration: missing seeds in batches of 8, results, traces |
| `api.py` | FastAPI app, background jobs with in-flight dedupe, SSE streaming |
| `outlook.py` | `waste_summary` (pre-consumer lb/$/portions per simulated night, same conversion as the back-test) and `outlook` (every bundled scenario on the coming dates) |
| `ai/ask.py` | Plain English → settings: Gemini function calling (`ask_tools`, `calls_to_plan`), JSON-schema and keyword fallbacks |
| `ai/agent.py` | Ask Sam: goal/what-if/question, the tool loop (`run_simulation`, `finish`, setting functions, Tiger tools), `check_goal` in code, rule-based fallback, hold-out check, history entries |
| `ai/tiger_mcp.py` | Sam's MCP client of `tiger mcp start` (only `db_schema` / `db_query`), `guard_sql` (one read-only SELECT), schema digest |
| `similar_nights.py` | Similar nights for the page and API: input checks, students tie-break and local fallback over `nightsearch.py`'s vectors |
| `nightsearch.py` | The similar-night search (page, API and `dcsim tsdb similar`): 77-dim night vectors in `dcsim.night_vectors` (DiskANN index), held-out check |
| `optimize.py` | Optimize plan: Optuna multi-objective search over station/dish multipliers, frontier re-checked on unseen seeds |
| `worlds.py` | Plausible worlds (`ScenarioConfig.uncertainty`): crowd, rush and popularity drawn per seed, new dishes with their own spread; sweep + waste tornado |
| `dishsearch.py` | Dish look-alikes across every menu (111-dim vectors in `dcsim.dish_vectors`, DiskANN), what they sold, a new dish's spread, the per-dish popularity check |
| `searchlog.py` | Plan searches kept in Tiger: `plan_searches`, hypertable `search_trials`, `search_worlds`; the "which lever keeps showing up" query |

## Guarantees (tested)
- **Golden baseline:** Nov 13 headline numbers (seeds 1–10) are pinned in `tests/golden/nov13.json`; any change that moves them fails until the file is rewritten on purpose.
- **Calibration targets** (`data/worcester/calibration_targets.json`): peak dinner line 30–45, busiest arrivals 7–8 pm, fewer than 30 inside at 11:30 pm, and few dinner dishes running out after 8:30 pm (all pass since 0.6.1).
- **Clean nights:** no server held, nobody waiting, lots equal to the pan level at the end of every night.
- Same config + seed → identical metrics; a no-op change gives a paired difference of exactly 0.
- Cooked = served + expired + leftover, for every dish and night; to-order dishes never waste or run out.
- A bigger crowd keeps every baseline student; removing a dish keeps everyone's order of the rest.
- Diet rules hold (Halal = labeled Halal or Plant Based); items at one station are taken in one pass.
- All 8 real dinner and late-night menus load; key classifications (entrée vs add-on etc.) are pinned by tests.
- Students never walk to a line and leave it; nobody is served after midnight; students eat from the period they arrived in; allergic students never get a dish labeled with their allergen; pescetarians only eat pescetarian-tagged dishes.

## Waste history, back-test and demo bundle

```bash
make demo-bundle      # = dcsim waste-gen && dcsim backtest && dcsim bundle --grid   (~5 min)
```

- `dcsim waste-gen` (`synthetic.py`): 6 weeks of SYNTHETIC recorded nights in `data/synthetic/worcester/` (MetaFoodX-style `consumption.csv`, LeanPath-style `waste_log.csv`, `nights.csv`). Noise settings are in `ASSUMPTIONS.md`; the per-night values are hidden in `_ground_truth.json`.
- `dcsim backtest` (`backtest.py`): re-simulates each recorded night's menu and student count under the baseline plan and the recommended plan (10 nights each) → `backtest.json`, with the reduction split into "cooking to the forecast" vs "the plan's changes", stockouts next to it, and predicted-vs-recorded for adopted nights. It also writes per-dish rows (`dishes`: recorded vs. plan lb per night served, change %, $ saved per week) and a per-station split on each night (`by_station`) for the Waste Record page. Served at `GET /api/waste/history` and `/api/waste/backtest`.
- `dcsim bundle` (`bundle.py`): writes `web/public/cache/` so the frontend works with no API, including `waste/outlook.json` (the coming nights' simulated waste for every bundled scenario; `--waste-only` refreshes it with the calibration and ledger snapshot in seconds). Look results up in `manifest.json` by key `"<date>|<preset or baseline>|<lever>"`, e.g. `2026-11-13|baseline|`, `2026-11-13|theme_night|`, `2026-11-13|baseline|crowd_multiplier=1.1`, `2026-11-13|baseline|stations.grill.servers=3` (lever grid with `--grid`). Traces are `.json.gz`; decompress in the browser with `DecompressionStream('gzip')`. Full bundle: 254 results + 13 traces + 13 narration files (156 narrations), about 18 MB, about 4 minutes plus narration time (the builder waits for the Gemini rate limit). The committed bundle is from engine 0.4.1; rebuild it once on the final engine.

## Tiger Data schema script (for the Tiger console)

`engine/sql/tiger_website.sql` creates every `dcsim` table in Tiger Data (TimescaleDB + pgvector): the tables `dcsim tsdb load` / `dcsim calibrate` fill, the columns added since engine 0.4 (late night, kitchen capacity, waits, seats, swipe, groups, allergies, per-period stockouts and waste), new fact tables (`dish_nights`, `station_hours`, `minute_series`, `night_vectors`), category tables seeded from the current data (`cat_values`, `periods`, `stations`, `dishes`, `personas`, `allergies`, `date_context`, `kitchen_resources`) and website views (`v_nights`, `v_waste_by_reason`, `v_menu`). Paste it into the Tiger console's SQL editor or run `psql "$DCSIM_TSDB_URL" -f engine/sql/tiger_website.sql`. It is idempotent. Regenerate after data changes with `uv run python -m dcsim.tiger_sql > sql/tiger_website.sql` (a test fails if the committed file is stale). `dcsim tsdb load` fills observed rows (incl. waste cost and students/waste by period) and the category tables; `dcsim calibrate` fills every new metric column on simulated nights and `dish_nights`, `station_hours`, `minute_series` from its replays (see `docs/TIGER.md` → Schema).

## Web app

The web app in `web/` (Simulation with the What-If Lab, Kitchen plan, Insights) reads this bundle, so it works with no API: `make web` (dev server on :5173), `make web-test`, `make web-build`. With `make server` running too, they also replay any scenario, narrate any student, and use the plain-English box, menu edits, new dishes, "Why?", Insights › Similar nights and **Ask Sam**. See `web/README.md` and `docs/UI_ISSUES.md`.

## Not built yet
Nav graph and table-level seats (zones are built; seat limits off until real seat counts) · add-ons consuming stock · breakfast and lunch · survey calibration · LLM personas (paused; narration is built) · animated 3D Hall. Built since 0.4: late night, kitchen capacity, staffing by hour, per-minute series, allergies, groups, swipe queue, pan sizes; on 2026-11-14 Ask Sam, function calling for the plain-English box, the waste outlook, the optimizer, plausible worlds, dish search and plan searches kept in Tiger (no engine version change). Out of scope: image input (menu photos).
