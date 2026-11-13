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
uv run dcsim bundle --waste-only                  # refresh calibration + ledger files in the demo bundle (seconds)
```

Code: `engine/src/dcsim/tsdb.py` (schema, loaders, ledger, named queries), `engine/src/dcsim/calibration.py`
(protocol, forecasts). API: `GET /api/waste/calibration`, `GET /api/forecasts?date=`, and adopting a scenario
(`POST /api/scenarios/{id}/adopt`, or `POST /api/scenarios` with `status: "adopted"`) logs its forecast. The What-If
Lab's **Adopt** button makes that call when the API is up, so adopting in the web app writes to the ledger. Page: `web/public/calibration.html`.

## What is built on Tiger

| # | Feature | Status | Where |
| --- | --- | --- | --- |
| 0 | Observed nights and simulated runs side by side; held-out calibration | built | `dcsim tsdb load`, `dcsim calibrate` |
| 1 | **Forecast ledger**: forecasts written before the night, append-only, scored automatically | built | `dcsim.forecasts`, `forecast_scorecard`, adopt hook |
| 2 | **Calibration page**: held-out ranges vs observed, ledger table | built | `web/public/calibration.html` |
| 3 | **Columnstore compression** with an automatic policy | built | `dcsim tsdb compress` |
| 4 | Calibration over time (weekly coverage aggregate + scheduled refresh) | idea | below |
| 5 | Weather and event flags as crowd-forecast inputs | idea | below |
| 6 | Tiger MCP server for live questions in the demo | idea | below |
| 7 | Vector search for similar nights (pgvector / pgvectorscale) | idea | below |

## Schema (`dcsim`)

| Table | Kind | Rows (current run) | What |
| --- | --- | --- | --- |
| `nights` | hypertable on `ts` (dinner open, Eastern) | 42 observed + ~5,000 simulated | one row per night or simulated night: students, cooked, waste lb (expired / close), stockout min, plus the **situation** copied onto every row (weekday, weather, event night, adopted plan, train/test split) and the sim inputs (crowd multiplier, production ratio) |
| `pan_windows` | hypertable | 15.7k observed + 157k replay | per dish per 30-min window: portions put out / eaten / left (MetaFoodX `consumption.csv` shape) |
| `waste_log` | hypertable | 8.9k | observed LeanPath-style weigh-ins |
| `planned_batches` | table | 418 | the plan each observed night was meant to follow |
| `calibrations` | table | 1 per run | fitted parameters, held-out scores, recovery check (JSONB) |
| `dish_nightly` | **continuous aggregate** over `pan_windows` | | per dish per night per run: cooked, eaten, left at close, empty windows. Bucketed in local time, because dinner crosses midnight UTC |
| `forecast_check` | view | | per observed night and scenario: simulated p10 / p50 / p90 and whether the observation fell inside |
| `forecasts` | table, **append-only** | 1 per forecast | the ledger: naive and calibrated p10 / p50 / p90 for students, waste, stockouts, cooked, plus samples (JSONB) |
| `forecast_scorecard` | view | | each ledger forecast next to the observed night: `issued_before_open`, `inside` / `outside` / `awaiting` |

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

### Result (engine 0.3.3, SYNTHETIC history)

| Held-out nights (n = 14) | naive inside 10–90% | calibrated inside 10–90% | naive bias / CRPS | calibrated bias / CRPS |
| --- | --- | --- | --- | --- |
| Pre-consumer waste (lb) | **1 / 14** | **10 / 14** | +63 / 69 | +29 / 41 |
| Stockout minutes | 6 / 14 | 8 / 14 | −11.0 / 22.5 | −0.1 / 18.6 |
| Students | 1 / 14 | 11 / 14 | −80 / 202 | −92 / 166 |

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
| pan_windows | 34.5 MB | 4.1 MB | 8.4× |
| nights | 1.3 MB | 0.6 MB | 2.2× |
| waste_log | 1.7 MB | 1.2 MB | 1.4× |
| total | 37.5 MB | 6.0 MB | **6.3×** |

On-disk total is 13–16 MB (higher right after a calibrate, until autovacuum catches up), because each chunk keeps fixed index and heap overhead and the newest week stays in
row-store until it is a day old. Calibrate still runs (33 s) with identical scores on compressed tables.

## Not built yet

**4. Calibration over time.** A continuous aggregate of `forecast_scorecard` by week (coverage, bias), refreshed by a
Tiger job (`add_job`) each night. It turns one score into a track record: "held for six weeks, drifted in week 7".
Needs a few weeks of ledger entries to be worth showing.

**5. Weather and event flags in the crowd forecast.** They're already on every row. Fit the crowd error by
situation on the training weeks and keep the change only if held-out CRPS improves. So far, clear held-out nights
were also about 150 students below forecast, so weather may not explain much.

**6. Tiger MCP server.** `tiger mcp start` runs Tiger's MCP server with the CLI's login. Add it to Claude Code and
judges' questions ("which dishes ran out on rainy Fridays?") can be answered live against these tables.
Keep `read_only` on for the demo.

**7. Similar-night search.** The service has `vector` (0.8.7) and `vectorscale` (0.9.0). Embed each night (menu,
situation, outcome) and offer "past nights most like tonight". It looks good but does less for credibility than 1–4.

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
