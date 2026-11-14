# Possible improvements

Status as of 2026-11-12, engine 0.3.3; status notes updated 2026-11-13 (engine 0.3.4, web UI built in `web/`) and again 2026-11-13 for engines 0.4.2, 0.5.0, 0.6.0 (simulation core: golden test, calibration targets, kitchen capacity, empty-pan waits, seats, staffing by hour, Container pans, series, realism items) and 0.6.1 (review fixes, ISSUES §6b). All Tier 0, Tier 1 and model-realism items are done; open: Tier 2 optimizer (2.1) and uncertainty sweep (2.2), 2.4 seat limits (need real seat counts), Tier 3, sensitivity analysis and real-data calibration. Markers: ✅ done · ◐ partly done · ✗ open (unmarked items are open). Companion to `ISSUES.md` (bugs and gaps), `UI_ISSUES.md` (web UI) and `LLM.md` (LLM layer).

Each item has:
- **Now:** what the code does today (checked against `engine/src/dcsim/`).
- **Gain:** why it is worth doing.
- **Effort:** S < half a day · M about a day · L several days.
- **Needs:** inputs or other items it depends on.

Anything that changes simulation logic needs an `ENGINE_VERSION` bump, then `make cache-clean` and `make demo-bundle`.

---

## Tier 0: do first (prerequisites)

These fix behavior that every later improvement would otherwise inherit.

| # | Improvement | Why first | Effort |
| --- | --- | --- | --- |
| 0.1 | ✅ **Done (0.4.1).** ~~**Close the hall properly** (ISSUES E2)~~: stop arrivals some minutes before 9:00 pm and send everyone still in the serving area to seating or the exit | 247 students still active after close distort waste, stockouts and the replay | S |
| 0.2 | ✅ **Done (0.4.1).** ~~**Stop students touring every line** (ISSUES E1)~~: cap stations tried per trip, and let students see lines from a distance and pick one | 33% of students walk away from 10+ lines; every other behavior builds on this | M |
| 0.3 | ✅ **Done 2026-11-13** (on GitHub). ~~**git init** + `.gitignore`~~ (`cache/`, `.env`, `.venv/`) | Tier 1 rewrites the core; you need a way back | S |
| 0.4 | ✅ **Done (0.5.0):** `engine/tests/test_golden.py` + `tests/golden/nov13.json` (Nov 13, seeds 1–10); rewrite on purpose with `DCSIM_UPDATE_GOLDEN=1`. ~~**Golden baseline test**~~: commit Nov 13 headline numbers; any engine change that moves them must be intentional | Rewrites like 1.1 should change behavior only where intended | S |

---

## Tier 1: rebuild the core properly

### 1.1 ✅ Done (0.6.0) · Pans as SimPy primitives (Container or Store of trays)
- **As built:** `model.PanStock` (a `simpy.Container`) holds the portions on the line; the lot list keeps hold times, and every get (immediate or after waiting) takes from the oldest lots in `PanStock._do_get`. Students waiting at an empty pan queue with `stock.get(1)`. Verified identical to 0.5.0 on the golden Nov 13 nights before the realism items went in; `test_night_ends_clean` checks that lots always add up to the Container level and no server or waiter is left over.
- **Now:** each `Pan` keeps a deque of `Lot`s by hand (oldest first). Each lot has its own expiry process, and the reorder check runs on every take, expiry and batch. Hold times are already enforced exactly (oldest lot first, expired portions counted as waste).
- **Gain:** less hand-written bookkeeping, and SimPy handles "wait until food arrives" (see 1.2).
- **Caution:** a plain `Container` only knows a level. It can't tell which portions are old, so expiry would break. Two options keep hold times:
  - **`Container` + lot list.** The Container holds the level and the waiting; the lot list stays for expiry. Smallest change.
  - **`FilterStore` of trays** (one item per lot, with timestamp and portions). Expiry is `store.get(lambda t: t.expires_at <= env.now)`; students take from the oldest tray. Cleanest model, but more events per night (benchmark: one night is 0.25 s serial today; 30 nights 1.4 s in parallel).
- **Recommendation:** Container + lot list. Same results, plus waiting, without a slowdown.
- **Effort:** M.
- **Sketch:**
  ```python
  pan.stock = simpy.Container(env, capacity=pan_capacity, init=opening)   # level + waiting
  pan.lots  = deque()                                                     # still needed for hold times
  yield pan.stock.put(batch_size); pan.lots.append(Lot(batch_size, env.now + hold_min))
  ```

### 1.2 ✅ Done (0.5.0) · Wait a little at an empty pan (reneging)
- **As built:** at the counter, if the dish is empty but a batch is already cooking, the student waits up to `empty_pan_wait_min` (dc.json 2 min, guess; × `favorite_patience_mult` for the first choice) for the refill, holding the server. New metric `empty_wait_min` (Nov 13: ~18 min of waiting per night).
- **Now:** a pan that is visibly empty is skipped at once; a pan that empties while the student is in line means they substitute immediately. Nobody ever waits for a batch that is 3 minutes away.
- **Gain:** more realistic, especially for favorites, and a new metric: **minutes spent waiting at empty pans**. That is what students actually complain about, and it is the honest cost of "smaller batches".
- **Effort:** S with 1.1 (or S without it, using a per-pan "refilled" event).
- **Needs:** patience at an empty pan, which is a new guess. Tie it to `favorite_patience_mult`, and only wait when a batch is already cooking.
- **Sketch:**
  ```python
  req = pan.stock.get(1)
  got = yield req | env.timeout(wait_patience)
  if req not in got:
      req.cancel()          # gave up: substitute the next dish
  ```

### 1.3 ✅ Done (0.5.0) · Cooks and equipment as Resources
- **As built:** `kitchen_defaults.json → resources` (placeholders): 12 dinner / 5 late-night cooks (4 until 0.6.1), equipment units (oven 14, steamer 8, kettle 4, flat-top 6, wok 5, fryer 3, tandoor 5), hands-on prep minutes per role, equipment by role / station / keyword. A batch needs a cook for its prep (inside `cook_min`), then one unit of equipment for the rest; without contention it still takes `cook_min`. New metrics `batch_wait_min`, `cook_busy_share` (~63% at 0.5.0; ~57% since 0.6.1 with 5 late-night cooks), `equipment_peak`; lever `kitchen_staff` (cooks, equipment). The `smaller_batches` preset now waits longer for the kitchen than the baseline. With 8 dinner cooks stockouts get ~6× worse.
- **Now:** **cooking capacity is unlimited.** `_cook` is just a timeout, so any number of batches can cook at once. Because of this, "cook smaller batches more often" (the recommended plan) costs nothing in the model.
- **Gain:** the biggest credibility improvement. Batches compete for cooks and equipment (wok, fryer, oven, grill top), so small batches queue up and the optimizer (2.1) finds a real trade-off.
- **Effort:** M.
- **Needs:** cooks per station or area, equipment per station, and **hands-on prep time** separate from **equipment time**. A cook is busy only during prep; the oven is busy for the whole cook. These are new inputs; ask the team or use R02/R07.
- **Sketch:**
  ```python
  with self.cooks.request() as c:            # hands-on prep
      yield c
      yield env.timeout(plan.prep_min)
  with self.equipment[plan.equipment].request() as e:
      yield e
      yield env.timeout(plan.cook_min)       # oven/wok time; cook is free again
  ```
- **New outputs:** cook utilization, minutes batches waited for equipment, and a staffing lever ("+1 cook at 6–8 pm").

### 1.4 ✅ Done (0.6.0) · Statistics monitor
- **As built:** `GET /api/results/{hash}?include=series` adds per-minute p10/p50/p90 bands (students inside, line length per station, portions on hand per dish), sampled from the recorder's exact step functions (`metrics.night_series`, `runner.series_summary`). Series aren't stored with cached nights (too big); the request re-runs the nights (~1–2 s). Typed as `Result.series` in the web app; no chart yet.
- **Now:** already better than a per-minute monitor. The `Recorder` logs every pan level and queue length **as a change** (exact step functions), so stockout minutes are exact, not sampled. The 3D trace and the arrival histogram come from it.
- **Gain:** what's missing is per-minute **series in results**, for charts like level over time, queue over time and the run-out timeline.
- **Effort:** S. Downsample the existing step functions to 1-minute series in `metrics.py`, aggregated as p10/p50/p90 across nights. Serve them via `GET /results/{hash}?include=series` (already planned in ENGINE.md §8).
- **Don't** add a sampling process to the simulation: it adds events and loses precision.

---

## Tier 2: features that impress judges

### 2.1 Optimizer (Optuna)
- **Now:** none. The "recommended plan" is the `smaller_batches` preset (ISSUES N6).
- **Gain:** "Optimize plan" button: lowest waste subject to *stockout minutes ≤ limit* and *wait p90 ≤ 8 min* (SPEC default).
- **Design points:**
  - **Search space:** about 50 dishes × 2 knobs (48 on Nov 13) is too many for a quick search. Optimize **per-station multipliers** on batch size and reorder point (about 20 parameters), plus per-dish knobs for the 5–10 dishes with the most waste.
  - **Common random numbers:** every trial runs the same seeds (1–10), so trials compare fairly. Use `svc.ensure_nights` and every trial lands in the cache.
  - **Snap** values to lever steps, so configs repeat and cache hits happen.
  - **Validate** the winner on unseen seeds (31–60) and report the paired win rate vs today, so the search doesn't overfit to its own 10 nights.
  - **Constraint handling:** penalty `waste + λ·max(0, stockout − limit)`, or Optuna's `constraints_func`.
  - **Budget:** about 200 trials × 10 nights ≈ 2,000 nights ≈ 1.5–2 min at today's speed. Run it as a background job with SSE progress (the jobs system already exists).
- **Honest framing:** this is black-box search (TPE), not learning from data. Say "searches 200 plans across 2,000 simulated nights".
- **Effort:** M.
- **Needs:** 1.3 first; otherwise it will just find "tiny batches, all the time".

### 2.2 Uncertainty sweep
- **Now:** ranges come only from night-to-night randomness (seeds). Inputs like popularity, crowd size and arrival peaks are fixed, so ranges look narrower than our knowledge justifies (ISSUES N7).
- **Gain:** "the plan beats today's plan in 82% of plausible worlds" is much stronger than one forecast.
- **Design:** don't make a new config per draw, because that defeats the cache. Add `uncertainty: {popularity_sd: 0.3, peak_shift_sd_min: 10, crowd_sd: 0.1}` to `ScenarioConfig`, and draw the parameters **per seed** from a dedicated RNG stream. Then:
  - one config + 300 seeds covers 300 plausible worlds;
  - baseline and plan see **the same worlds** (common random numbers), so the paired win rate stays valid;
  - caching works as today.
- **Multiprocessing:** already in place (`ProcessPoolExecutor`). 300 nights ≈ 15 s at today's speed (30 nights take 1.4 s); run it as a background job.
- **Extra:** a **tornado chart** showing which assumption moves waste the most, which tells the team which data request (R01–R08) matters most.
- **Effort:** M.

### 2.3 Food safety hold times
- **Now:** **already modeled.** Each batch is a lot with `expires_at = ready + hold_min`; expired portions are thrown out oldest first and counted as `expired` waste. The cook sheet and synthetic waste log separate `expired` from `close`.
- **Possible upgrades:**
  - A `FilterStore` of trays (see 1.1) only if the rewrite happens.
  - **Separate quality hold vs food-safety limit**: e.g. fries go soggy in 10 minutes but are safe for hours. Today `hold_min` mixes the two (ASSUMPTIONS A17).
  - **Temperature log events** for a HACCP-style record on the Waste page.
- **Effort:** S per upgrade.

### 2.4 ◐ Built, off by default (0.5.0) · Seating as a Resource
- **As built:** each zone is a `simpy.Resource(seats)`; usual zone, else the zone with most free seats, else wait standing; seat kept through seconds. `seat_limits` is **false** in dc.json: the 7:30 pm peak wants 530–680 seats but the layout's 404 are assumed, so seats stay unlimited until someone counts them. Every night reports `peak_seated` (and `seat_wait_min` when limits are on).
- **Now:** ◐ **partly done (0.3.4):** students sit in seating zones (main / back) reached through door routes, but zones have **no capacity** yet. (Was: one point with no capacity, `seat: "Z1"` stub.)
- **Gain:** on busy nights seating becomes a second bottleneck. It also enables groups sitting together (social persona) and zones for the 3D view.
- **Design:** `simpy.Resource(capacity=seats)` per zone, plus a choice of the nearest zone with space. Group arrivals need a group size per student.
- **Effort:** M.
- **Needs:** seat count per zone (floor plan or photo walk), and the nav graph for walking times (ISSUES N2).

---

## Tier 3: after the hackathon or for a resume

### 3.1 Interrupts (breakdowns, early closures)
- **Gain:** "the fryer breaks at 6:15 pm" or "Sushi closes at 8 pm". Uses SimPy's Interrupt (the Machine Shop pattern) on the equipment Resource from 1.3. A strong live demo.
- **Design:** scheduled events in the config (`incidents: [{at: "18:15", equipment: "fryer", minutes: 30}]`) so they stay repeatable and cacheable. Random breakdowns come later.
- **Effort:** M (after 1.3).

### 3.2 Multiple meals and dining commons
- **Gain:** lunch leftovers carry into dinner, and students choose between dining commons by line length. Answers "what if Worcester closes early and students go to Franklin?".
- **Effort:** L. Needs other dining commons' menus and layouts, and lunch arrivals (ISSUES N4).

### 3.3 Event log for the dashboard
- **Now:** **largely done.** The web app is meant to replay, not simulate:
  - `GET /api/traces/{hash}` gives one night's events for 3D replay.
  - The static bundle (`web/public/cache`) has 254 results, 13 traces and 156 narrations, and works with no backend.
  - The web Hall replays traces from the bundle, or from the API for any other scenario.
- **Upgrades:**
  - Smaller traces: msgpack or columnar arrays; the current 347 KB gzipped per night is fine for one night, heavy for split-screen.
  - Keyframes for scrubbing.
  - Optional Parquet export of all events for analysis.
- **Effort:** S–M.

### 3.4 Learn from MetaFoodX-style data
- **Gain:** fit dish popularity and arrival waves from `consumption.csv` automatically, so the model recalibrates whenever new data arrives (ENGINE.md §7.3 step 6).
- **Caution:** today's `consumption.csv` is **synthetic and generated by this engine** (ISSUES B1). Fitting to it only recovers our own assumptions. Build the fitting pipeline, but test it on synthetic data with **known hidden parameters** (the generator's `_ground_truth.json`, extended with true popularity). Only call it calibration on real exports.
- **Method:**
  - Popularity: Bradley-Terry or a choice model on served counts while every dish is available. Correct for stockouts: when a dish ran out, demand was censored.
  - Arrivals: fit wave peaks, widths and shares to entry counts.
- **Effort:** L.

---

## More improvements (not in the list above)

### A. Model realism
| Improvement | Gain | Effort |
| --- | --- | --- |
| ✅ **Done (0.5.0):** `Station.shifts`, lever `stations.<id>.shifts`, `staffing` output (servers needed per hour). **Staffing by hour** (servers change during the meal) | Real staffing levers ("+1 Grill server 6:30–8:00") | M |
| ✅ **Done (0.4.2, placeholder multipliers; needs R01).** **Per-date crowd** from `calendar.json` (weekend, events, exams, weather) (ISSUES E5) | Dates stop looking identical | S (needs R01/R08 for values) |
| ✅ **Done (0.6.0):** `overrides.json → dish_popularity` (Hot Pot, Pho, Tom Yum, Taiwan Pork 2×; ramen 1×; team). **Item-level popularity boosts** instead of station-wide ones (Noodle Bowl, ISSUES E6) | Matches the team: the line depends on the dish | S |
| ✅ **Done (0.6.0, placeholder: 2 readers, 4 s):** `swipe_wait_min`, `swipe_queue_max`. **Entrance swipe queue** (card readers as a Resource) | A real bottleneck at peak; shows in 3D | S |
| ✅ **Done (0.6.0, placeholder: 40% of social students, groups of 2–4):** same arrival time and seat zone; `group_students`. **Groups** (social students arrive and sit together) | Realistic waves and seating pressure | M |
| ✅ **Done (0.6.0, placeholder sizes):** `kitchen_defaults.json → pan_capacity` by role; the rest of a batch waits in the hot-hold cabinet; a 0.5-min pan swap shows the pan empty. **Pan size limits** (hotel-pan capacity per dish) | Batch sizes the kitchen can actually cook | S |
| ✅ **Done (0.6.0) as preset `forecast_reorder`:** reorder when stock won't cover 1.2 × the forecast for the next cook time + 5 min, plus safety stock 1.65·√forecast; re-checked every 5 min. Engine 0.6.1: Nov 13 waste −13%, stockouts 36 → 107 min; Nov 17 waste −6%, stockouts 66 → 57 min (a trade-off, ISSUES R7). **Forecast-based reordering** (start a batch from the expected rush, not only the current level) | Fewer stockouts at the 7:30 peak; a smarter preset | M |
| ✅ **Done (0.4.2, carving line; confirm with the team).** **Per-date station overrides** (Thanksgiving carving made to order, ISSUES E7) | Special nights modeled correctly | S |
| ✅ **Done (0.6.0):** survey allergy shares (2022–23, ~10%) drawn per student, dishes labeled with the allergen avoided; pescetarian 2% diet; `allergy_students`, `allergy_no_meal`. **Allergens and pescetarian diet rules** (ISSUES E8, N8) | Diet-gap flags cover more students | S |

### B. Calibration and validation
| Improvement | Gain | Effort |
| --- | --- | --- |
| ✅ **Done (0.5.0):** `data/worcester/calibration_targets.json` + `tests/test_calibration_targets.py` (all targets pass since 0.6.1; late run-outs was a known E3 failure on Nov 17 until then). **Calibration targets file**: the team's observations as tests (longest lines 30–40, rarely runs out late, biggest rush 7–8 pm) | Every engine change is checked against what the team sees | S |
| **Sensitivity analysis** (tornado or Sobol on the guessed inputs) | Ranks which data requests R01–R08 matter most | M (with 2.2) |
| **Approximate Bayesian computation** when real counts arrive: find input values that reproduce observed line lengths and waste | Replaces guesses with ranges backed by data | L |
| ◐ **Protocol built on synthetic data** (`make calibrate`, TIGER.md); waits for real data. **Holdout back-test** once real data exists: fit on weeks 1–4, test on 5–6 | Fixes the circular back-test (ISSUES B1) | M |

### C. Honest results
| Improvement | Gain | Effort |
| --- | --- | --- |
| **Minimum meaningful difference** in verdicts (ISSUES C1) | No "worse" for 0.1-minute changes | S |
| ✅ **Done:** the Lab shows waste, stockouts, missed first choice and wait with a verdict each; the Waste Record shows the stockout change next to the waste reduction. **Trade-off card**: always show waste *and* stockout *and* wait together (ISSUES B2) | Prevents a waste-only headline | S |
| **Data-source badges on every number** (measured / team-confirmed / sourced / guess / synthetic / AI estimate) | Judges can see exactly what is real | S |
| **Generated ASSUMPTIONS numbers** (ISSUES D1) | Docs can't go stale | S |

### D. Product and frontend (ISSUES N1, UI_ISSUES)
| Improvement | Gain | Effort |
| --- | --- | --- |
| **Printable cook sheet** (PDF per date: portions, batches, when to start the next one). **Partly done:** the Dashboard's production plan has Print (print styles) and CSV export | Something a kitchen manager can use tomorrow | S |
| **Split-screen 3D compare** (today vs plan, same night, synced) | The most convincing visual for judges | M |
| ~~**"Ask" box**~~ **Done 2026-11-13:** What-If Lab, settings shown before running | Easy entry for non-technical judges | — |
| ~~**Student card with narration on click**~~ **Done:** Hall student card; any student with the API | Makes numbers human | — |
| ~~**Waste Record page from the back-test**~~ **Done:** labeled synthetic, with per-dish "with plan" and station filters | Shows the planning loop end to end | — |
| **Animated 3D Hall** (React Three Fiber) to replace the top-down 2D replay (UI_ISSUES U2) | The designed centerpiece | L |
| **Level of detail for 3,000 figures** (instanced meshes, impostors) | Smooth 3D on a laptop | M |
| **Quick uncertainty check** in the Lab: re-run a scenario at crowd ×0.9 and ×1.1 through `/api/simulate` until the full sweep (2.2) exists | Shows whether a plan holds up | S |
| **Link the calibration page** (`web/public/calibration.html`) from the Waste Record | One place for "how good are the forecasts" | S |

### E. Backend and infra
| Item | Gain | Effort |
| --- | --- | --- |
| **Auth token for write routes + rate limit on LLM routes** (ISSUES A1) | Safe to host publicly | S |
| **Expire jobs; smaller trace cache** (ISSUES A2) | Stable memory on a long demo day | S |
| **CI** (tests + quick bundle on every push) | Catches regressions | S (after git) |
| **Docker + deploy** (Render or Fly.io with a disk for SQLite; frontend on Vercel) | A shareable demo URL | S–M |
| **`POST /warm`**: precompute the configs the UI might ask for next | Instant results while sliders move | S |
| **Daily menu refresh** (scraper → `dcsim prepare`) | Always tomorrow's real menu | M |

### F. LLM layer (details in `LLM.md` §5)
- Persona replacement (paused; your alternative arrives tomorrow).
- ✅ ~~In-app rate-limit queue for Groq's 1,000 output tokens per minute.~~ Done: live load gate + backup model (LLM.md §5.4); Gemini is the default.
- ✅ ~~Wider narration checks (times, line lengths).~~ Done (`narrate-v3`).
- Pre-warm narration for each new scenario's median night.
- Prompts in separate versioned files.
- Survey calibration (ENGINE.md §7.3).

---

## Inputs needed from the team

| For | Question |
| --- | --- |
| 0.1 | Is 9:00 pm last entry or doors closed? Are students already in line served after 9? |
| 1.2 | How long would you wait at an empty pan if you knew more was coming? |
| 1.3 | How many cooks work dinner, and where? Which equipment does each hot station use (wok, fryer, oven, grill top), and how many of each? |
| 2.4 | Roughly how many seats, and in which areas? |
| A | Do staffing levels change during dinner? Which Noodle Bowl dishes draw a crowd? Is Thanksgiving carving made to order? |

---

## Suggested order

1. **Tier 0:** 0.1–0.3 done; **0.4 golden baseline test** left.
2. **1.3 cooks and equipment.** Highest credibility gain; it makes the recommendation honest.
3. **1.1 + 1.2:** pans with waiting, and the "waiting at empty pans" metric.
4. **C items:** honest verdicts and trade-off card. Cheap.
5. **2.1 optimizer**, validated on unseen seeds.
6. **2.2 uncertainty sweep** + tornado chart.
7. **D items** for the demo: animated 3D Hall, split-screen 3D, cook sheet PDF (ask box and student card are done).
8. Tier 3 as time allows. Do 3.1 interrupts first: cheap after 1.3 and great on stage.
