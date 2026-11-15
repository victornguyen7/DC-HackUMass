# Tiger Data: observed nights and simulated runs side by side

The engine's results cache stays in SQLite (`cache/results.sqlite`). **Tiger Data (TimescaleDB)** holds the
evidence: every observed night and every simulated stand-in for it, in the same tables, so one SQL query
compares them. Calibration reads its targets from Tiger and writes its forecasts and scores back.

```bash
tiger db save-password ht1fm93al5        # once (or put DCSIM_TSDB_URL=postgresql://... in engine/.env)
make calibrate                            # = waste-gen -> dcsim tsdb load -> dcsim calibrate   (~40 s)
make tsdb-compress                        # convert older chunks to the columnstore, print sizes
cd engine && uv run dcsim tsdb query coverage     # or: status situations weekdays pans dishes production ledger nights calibrations
uv run dcsim tsdb query "SELECT ..."              # any SQL
uv run dcsim forecast --date 2026-11-15 --preset smaller_batches   # log a forecast in the ledger (adopting in the app does this too)
uv run dcsim tsdb observe --date 2026-11-15       # SYNTHETIC: let that night "happen", then see it scored
uv run dcsim tsdb similar --date 2026-11-14 --weather rain   # past nights most like this one (pgvector), + held-out check
uv run dcsim tsdb bench                           # raw hypertable vs continuous aggregate, server-side ms (§8)
uv run dcsim tsdb refresh                         # bring every continuous aggregate up to date now
uv run dcsim tsdb query stack                     # one query across the stack; also: hypertables aggregates waste-daily waste-dishes pans-hourly
uv run dcsim tsdb track                           # score every observed night now, weekly track record with a verdict (§4)
uv run dcsim calibrate --situations-only          # refit the crowd error by weather/event from Tiger, no new simulations (§5)
uv run dcsim forecast --date 2026-11-15 --weather rain   # a forecast that records (and, if the fit was kept, uses) the situation
uv run dcsim tsdb dishes --name "Butter Chicken" --role entree   # dish-popularity check + a new dish's look-alikes (§9)
uv run dcsim optimize --date 2026-11-13 --save    # keep an optimizer run in Tiger (also: dcsim worlds --save) (§10)
uv run dcsim tsdb searches                        # saved searches + the levers that keep showing up on the frontier (§10)
uv run dcsim bundle --waste-only                  # refresh calibration + ledger files in the demo bundle (seconds)
```

Live dashboard: `make server`, then open `http://127.0.0.1:8000/tiger` (§8). It is served by the API only, not by the web app. API: `GET /api/tiger/dashboard`, `GET /api/tiger/bench`.

Code: `engine/src/dcsim/tsdb.py` (schema, loaders, ledger, named queries), `engine/src/dcsim/calibration.py`
(protocol, forecasts), `engine/src/dcsim/nightsearch.py` (`dcsim tsdb similar`), `engine/src/dcsim/similar_nights.py` (the Similar nights page; same vectors and table as `nightsearch.py`), `engine/src/dcsim/dishsearch.py` (dish search + popularity check, §9), `engine/src/dcsim/searchlog.py` (plan searches, §10). API: `GET /api/waste/calibration`, `GET /api/forecasts?date=`, `GET /api/similar-nights` ([SIMILAR_NIGHTS.md](SIMILAR_NIGHTS.md)), `POST /api/dish-evidence`, `GET /api/searches[?date=&kind=]`, `GET /api/searches/changes`, `GET /api/searches/{id}`, and adopting a scenario
(`POST /api/scenarios/{id}/adopt`, or `POST /api/scenarios` with `status: "adopted"`) logs its forecast. The What-If
Lab's **Adopt** button makes that call when the API is up, so adopting in the web app writes to the ledger. Page: `web/public/calibration.html`.

## What is built on Tiger

| # | Feature | Status | Where |
| --- | --- | --- | --- |
| 0 | Observed nights and simulated runs side by side; held-out calibration | built | `dcsim tsdb load`, `dcsim calibrate` |
| 1 | **Forecast ledger**: forecasts written before the night, append-only, scored automatically | built | `dcsim.forecasts`, `forecast_scorecard`, adopt hook |
| 2 | **Calibration page**: held-out ranges vs observed, ledger table | built | `web/public/calibration.html` |
| 3 | **Columnstore compression** with an automatic policy | built | `dcsim tsdb compress` |
| 4 | **Calibration over time**: every night scored into a hypertable by a nightly Tiger job, rolled up by week in a real-time continuous aggregate | built 2026-11-14 (§4) | `dcsim tsdb track`, `dcsim.forecast_results`, `dcsim.forecast_weekly`, job `score_forecasts` |
| 5 | **Weather and event flags** in the crowd error: fitted on training weeks, kept only if held-out CRPS improves | built 2026-11-14; **not kept** on this history (§5) | `dcsim calibrate --situations-only`, `forecast --weather` |
| 6 | **Tiger MCP** for live questions: schema comments for the assistant, verified question set | built 2026-11-14 (§6) | `COMMENT ON` in `tsdb.COMMENTS`; questions below |
| 8 | **Live dashboard**: two real-time continuous aggregates with refresh policies, raw-vs-aggregate benchmark, one query across the stack | built 2026-11-14 (commit `78d9239`; §8) | `dcsim tsdb bench`, `/api/tiger/*`, API page `/tiger` |
| 7 | **Similar-night search** with pgvector + a pgvectorscale DiskANN index | built | `dcsim tsdb similar`, `dcsim.night_vectors` (§7); API + web page handoff: [SIMILAR_NIGHTS.md](SIMILAR_NIGHTS.md) |
| 9 | **Dish search + dish-popularity check**: look-alike dishes across every menu (pgvector + DiskANN), what they sold, a new dish's popularity spread in plausible worlds; per-dish popularity error measured from observed vs replay | built 2026-11-14 (§9); **no dish-level error found** on this synthetic history (τ 0.021) | `dcsim tsdb dishes`, `POST /api/dish-evidence`, `dcsim.dish_vectors` |
| 10 | **Plan searches in Tiger**: every Optimize plan / Plausible worlds run kept (search row, one hypertable row per plan tried, one row per world); "which lever keeps showing up on the frontier" in SQL | built 2026-11-14 (§10) | `dcsim tsdb searches`, `GET /api/searches[...]`, `dcsim.plan_searches`, `search_trials`, `search_worlds` |

## Schema (`dcsim`)

| Table | Kind | Rows (current run) | What |
| --- | --- | --- | --- |
| `nights` | hypertable on `ts` (dinner open, Eastern) | 42 observed + ~5,000 simulated | one row per night or simulated night: students, cooked, waste lb (expired / close), stockout min, plus the **situation** copied onto every row (weekday, weather, event night, adopted plan, train/test split) and the sim inputs (crowd multiplier, production ratio) |
| `pan_windows` | hypertable | 15.7k observed + 157k replay | per dish per 30-min window: portions put out / eaten / left (MetaFoodX `consumption.csv` shape) |
| `waste_log` | hypertable | 8.9k | observed LeanPath-style weigh-ins |
| `planned_batches` | table | 418 | the plan each observed night was meant to follow |
| `calibrations` | table | 1 per run | fitted parameters, held-out scores, recovery check (JSONB) |
| `dish_nightly` | **continuous aggregate** over `pan_windows` | | per dish per night per run: cooked, eaten, left at close, empty windows. Bucketed in local time, because dinner crosses midnight UTC |
| `waste_daily_by_dish` | **continuous aggregate** over `waste_log`, real-time, refreshed hourly | 5,590 from 10,655 | per night, dish and reason: portions, lb, weigh-ins. The "day" starts at 6 am Eastern so a whole night is one bucket; has a `night` column for joins |
| `pan_hourly` | **continuous aggregate** over `pan_windows`, real-time, refreshed every 30 min | 23,150 from 212,296 | per night, scenario, dish and clock hour: portions out / eaten, lowest and highest level, sum of levels, empty windows, windows |
| `forecast_check` | view | | per observed night and scenario: simulated p10 / p50 / p90 and whether the observation fell inside |
| `forecasts` | table, **append-only** | 1 per forecast | the ledger: naive and calibrated p10 / p50 / p90 for students, waste, stockouts, cooked, plus samples (JSONB) |
| `night_vectors` | table + DiskANN index | 1 per observed night | 77-dim night vector (weekday, weather, event, hashed menu) for `dcsim tsdb similar` and the Similar nights page |
| `dish_vectors` | table + DiskANN index | 268 (one per dish id) | 111-dim dish vector (station, role, period, diet, allergens, hot/vessel, hashed name words) for the new-dish search (§9) |
| `plan_searches` | table | 1 per saved search | Optimize plan / Plausible worlds run: date, weekday, limits, baseline, summary (frontier with unseen-night check, or win rates + tornado) as JSONB (§10) |
| `search_trials` | hypertable on `ts` | 40 per optimizer run | every plan tried: waste, stockouts, wait, within limits, on the frontier, unseen-night waste % and stockout cost, params, levers, readable changes |
| `search_worlds` | table | 2 per world | per world of a worlds run: baseline and plan waste, stockouts, wait |
| `forecast_scorecard` | view | | each ledger forecast next to the observed night: `issued_before_open`, `inside` / `outside` / `awaiting` |
| `dish_nights` | hypertable (engine 0.6.2 loader) | ~23k (replays) | per dish per replay night: cooked, served, expired, leftover, waste, stockout min, run-out time, first-choice demand, period |
| `station_hours` | hypertable | ~31k (replays) | per station and hour: servers planned (shifts), servers needed for ≤ 85% busy, peak line that hour |
| `minute_series` | hypertable | ~263k (replay seed 1) | per minute: students inside (`kind='inside'`, key `hall`) and line length per station (`kind='queue'`) |
| category tables | tables | | `periods`, `stations`, `dishes` (447: all dates, dinner + late night, diet tags, allergens), `personas`, `allergies`, `date_context`, `kitchen_resources`, `cat_values` (code labels); upserted by every `dcsim tsdb load` |
| `v_nights`, `v_waste_by_reason`, `v_menu` | views | | for the website: codes joined to labels, night context from the night's own date |

**Columns added since engine 0.4** (`tsdb.NEW_COLUMNS`, added by every `dcsim tsdb init/load`):
- `nights`: waste cost; students by period; missed first choice; missed meals; wait p50/p90; lines skipped; empty pans seen; students inside at 11:30 pm; kitchen batch wait and cook busy share; equipment peaks (JSONB); empty-pan wait; seat wait and peak seated; swipe wait and queue; group students; allergy students and allergy no-meal; stockouts and waste by period; engine version and config hash.
  - **Simulated rows** (`forecast_*`, `replay`) fill all of them.
  - **Observed rows** fill only what a real record has: waste cost, students by period (swipe times), waste by period (weigh-ins). The live Nov 14 night was loaded before this and keeps them empty; `dcsim tsdb observe --date 2026-11-14` would refill it but also regenerate its observation.
- `pan_windows`, `waste_log`: `period` (dinner / late_night). `planned_batches`: period, cook/hold time, last batch, expected demand, late-night taper fields, forecast reorder.

The same schema is one console script: `engine/sql/tiger_website.sql` (`uv run python -m dcsim.tiger_sql`).

`scenario` tells the rows apart:

| scenario | when | crowd | production | seeds |
| --- | --- | --- | --- | --- |
| `observed` | the record | as happened | as happened | — |
| `forecast_naive` | before the night | expected | plan as written | 20 |
| `forecast_calibrated` | before the night | drawn from the fitted crowd error (5 strata) | drawn from training nights' ratios (3 strata) | 4 per draw = 60 |
| `replay` | after the night | that night's observed count | that night's measured ratio | 10, with pan windows |

## Calibration protocol (`dcsim calibrate`)

1. **Split by date.** Train on Oct 1–28 (28 nights). Hold out Oct 29–Nov 11 (14 nights, including the 3 adopted-plan nights). Nothing from the held-out weeks reaches the fit.
2. **Fit two things a kitchen records but the engine doesn't know:**
   - *Crowd forecast error:* observed students divided by the engine's expected students, fitted as a lognormal. The engine's own seed-to-seed spread is subtracted so it isn't counted twice. Source in real life: meal swipes.
   - *Production ratio:* how much bigger the kitchen's batches are than the plan's. It is computed **in SQL from the pans** (`dcsim tsdb query production`): after the opening stock, the smallest refill a dish gets in one window is one batch, and that divided by the planned batch size gives the ratio. Source in real life: MetaFoodX pan data plus the FoodPro plan (data request R06).
3. **Forecast each held-out night** two ways: naive (what the app shows today) and calibrated.
4. **Score:** the share of nights whose observed value falls inside the simulated 10–90% range (target 80%), bias, and CRPS (a proper score in the metric's own unit; lower is better).
5. **Replay** every night with its observed crowd and production ratio, with pan windows. This separates "the engine's kitchen and demand model is off" from "the forecast inputs are off".
6. **Recovery check.** Possible only because the data is synthetic: the fitted values are compared with the generator's hidden ones. It tests the pipeline, not the engine.

### Result (engine 0.6.2, SYNTHETIC history, `cal-20261114T044443`)

| Held-out nights (n = 14) | naive inside 10–90% | calibrated inside 10–90% | naive bias / CRPS | calibrated bias / CRPS |
| --- | --- | --- | --- | --- |
| Pre-consumer waste (lb) | **1 / 14** | **9 / 14** | +98.5 / 96.9 | +51.3 / 51.6 |
| Stockout minutes | 5 / 14 | 10 / 14 | −5.7 / 15.0 | +5.0 / 12.9 |
| Students | 1 / 14 | 10 / 14 | −104 / 270 | −87 / 209 |

Engine 0.6.2 numbers (dinner + late night, kitchen capacity, crowd by date). Replay with crowd and production known: waste bias +8.4 lb, CRPS 15.2 lb; stockouts bias +0.6 min. Recovery: production 1.080 fitted vs 1.084 true (per-night correlation 0.997); crowd log-sd 0.077 vs 0.074. Training production ratio median 1.065 (sd 0.058); adopted nights 1.00, 1.02, 1.05. The bullets below were written for engine 0.3.3 (table: waste 1/14 → 10/14, students 1/14 → 11/14); the pattern is the same.

A perfectly calibrated 80% range lands **8–14 of 14** inside, 95% of the time, so 10 and 11 are consistent with honest ranges. The naive ranges come only from random seeds, which is too narrow: the forecast the app shows today is overconfident.

What else it found:

- **Adopted nights are different.** On the 3 nights that followed the recommended plan, the measured production ratio was 1.00, 1.03 and 1.05, against 1.07 in training (sd 0.056): staff stuck closer to the plan. The calibrated waste forecast covers 9 of 11 non-adopted nights but only 1 of 3 adopted ones. Adopted nights need their own production distribution once there are enough of them.
- **The held-out weeks were quieter.** Observed students ran about 90 below the forecast median on both methods, which is why waste bias stays positive. Weather doesn't explain it: clear nights were also about 150 below forecast (`dcsim tsdb query situations`). With 14 nights this is consistent with ordinary week-to-week drift, and a longer history would show whether the crowd forecast needs a trend term.
- **The replay is close.** With crowd and production known, waste bias is −1 lb and CRPS is 14 lb, and pan levels match window by window (`query pans`). The remaining forecast error is mostly about *inputs* (who shows up, how big the kitchen cooks), not the kitchen model.
- **Recovery:** production ratio 1.083 fitted vs 1.084 true (per-night correlation 0.99); crowd log-sd 0.078 vs 0.074.

### What this does and doesn't show

- It **does** show that the ranges the app would display, fitted only on past weeks, contain later nights about as often as they claim, and that a forecast without calibration would not.
- It **does not** show the engine matches Worcester. The "observed" nights are generated by the same engine (ISSUES B1), so a 0.99 recovery mostly confirms the plumbing. The protocol, queries and tables are built for real MetaFoodX / LeanPath / swipe exports in the same shape. Load those into `dcsim.nights` / `pan_windows` / `waste_log` and rerun `dcsim calibrate`, with no code change.
- The over-prep estimator works so well partly because the generator models over-prep as bigger batches. A real kitchen might add extra pans instead, which the smallest-refill estimate would miss.
- Weather and event flags are the only fields loaded from the generator's `_ground_truth.json`, because a manager knows them on the day. Crowd multiplier, over-prep and plate-waste share never enter the database. The API serves only error statistics from the recovery check, never the hidden values.

## 1. Forecast ledger

The held-out check above is still run after the fact. The ledger makes the claim checkable as the
calibration is used: a forecast is written down **before** the night, by a database that won't let it change.

- **When it's written:** adopting a scenario in the app (or saving one as adopted) queues a background job that logs two
  forecasts for that night: naive and calibrated, using the latest `dcsim.calibrations` row. `dcsim forecast` does
  the same from the CLI. If Tiger isn't configured, adoption still works and the response says
  `"forecast": "not logged (Tiger not configured)"`.
- **Why it can be trusted:** `issued_at` is set by the database clock (`DEFAULT now()`), not the client.
  Triggers reject `UPDATE`, `DELETE` and `TRUNCATE` on `dcsim.forecasts`, so a bad forecast stays visible and a
  revised one is a new row with a later time. (A database owner could still drop the trigger. This guards against
  mistakes and quiet edits; it isn't cryptographic proof.)
- **Scoring:** `dcsim.forecast_scorecard` joins every forecast to the observed night once it exists and marks
  `issued_before_open`, then `inside` / `outside` / `awaiting` for waste and stockouts. `dcsim tsdb query ledger`,
  `GET /api/forecasts`.
- **Observations after the history:** real exports load as `scenario = 'observed'`. For the demo,
  `dcsim tsdb observe --date D` generates a SYNTHETIC night under the plan the latest forecast assumed, with the
  same noise model as the history, and stores it with `run_id = 'observed:live'`. Live nights never enter the
  calibration's train/test split, and reloading the history never deletes them.

First entries (Nov 13): forecasts for Nov 13 (baseline) and Nov 14 (smaller batches). Nov 14 was then observed
(SYNTHETIC): 744 lb. The calibrated range 625–776 contained it; the naive range 658–698 did not. Nov 13 is
still `awaiting`, which leaves one to observe live in the demo.

## 2. Calibration page

`web/public/calibration.html` is a standalone page. It reads the demo bundle by default
(`cache/waste/calibration.json`, `cache/waste/forecasts.json`), or the live API with
`?api=http://127.0.0.1:8000`. It has:

- headline tiles (nights inside range, CRPS, bias, the 8–14 band an honest range would give, recovery r);
- a range chart per held-out night: naive and calibrated 10–90% ranges with medians, the observed night as a dot,
  switchable between waste, stockouts and students, with a hover/focus tooltip and a table view;
- the forecast ledger with inside / outside / awaiting status;
- the caveats, in plain words.

This page is still self-contained: the React app in `web/` doesn't link to it or reuse its chart yet (it opens at
`/calibration.html` on the dev server). Port the chart into the Waste Record page, or link it from there.
After `make calibrate` or new ledger entries, run `dcsim bundle --waste-only` to refresh its data.

## 3. Columnstore compression

The three hypertables use Tiger's columnstore. They are segmented by what queries and calibrate's
delete-and-reload filter on (`scenario`, and `dish_id` for pans), so a re-run drops whole compressed segments.
`add_columnstore_policy(after => 1 day)` converts chunks as they age. `dcsim tsdb compress` converts now,
recompresses chunks that calibrate rewrote, vacuums, and reports:

| table | row data | columnstore | ratio |
| --- | --- | --- | --- |
| minute_series | 29.5 MB | 0.7 MB | 40.0× |
| pan_windows | 34.5 MB | 4.1 MB | 8.4× |
| station_hours | 3.8 MB | 0.7 MB | 5.5× |
| dish_nights | 5.4 MB | 2.2 MB | 2.4× |
| nights | 1.3 MB | 0.6 MB | 2.2× |
| waste_log | 1.7 MB | 1.2 MB | 1.4× |
| total | 76.3 MB | 9.6 MB | **7.9×** |

Measured 2026-11-14 after `dcsim tsdb compress` (36 chunks, all six hypertables; on disk 22.7 MB, down from
about 87 MB before the three newer tables were converted). The earlier three-table figure was 37.5 MB → 6.0 MB (6.3×).
On-disk total for those three is 13–16 MB (higher right after a calibrate, until autovacuum catches up), because each chunk keeps fixed index and heap overhead and the newest week stays in
row-store until it is a day old. Calibrate still runs (33 s) with identical scores on compressed tables.

## 7. Similar-night search

`dcsim tsdb similar --date D [--weather rain] [--event] [-k 5]` lists the observed nights most like D and what
happened on them, then runs a held-out check. Each observed night is one 77-dim vector in `dcsim.night_vectors`
(extension `vector`, DiskANN index from `vectorscale`; falls back to an HNSW index without it). The Similar nights
page and `GET /api/similar-nights` use the same vectors, table and index (ISSUES T5, merged 2026-11-14), and order
equally similar nights by how close their crowd was to the expected students:

| block | dims | from |
| --- | --- | --- |
| weekday | 7 | one-hot |
| weather | 4 | clear / rain / cold / snow; all zero when not given, so it can't favour any past night |
| event night | 2 | one-hot (no / yes), so every recorded night has the same length |
| menu | 64 | the dishes put out that night (`pan_windows`), feature-hashed; for D, its planned dinner menu |

Blocks are scaled to unit length and weighted, and distance is cosine. It is a hand-built feature vector, not a
learned embedding, so "similar" means "same kind of night, same menu". The command rebuilds the vectors on each
run (43 nights, under a second).

**Held-out check (SYNTHETIC history, k = 5, neighbours from the 28 training nights, 14 test nights), mean absolute error, run 2026-11-14:**

| outcome | similar nights | training mean | same-weekday mean |
| --- | --- | --- | --- |
| students | **282.0** | 367.7 | 285.5 |
| waste lb | 81.8 | 139.2 | **75.2** |
| stockout min | 19.6 | **18.4** | 19.0 |

(The 76-dim vector before T5 gave the same numbers on this history; the earlier 226.6 / 74.3 / 25.3 table was from
an earlier copy of the history in Tiger, most likely the one before the ISSUES T3 reload.) It beats the overall average clearly on students and waste but is
no better than "average of the same weekday". The synthetic history
reuses 7 menus, one per weekday, so menu and weekday carry the same information here, and the five Saturdays tie
at the same distance. Treat it as a lookup ("what happened last time we served this on a Saturday"), not a
forecast. It earns more with real history, where menus rotate independently of the weekday.

## 8. Live dashboard: aggregates, benchmark, one query across the stack

Built 2026-11-14 from the Tiger track plan (unified stack, hypertables, continuous aggregates, compression,
show the performance, forecasting pitch). **Status: working, tested and committed (`78d9239`; the page moved from the web app to the API on 2026-11-14).** What is left is at
the end of this section.

### What the plan asked for, and where it stands

| Plan item | Status | What exists |
| --- | --- | --- |
| 1. Unified stack: one query joins metric streams to plans and forecasts | ✅ | `dcsim tsdb query stack` (`tsdb.STACK_SQL`): two continuous aggregates + `nights` hypertable + `planned_batches` + simulated forecast range (`forecast_check`) + ledger (`forecasts`), one statement |
| 2. Hypertables | ✅ already there | All six fact tables were hypertables before this work (`dcsim tsdb query hypertables`). **Don't run** the plan's `create_hypertable(..., migrate_data => true)` lines; they aren't needed |
| 3. Continuous aggregates | ✅ | `waste_daily_by_dish` (the plan's, corrected) and `pan_hourly` (the plan's "second one"), both real-time with refresh policies |
| 4. Compression | ✅ already there | Columnstore on all six hypertables with a 1-day policy (§3). **Don't run** the plan's `ALTER TABLE ... timescaledb.compress` / `add_compression_policy(..., '7 days')`: the columnstore is already on, with a different segment-by |
| 5. Show the performance | ✅ | `dcsim tsdb bench`, `GET /api/tiger/bench`, and the dashboard's "Same question, raw table or aggregate" card |
| 6. Pitch: forecasting engine scored in SQL | ✅ data, wording below | Ledger + `forecast_scorecard` (§1), shown on the dashboard |
| Dashboard refreshing from the aggregate | ✅ | API page `/tiger` (`engine/src/dcsim/static/tiger.html`), polls `GET /api/tiger/dashboard` every 5 s |

### The two aggregates

Defined in `tsdb.DASH_CAGGS`; `init_schema` creates them, `tsdb.refresh()` fills them, and the console script
(`engine/sql/tiger_website.sql`) has the same SQL.

- **`waste_daily_by_dish`** over `waste_log`: `bucket, night, station, dish_id, dish, reason, period, portions,
  weight_lb, events`. Policy: every hour, last 3 days.
- **`pan_hourly`** over `pan_windows`: `bucket, night, source, scenario, station, dish_id, dish, period,
  portions_out, portions_eaten, min_left, max_left, sum_left, windows_empty, windows`. Policy: every 30 min, last 2 days.
  Runs of one scenario (the 10 replay seeds) are pooled; average level = `sum_left / windows`.

Three things differ from the SQL in the plan, each for a reason:

1. **The day starts at 6 am Eastern, not midnight UTC.** Dinner runs 4:30 pm to midnight Eastern and close-out
   weigh-ins land after midnight, so a UTC day (the plan's `time_bucket('1 day', ts)`) splits every night across
   two buckets. The bucket is `time_bucket('1 day', ts, 'America/New_York', origin 06:00)`, and `night` is grouped
   too, so the aggregate joins straight to `nights` and `forecasts`. Checked: 43 nights, 43 buckets, correct on
   both sides of the Nov 1 clock change.
2. **Real-time aggregation is on** (`materialized_only = false`): rows newer than the last materialized bucket are
   read from the hypertable at query time. Checked with a rolled-back insert: a weigh-in for a later night showed in
   the aggregate with no refresh. A change to an *older* night shows after a refresh (the policy, `dcsim tsdb
   refresh`, or the loaders, which refresh on every load).
3. **A dish's last pan window is the close-out** (pans are pulled, so the level reads 0). `pan_hourly` keeps
   `max_left` so the level before closing is still there, and `PANS_SQL` marks each dish's `closing` hour. Counting
   those zeros as "ran empty" would be wrong (838 of 882 zero readings in the recorded data are close-outs).

An aggregate's definition can't be changed in place. Each one names a `marker` column of its current shape, and
`DROP_DRAFT_CAGG` drops an older draft that lacks it. That is how the first `waste_daily_by_dish`, created in the
Tiger console from the plan's SQL (UTC buckets, 131 rows, one day), was replaced.

### Raw table vs aggregate (`dcsim tsdb bench`)

Server-side time (plan + execute, from `EXPLAIN ANALYZE`), median of 5 runs, after `dcsim tsdb compress`.
Tiger Cloud, TimescaleDB 2.32, Postgres 18, SYNTHETIC six-week history. Every pair returned identical rows.

| # | Question | Raw hypertable | Aggregate | Result |
| --- | --- | --- | --- | --- |
| 1 | Portions eaten and average on hand by hour, every night and scenario | `pan_windows`, 212,296 rows: **557 ms** | `pan_hourly`, 23,150 rows: **82 ms** | aggregate 6.8× faster |
| 2 | One night's pans by dish and hour (the dashboard's chart) | 3.1 ms | 1.2 ms | aggregate 2.5× faster |
| 3 | Portions put out and eaten per dish, replays of every night | 5.5 ms | 8.9 ms | raw 1.7× faster |
| 4 | Waste by night and reason | `waste_log`, 10,655 rows: 3.0 ms | `waste_daily_by_dish`, 5,590 rows: 4.2 ms | about the same |

What to say about it:

- **#1 is the aggregate's case.** The raw query works out a local-time hour for every row; the aggregate has one
  row per dish and hour.
- **#3 is the columnstore's case.** It groups by the columns the columnstore is segmented by (`scenario, dish_id`),
  and the plan shows `VectorAgg` over `ColumnarScan`: compressed batches are summed directly. Before the chunks
  were re-converted (right after a calibrate reload), the same raw query took 230–360 ms and the aggregate won by
  about 27×. So: aggregate for fresh data, columnstore once it settles.
- **#4 is a small table.** About 250 weigh-ins a night, and the aggregate only halves the rows.
- The tables are small, so these show direction, not what a year of data would do. Say so if asked.

### The dashboard (`engine/src/dcsim/static/tiger.html`, served at `/tiger`)

Standalone page, same style as the calibration page. **It is not part of the web app**: the engine API serves it
at `GET /tiger` (hidden from the OpenAPI docs), so open `http://127.0.0.1:8000/tiger` with `make server` running.
The React app has no link to it and the Vite dev server doesn't serve it. It polls every 5 s and shows:

- tiles (nights, weigh-ins, pan readings, columnstore ratio, ledger size);
- **waste by night**, stacked by reason, from `waste_daily_by_dish`, with a table view, and the most wasted dishes;
- **pans by hour** for a chosen night, from `pan_hourly` (12 dishes whose pans ran lowest);
- the **raw vs aggregate** timings, with a "Run again" button;
- the **one-query** table (latest 10 nights) and the **forecast ledger**;
- **storage**: chunks in the columnstore, row data vs columnstore, aggregate freshness.

It needs Tiger: without it the status line gives the API's reason (for example "Tiger not configured"). If a poll
fails after the page has loaded, the last good data stays on screen, dimmed. There is no offline snapshot (an
earlier version wrote one into the web bundle; it was removed with the page). Reads take about 1.2 s per poll from a
laptop, most of it connecting to Tiger Cloud.

### Demo script (about 2 minutes)

1. Open `http://127.0.0.1:8000/tiger`. "Weigh-ins and pan levels are hypertables. These charts read continuous aggregates."
2. In a terminal: `cd engine && uv run dcsim tsdb observe --date 2026-11-13`. This writes a SYNTHETIC Nov 13
   night. Within 5 s the page shows "New night: Fri, Nov 13", a new bar appears in the gap before Nov 14, and the
   Nov 13 ledger rows change from "Awaiting the night" to inside or outside. **It has not been run yet, on
   purpose: it only looks new once.** (Running it again rewrites the same night with no visible change.)
3. Scroll to "Same question, raw table or aggregate", press **Run again**. Point at #1 (aggregate) and #3
   (columnstore).
4. "One query across the stack": `uv run dcsim tsdb query stack`, or paste `tsdb.STACK_SQL` in the Tiger console.
5. Storage card: 76 MB of row data in 9.6 MB.

**Pitch:** "A forecasting engine for dining-hall food waste where every prediction is written down before the
night and scored against what happened, in plain SQL on Tiger Data." Say that the recorded nights are simulated
(`source`, `scenario`, `run_id`, `seed` show it) and that the claim is a validated pipeline ready for real kitchen
exports, not a measured saving. Check the sponsor's submission page for rules such as the Tiger Cloud tier.

### What was changed on the live Tiger service (2026-11-14)

- Dropped the console-made `waste_daily_by_dish` (and its policy job 1007) and recreated it; created `pan_hourly`.
  Policy jobs 1008 (hourly) and 1010 (every 30 min). Both aggregates refreshed over the whole history.
- Ran `dcsim tsdb compress`: `dish_nights`, `station_hours` and `minute_series` moved to the columnstore.
- No fact rows were added, changed or deleted. `tsdb observe` for Nov 13 was **not** run.

Later on 2026-11-14 (items 4-6, `dcsim tsdb track` and `dcsim calibrate --situations-only`):

- `dcsim tsdb init` objects added: hypertable `forecast_results` (by `night`, 28-day chunks), procedure
  `score_forecasts`, job 1016 (daily, 6 am Eastern), real-time continuous aggregate `forecast_weekly` with policy
  job 1015 (hourly), columns `forecasts.weather` / `forecasts.event_night`, comments on the schema, 17 tables and views, and their key columns.
- New calibration row `cal-20261114T143301` (copy of `cal-20261114T044443` plus `params.crowd.situational`,
  `kept: false`), and `data/synthetic/worcester/calibration_report.json` updated to match (fields added only).
- Fact tables unchanged. Nov 13 still not observed; when it is, `load_live_night` scores it into the track record.

### Handoff: what is left

Done and verified: engine code, 10 tests (`engine/tests/test_tiger_dashboard.py`; full engine suite 161 passed,
2 skipped after the page moved to the API), aggregates live on Tiger, the page checked in a browser at desktop, phone and dark,
live and offline, with no console errors. Web unit tests pass (46); the page is no longer in the web app or its smoke script.

Still to do:

1. **Restart `make server`.** An API started before this work answers 404 on `/tiger` and `/api/tiger/*`.
2. **Rehearsal reset for step 2 of the demo.** There is no `unobserve`. To rehearse and then reset:
   `DELETE` the `run_id = 'observed:live' AND night = '2026-11-13'` rows from `nights` and `pan_windows`, the
   Nov 13 rows from `waste_log`, then `dcsim tsdb refresh`. Worth a small CLI action if rehearsing more than once.
3. Ideas not started: a longer history (the "500-night" query) so the timings mean more, columnstore on the
   aggregates themselves, a track-record card on the `/tiger` dashboard (the data is in `forecast_weekly`).
   (A **Plan searches** card was added 2026-11-14, §10.)

## 4. Calibration over time

One score is an anecdote; a score every week is a track record. `dcsim.forecast_results` (a hypertable on `night`)
holds one scored row per observed night and method, from two origins:

- `backtest`: `forecast_check`, naive on all 42 recorded nights, calibrated on the 14 held-out ones;
- `ledger`: `forecast_scorecard`, the latest forecast per night and method **issued before doors opened**
  (later ones don't count; 'awaiting' nights aren't scored).

`dcsim.score_forecasts()` fills it (back-test rows replaced, ledger rows upserted). A Tiger job runs it every
morning at 6 am Eastern, after the close-out weigh-ins, and the loaders call it after each write (`tsdb load`,
`calibrate`, `tsdb observe`), so an observed night shows up straight away. `dcsim.forecast_weekly` is a real-time
continuous aggregate over it by week. Weeks start on Thursday 2026-10-01, so weeks 5 and 6 are exactly the held-out split.

`dcsim tsdb track` prints it with a verdict: a calibrated 80% range should hold on 3-7 of 7 nights 95% of the time
(`binomial_range`). Result on the SYNTHETIC history (2026-11-14):

| week | naive (waste inside / 7) | calibrated |
|---|---|---|
| Oct 1, 8, 15, 22 (training) | 2, 1, 0, 1: **too narrow** every week | not scored (fit on these) |
| Oct 29 (held out) | 0: too narrow | 3: as calibrated (bias +64 lb, crowd -164) |
| Nov 5 (held out) | 1: too narrow | 6: as calibrated |
| Nov 12 (ledger, 1 night) | 0 of 1 | 1 of 1 |

The naive range misses most nights in every week, so its failure isn't a one-week fluke. The calibrated range
holds in both held-out weeks, but on Oct 29 it only just holds (3 is the low edge), and that week's crowd was
164 students below forecast. Seven nights a week is a small sample, so the verdict catches drift, not fine shifts.

## 5. Weather and event flags in the crowd error

`calibration.fit_situations`: on the training nights only, log(observed / expected students) = intercept +
effect[rain, cold, snow] + effect[event], each effect shrunk towards 0 as if 4 more nights had shown none (ridge;
2 cold nights shouldn't move a forecast much). `score_situations` compares it with the pooled crowd error on the
held-out nights (students, engine seeds x 25 crowd quantiles) and **keeps it only if CRPS is lower**. `dcsim
calibrate` does this every run; `dcsim calibrate --situations-only` does it from what is already in Tiger (the
naive forecast's students per seed give each night's expected crowd) and logs a new calibration row.

When kept, `dcsim forecast --weather rain [--event]` draws the calibrated crowd from that situation's fit. Either
way the forecast records the weather and event flag it assumed (`forecasts.weather`, `forecasts.event_night`).
Adopting in the app doesn't pass a situation yet, so those forecasts use the pooled error.

Result (`cal-20261114T143301`, SYNTHETIC):

| situation | training nights | fitted (shrunk) | generator's hidden effect |
|---|---|---|---|
| rain | 9 | -3.2% | -6% |
| cold | 2 | -0.9% | -3% |
| event | 3 | +3.5% | +15% |
| snow | 0 | 0 | none |

Held-out students CRPS: **205.8 situational vs 200.0 pooled, so not kept**; forecasts keep the pooled error. The
fit points the right way, but the held-out weeks don't reward it. Clear held-out nights came in 170 students below
the median forecast and cold ones 267 above (`forecast_results`, split test), the opposite of what the fit
expects. With 2 rainy, 3 cold and no event nights held out, the test can't tell a real effect from noise. Re-run
it with more history; the rule stays the same.

## 6. Live questions through the Tiger MCP

Tiger's MCP server (`tiger mcp install claude-code`, which uses the CLI's login) gives an assistant `db_schema`
and `db_query` against this service. To answer questions well it needs to know which rows are observed and how
"ran out" is measured, so `dcsim tsdb init` now writes comments on the main tables, columns and aggregates
(`tsdb.COMMENTS`); `db_schema` with `comments: true` shows them.

Questions answered live through the MCP on 2026-11-14, as a demo set:

| Question | Answer (SYNTHETIC history) | How |
|---|---|---|
| Which dishes ran out on rainy Fridays? | 3 rainy Fridays (Oct 2, 9, 16). Cumin Basmati Rice (tandoor) at 6:00 pm on one, Savory Baked Chicken Thigh (grill) at 11:00 pm on one; nothing ran out on more than one | `pan_windows` observed, `portions_left = 0`, excluding each dish's close-out window |
| Did forecasts hold on rainy nights? | Held-out rain: calibrated 1 of 2 inside, naive 0 of 2; crowd about 250 below forecast | `forecast_results`, origin backtest, split test |
| Has the calibration drifted? | No: as calibrated in both held-out weeks (3/7, 6/7); naive too narrow every week | `forecast_weekly` |
| Does weather explain the crowd? | Partly in training (rain -3%), not on held-out nights, so it isn't used | `calibrations.params->'crowd'->'situational'` |

Safety: the server's read-only mode applies only to services tagged **PROD**. This service is tagged DEV, so
`db_query` runs any SQL as `tsdbadmin`, and the server also exposes `service_delete`, `service_resize` and
`service_update_password`. For the demo: keep Claude Code's per-call approval on and deny the `service_*` tools,
or set the service's environment to PROD in the Tiger console if it allows that. By the MCP server's own
instructions it then connects read-only and refuses service changes; the engine connects directly, so it isn't
affected (not tested here). The ledger triggers block edits to `dcsim.forecasts` either way.

**Sam uses the same MCP server (2026-11-14).** The Ask Sam page and `dcsim agent` run `tiger mcp start stdio` and call
`db_schema` and `db_query` themselves (`engine/src/dcsim/ai/tiger_mcp.py`). Each query is checked in code first
(single SELECT, no writes) and wrapped in `BEGIN TRANSACTION READ ONLY` with a 20 s timeout. The server's own
read-only mode covers only PROD services and this one is DEV, so that guard is what keeps Sam read-only.
Details: SPONSORS.md §2.

Similar-night search: the CLI version is in §7 below. A teammate's API and web page version (`GET /api/similar-nights`,
exact pgvector cosine scoring, local synthetic fallback) is described in [the integration handoff](SIMILAR_NIGHTS.md).

## 9. Dish search and the dish-popularity check

The plausible-worlds tornado ranks dish popularity second for waste (±30% per dish moves waste by 290 portions on Nov 13), and that ±30% is a guess. Tiger holds every recorded dish-night, so two questions can be asked of it (`engine/src/dcsim/dishsearch.py`):

**1. Is each dish's popularity off?** For every recorded night, a dish's observed portions eaten (`dish_nightly`, `observed`) is compared with the replay of the same night (`replay`: that night's crowd and production). The log ratio mixes noise with any real per-dish error; `popularity_check` separates them (method-of-moments empirical Bayes: τ² = var(dish means) − mean(σ²/n), each dish's factor shrunk toward 1 by how many nights back it).

- **Result on Tiger (`uv run dcsim tsdb dishes`):** 240 dishes, 2,142 dish-nights, noise sd 0.093, **τ = 0.021: no dish-level popularity error detected** (threshold 5%).
- That is the honest answer, not a weak method: the synthetic generator (`synthetic.py`) draws crowd, weather, over-prep and plate waste per night, but no per-dish popularity error, so there is none to find. Worlds therefore keeps the flat ±30% for menu dishes. With R04 consumption data the same check would measure the real spread.
- **The method works when there is something to find** (`tests/test_dishsearch.py`): on a fixture of 60 dishes × 8 nights with a known spread τ = 0.25 and noise 0.1, it recovers τ within ±0.07, its shrunk factors correlate > 0.8 with the truth, and with τ = 0 it reports nothing.

**2. What is a new dish like?** Every dish id on every menu date is a 111-dim vector in `dcsim.dish_vectors` (station 16 hashed, role 6, period 2, diet 3, allergens 16 hashed, hot + vessel 4, name words 64 hashed; blocks unit length and weighted; cosine distance; DiskANN index). It is a hand-built feature vector, not a learned embedding.

- `POST /api/dish-evidence` returns the k = 5 nearest dishes with what they sold (portions per student, from the observed history), and the new dish's popularity spread for plausible worlds: `sqrt(0.30² + s²)`, where s is the sd of log portions-per-student among the look-alikes (dishes that look alike but sell differently → a wider spread).
- Example: "Butter Chicken" (entree) → Mandarin Chicken w/Cashews, Kung Pao Peanut Chicken, Savory Baked Chicken Leg / Thigh, Chicken Noodle; they sold 3.4–6.8 portions per 100 students over 30 recorded nights; spread ±42% (look-alikes differ by ±30%).
- **Where it shows:** the Lab's new-dish form ("In Tiger: dishes like this … sold …"), and `POST /api/worlds`: when the plan adds dishes and the request doesn't set `uncertainty.new_dish_popularity_sd`, the API fills it from Tiger (`new_dish_sd_source: "tiger"`), so a new dish is less certain than a known one. Without Tiger the dish gets the ordinary ±30%.
- `Uncertainty.new_dish_popularity_sd` is left out of the config hash when unset, so no cached config changed.

## 10. Plan searches kept in Tiger

Optimize plan and Plausible worlds results used to live only in the API's memory. `engine/src/dcsim/searchlog.py` keeps every finished run:

| Table | One row per |
| --- | --- |
| `plan_searches` | search: kind, menu date, weekday, engine version, baseline / plan hash, limits, baseline, summary (JSONB: frontier with its unseen-night check and lever labels, or win rates and tornado) |
| `search_trials` (hypertable on `ts`, columnstore segment-by `menu_date`) | plan the optimizer tried: waste, stockouts, p90 wait, within limits, on the frontier, unseen-night waste % and stockout minutes added, params (multipliers), levers (frontier plans), readable changes |
| `search_worlds` | world (seed) and side (baseline / plan) of a worlds run |

- **When it saves:** the API saves after a run finishes and before `final` goes out, so the result carries `tiger: {saved, search_id | reason}` (Lab: "Saved to Tiger ✓" or "Not saved to Tiger (writes off)"). It is gated like the ledger: Tiger configured and `DCSIM_LEDGER_WRITES` ≠ 0, so `make server-test` writes nothing. A failed save never fails the run. CLI: `dcsim optimize --save` / `dcsim worlds --save` (`--source` tags it). These tables are not append-only.
- **Reads:** `GET /api/searches[?date=&kind=]`, `GET /api/searches/changes`, `GET /api/searches/{id}` (a past optimize run in its live shape, so **Load into builder** works on it). Reads never create tables. The Lab's Optimize card lists past searches for the date and "What keeps showing up on the frontier"; the `/tiger` dashboard has a **Plan searches** card.
- **"Which lever keeps showing up?"** (`searchlog.RECURRING_CHANGES_SQL`): per search parameter (e.g. `batch:soups`), over frontier plans that were re-checked on unseen nights, how often it moved down or up, its mean multiplier, and the mean waste and stockout change of those plans. `BY_WEEKDAY_SQL` gives the median best unseen-night waste change per weekday, one value per search.
- **On Tiger now:** one 40-trial Nov 13 run (`source = 'cli-test'`, search `09178621d771`). Its three frontier plans all made **Soups batches smaller (avg ×0.77)**, Latino batches smaller (×0.80), Teriyaki Salmon batches smaller (×0.80), and lowered the Display, Grill, Mediterranean and Seasons reorder points (×0.87). Unseen-night results: −28.6% waste for +48.6 stockout min, −27.4% / +35.9, −28.6% / +5.8. One run is not a pattern; the query becomes useful as runs accumulate across dates.
- The tables have `COMMENT ON` text, so the Tiger MCP and Sam can answer "which change keeps showing up?" from the schema.

```sql
-- the lever query, as the dashboard runs it
SELECT p.key, count(*) FILTER (WHERE p.value::float8 < 0.999) AS lower, count(*) AS frontier_plans,
       round(avg(p.value::float8)::numeric, 2) AS avg_multiplier
FROM dcsim.search_trials t CROSS JOIN LATERAL jsonb_each_text(t.params) AS p(key, value)
WHERE t.on_frontier AND t.validated_waste_pct IS NOT NULL
GROUP BY p.key ORDER BY lower DESC LIMIT 8;
```

## Useful queries

```sql
-- Average portions left at each window, observed vs replay, and over how many nights
SELECT to_char(ts AT TIME ZONE 'America/New_York','HH24:MI') AS win, scenario,
       count(DISTINCT night) AS nights, round(avg(portions_left),1) AS avg_left
FROM dcsim.pan_windows WHERE dish_id = 'grill.golden_bbq_chicken_thigh'
GROUP BY 1,2 ORDER BY 1,2;

-- Rainy held-out nights: did the calibrated range hold?
SELECT night, waste_lb, round(waste_p10) p10, round(waste_p90) p90, waste_inside
FROM dcsim.forecast_check WHERE scenario = 'forecast_calibrated' AND weather = 'rain';

-- How waste responds to the kitchen's production ratio (simulated draws)
SELECT over_prep, count(*), round(avg(waste_lb)::numeric,1) FROM dcsim.nights
WHERE scenario = 'forecast_calibrated' GROUP BY 1 ORDER BY 1;
```

The `pans` query's last window (20:30) shows 0 left because it includes the close-out, when pans are pulled.
