# dcsim: DC What-If simulation engine

SimPy model of a Worcester dinner on the **real menu** (see `../SPEC.md` §7 and `../ENGINE.md`).

```bash
cd engine
uv sync
uv run dcsim prepare                     # real menu snapshot -> per-date menus + baseline kitchen plans
uv run dcsim menu --date 2026-11-13      # simulated items by station, roles, diet tags, plan
uv run dcsim run --date 2026-11-13       # 30 nights, medians [p10-p90]; --all lists every dish
uv run dcsim compare --preset smaller_batches --date 2026-11-13
uv run dcsim trace --date 2026-11-13 --out trace.json
uv run dcsim bench
uv run pytest -q
```

Presets: `smaller_batches`, `trim_slow`, `extra_server`, `theme_night`, `combined`.

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
| `GET /api/results/{hash}` | Result from cached nights (`?n_nights=`) |
| `POST /api/compare` | `{baseline_hash, scenario_hash, n_nights}` → paired diff and win rate |
| `GET /api/traces/{hash}` | One night's per-student events + pan events for 3D (median night, or `?seed=`), gzipped JSON |
| `GET /api/dates`, `/api/menu/{date}`, `/api/arrivals/{date}`, `/api/layout`, `/api/presets`, `/api/facts/waste` | Reference data for the pages |
| `GET/POST /api/scenarios`, `POST /api/scenarios/{id}/adopt`, `DELETE /api/scenarios/{id}` | Saved and adopted what-ifs (one adopted per date) |

A result holds `headline` (median and 10–90% range of each metric), `cook_sheet` (per dish: portions to prepare, batch plan, run-out window), `flags` (run-outs, long lines, diet coverage), `stations`, `arrivals` (expected per 10 min), `median_seed`, and `vs_baseline` for anything that isn't the baseline.

Levers (`dcsim/levers.py`): `crowd_multiplier` (snapped to 5%), `crowd_shift_min`, `stations.{id}.servers / service_median_min`, `kitchen.{dish_id}.{batch_size, starting_batches, reorder_point, max_batches, last_batch_min}`, `popularity.{dish_id}` (snapped to 0.1), `remove_dishes`. Snapping keeps nearby slider positions on the same cached result.

Storage is one SQLite file (`cache/results.sqlite`: configs, per-seed nights, scenarios) plus gzipped traces. Nights are stored per (config hash, seed), so 30 → 60 nights only runs the new seeds and every comparison reuses the date's baseline. The hash includes the engine and data versions, so editing any input file or engine logic can never serve a stale result. Measured on this laptop: cold preset vs. baseline (60 nights) 2.5 s with a provisional result at 1.7 s; cache hit 60 ms; new preset with cached baseline 1.5 s; trace 0.26 s.

## Data flow

```
data/raw/menu/worcester_menus_*.json   (real umassdining.com snapshot)
        │  dcsim prepare: classify roles, map diet labels, drop add-ons from the sim
        ▼
data/worcester/menus/<date>_dinner.json ──► planner (pilot nights, neutral popularity)
        │                                        ▼
        │                         data/worcester/kitchen/<date>_dinner.json
        ▼
load_baseline(date) + stations.json + personas.json + overrides.json ──► ScenarioConfig
```

Edit `data/worcester/overrides.json` to fix a role (then re-run `prepare`) or to set a staff popularity multiplier (applies at load time; the baseline plan intentionally ignores it, so favorites run out).
`station_popularity` in the same file multiplies every dish at a station and the baseline plan does know it (well-known busy stations). `favorite_patience_mult` in `dc.json` lets students wait longer in a line that serves their first choice.

## Layout

| Module | Role |
| --- | --- |
| `config.py` | `ScenarioConfig` (Pydantic, frozen), validation, canonical JSON, `config_hash` |
| `menu_import.py` | Real menu → roles (entrée/starch/side/soup/dessert/bread/add-on), diet tags, vessels |
| `planner.py` | Baseline production plan from pilot demand |
| `data.py` | Load a date's config; popularity = role prior ÷ station dilution × overrides; `data_version` |
| `rng.py` | Counter-based random numbers keyed by (seed, stream, ids) for common random numbers |
| `arrivals.py` | Class-wave arrival intensity; thinning from a fixed candidate pool |
| `population.py` | Persona assignment, attributes, Gumbel taste ranking (logit choice), diet eligibility |
| `model.py` | SimPy night: one line per station visit, pans with lots, event-driven kitchen, to-order stations |
| `recorder.py`, `metrics.py` | Change logs → `NightMetrics`; `build_trace` for 3D |
| `runner.py` | `run_night`, `run_scenario` (process pool), `summarize`, `compare`, `median_night` |
| `presets.py` | Preset what-ifs as pure config transforms |
| `levers.py` | What-If Lab request → config (presets + snapped levers) |
| `reports.py` | Cook sheet, alert flags, arrivals, station table from a summary |
| `store.py` | SQLite + trace files cache, saved/adopted scenarios |
| `service.py` | Cache-aware orchestration: missing seeds in batches of 8, results, traces |
| `api.py` | FastAPI app, background jobs with in-flight dedupe, SSE streaming |

## Guarantees (tested)
- Same config + seed → identical metrics; a no-op change gives a paired difference of exactly 0.
- Cooked = served + expired + leftover, for every dish and night; to-order dishes never waste or run out.
- A bigger crowd keeps every baseline student; removing a dish keeps everyone's order of the rest.
- Diet rules hold (Halal = labeled Halal or Plant Based); items at one station are taken in one pass.
- All 8 real dinner menus load; key classifications (entrée vs add-on etc.) are pinned by tests.

## Waste history, back-test and demo bundle

```bash
make demo-bundle      # = dcsim waste-gen && dcsim backtest && dcsim bundle --grid   (~5 min)
```

- `dcsim waste-gen` (`synthetic.py`): 6 weeks of SYNTHETIC recorded nights in `data/synthetic/worcester/` (MetaFoodX-style `consumption.csv`, LeanPath-style `waste_log.csv`, `nights.csv`). Noise settings are in `ASSUMPTIONS.md`; the per-night values are hidden in `_ground_truth.json`.
- `dcsim backtest` (`backtest.py`): re-simulates each recorded night's menu and student count under the baseline plan and the recommended plan (10 nights each) → `backtest.json`, with the reduction split into "cooking to the forecast" vs "the plan's changes", stockouts next to it, and predicted-vs-recorded for adopted nights. It also writes per-dish rows (`dishes`: recorded vs. plan lb per night served, change %, $ saved per week) and a per-station split on each night (`by_station`) for the Waste Record page. Served at `GET /api/waste/history` and `/api/waste/backtest`.
- `dcsim bundle` (`bundle.py`): writes `web/public/cache/` so the frontend works with no API. Look results up in `manifest.json` by key `"<date>|<preset or baseline>|<lever>"`, e.g. `2026-11-13|baseline|`, `2026-11-13|theme_night|`, `2026-11-13|baseline|crowd_multiplier=1.1`, `2026-11-13|baseline|stations.grill.servers=3` (lever grid with `--grid`). Traces are `.json.gz`; decompress in the browser with `DecompressionStream('gzip')`. Full bundle: 254 results + 13 traces + 13 narration files (156 narrations), about 17 MB, about 4 minutes plus narration time (the builder waits for the Gemini rate limit).

## Web app

The four pages (Hall, Dashboard, Waste Record, What-If Lab) are in `web/` and read this bundle, so they work with no API: `make web` (dev server on :5173), `make web-test`, `make web-build`. With `make serve` running too, they also replay any scenario, narrate any student, and use the plain-English box, menu edits, new dishes and "Why?". See `web/README.md` and `docs/UI_ISSUES.md`.

## Not built yet
Nav graph and table-level seats (seating zones and door routes are built) · servers by hour and "servers needed" · add-ons consuming stock · full-day arrivals · survey calibration · LLM personas (paused; narration is built) · optimizer · uncertainty sweep · animated 3D Hall.
