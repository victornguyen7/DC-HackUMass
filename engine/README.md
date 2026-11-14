# dcsim: DC What-If simulation engine

SimPy model of a Worcester night, **dinner 4:30–9:00 pm + late night 9:00 pm–12:00 am**, on the **real menu** (see `../docs/SPEC.md` §7 and `../docs/ENGINE.md`). Engine 0.6.1.

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
```

Presets: `smaller_batches`, `trim_slow`, `extra_server`, `theme_night`, `combined`, `forecast_reorder` (cook to the expected rush, with safety stock).

## Backend API

```bash
uv run dcsim serve                 # http://127.0.0.1:8000, docs at /docs
uv run dcsim serve --warm          # also precompute baseline + presets for every date in the background
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
| `GET/POST /api/scenarios`, `POST /api/scenarios/{id}/adopt`, `DELETE /api/scenarios/{id}` | Saved and adopted what-ifs (one adopted per date) |

A result holds `open`/`close`/`dinner_close`/`late_close`, `headline` (median and 10–90% range of waste, stockout-minutes, missed first choice, wait p90, missed meal, students, plus kitchen wait, cook busy share, empty-pan wait, seat wait, peak seated), `cook_sheet` (per dish incl. `period`: portions to prepare, batch plan, run-out window), `flags` (run-outs, long lines, diet coverage), `stations`, `arrivals` (expected per 10 min), `staffing` (servers needed per station and hour), `median_seed`, and `vs_baseline` for anything that isn't the baseline. Per night (`NightMetrics`) there are also swipe wait, group students, and allergy students / allergy students with no meal.

Levers (`dcsim/levers.py`): `crowd_multiplier` (snapped to 5%; scales dinner and late night), `crowd_shift_min`, `stations.{id}.servers / service_median_min / shifts` (`[[from_min, servers], ...]`), `kitchen.{dish_id}.{batch_size, starting_batches, reorder_point, max_batches, last_batch_min}`, `kitchen_staff.{cooks, equipment}`, `popularity.{dish_id}` (snapped to 0.1), `remove_dishes`, `add_dishes`. Snapping keeps nearby slider positions on the same cached result.

Storage is one SQLite file (`cache/results.sqlite`: configs, per-seed nights, scenarios) plus gzipped traces. Nights are stored per (config hash, seed), so 30 → 60 nights only runs the new seeds and every comparison reuses the date's baseline. The hash includes the engine and data versions, so editing any input file or engine logic can never serve a stale result. Measured on this laptop: cold preset vs. baseline (60 nights) 2.5 s with a provisional result at 1.7 s; cache hit 60 ms; new preset with cached baseline 1.5 s; trace 0.26 s.

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
- `dcsim bundle` (`bundle.py`): writes `web/public/cache/` so the frontend works with no API. Look results up in `manifest.json` by key `"<date>|<preset or baseline>|<lever>"`, e.g. `2026-11-13|baseline|`, `2026-11-13|theme_night|`, `2026-11-13|baseline|crowd_multiplier=1.1`, `2026-11-13|baseline|stations.grill.servers=3` (lever grid with `--grid`). Traces are `.json.gz`; decompress in the browser with `DecompressionStream('gzip')`. Full bundle: 254 results + 13 traces + 13 narration files (156 narrations), about 18 MB, about 4 minutes plus narration time (the builder waits for the Gemini rate limit). The committed bundle is from engine 0.4.1; rebuild it once on the final engine.

## Web app

The four pages (Hall, Dashboard, Waste Record, What-If Lab) are in `web/` and read this bundle, so they work with no API: `make web` (dev server on :5173), `make web-test`, `make web-build`. With `make serve` running too, they also replay any scenario, narrate any student, and use the plain-English box, menu edits, new dishes and "Why?". See `web/README.md` and `docs/UI_ISSUES.md`.

## Not built yet
Nav graph and table-level seats (zones are built; seat limits off until real seat counts) · add-ons consuming stock · breakfast and lunch · survey calibration · LLM personas (paused; narration is built) · optimizer · uncertainty sweep · animated 3D Hall. Built since 0.4: late night, kitchen capacity, staffing by hour, per-minute series, allergies, groups, swipe queue, pan sizes.
