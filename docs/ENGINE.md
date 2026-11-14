# DC What-If — Simulation Core Design

Companion to `SPEC.md` §7. This document covers how the engine simulates a scenario, how it records what happened, how results are cached and stored, and how responses get back to the UI fast.

---

## 1. The big picture

```
 UI request (levers)
      │
      ▼
 ┌────────────┐   ┌──────────────┐   hit    ┌──────────────────┐
 │ Normalize  │──▶│ Hash config  │────────▶ │ Return cached    │  ~10–50 ms
 │ + validate │   │ (sha256)     │          │ result           │
 └────────────┘   └──────┬───────┘          └──────────────────┘
                         │ miss
                         ▼
                 ┌──────────────────┐   per-night cache hit → reuse
                 │ Job scheduler    │──────────────────────────────┐
                 │ (dedupe in-flight)│                              │
                 └──────┬───────────┘                              │
                        ▼                                          │
          ┌──────────────────────────────┐                         │
          │ Warm process pool (N cores)  │                         │
          │  night(seed=1) … night(30)   │  each = one SimPy run   │
          └──────┬───────────────────────┘                         │
                 │ NightMetrics streamed as they finish            │
                 ▼                                                 ▼
          ┌──────────────┐   ┌───────────────┐   ┌──────────────────────┐
          │ Aggregator   │──▶│ Result store  │──▶│ UI: provisional →    │
          │ (percentiles,│   │ memory + disk │   │ final (SSE stream)   │
          │ paired diff) │   └───────────────┘   └──────────────────────┘
          └──────────────┘
```

**Core principle:** `run(config, seed) → NightResult` is a **pure, deterministic function**. The same config and seed always give the same answer. Caching, parallelism and comparing scenarios all rely on this.

**The LLM sits outside the loop.** It prepares inputs *before* a run (personas, new-dish priors) and narrates *after* a run (on click). SimPy makes every simulated decision by sampling from the persona tables with the seeded random generator. This keeps the purity rule intact (§7).

### Engine choice
The whole backend is **Python only**. **SimPy** is chosen for how fast it is to build, not for raw speed. A dinner is roughly 50k events, about 0.1–0.3 s per night (to verify with `engine/bench.py`), and caching covers repeat requests. All callers go through `run(config, seed)`, so the engine can be swapped. Upgrade path if the benchmark is slow: profile (`py-spy`) → custom `heapq` event loop with the same interface (~2–4×) → Rust core via PyO3 (~30–100×, post-hackathon, only if the optimizer needs thousands of nights). Considered and rejected for now: salabim (slower, duplicates our 3D), Ciw (too rigid), Mesa (ticks every step), Julia (toolchain friction), an in-browser TypeScript engine (would need a full rewrite; revisit post-hackathon for a server-free version), and analytical averages (no individual students, so no 3D traces).

---

## 2. Inputs: the scenario config

A scenario is one immutable `ScenarioConfig` (a Pydantic model). It is fully resolved before simulating: no "defaults filled in later".

```python
class ScenarioConfig(BaseModel):
    dc: str = "worcester"
    date: date
    meal: Literal["dinner"]                 # full-detail meal in the MVP
    open_min: int; close_min: int            # minutes since midnight
    menu: list[DishPlan]                     # dish, station, diet tags, vessel, popularity (mean, sd)
    kitchen: dict[str, BatchPlan]            # starting_pans, batch_size, reorder_point, cook_min, hold_min, max_batches, last_batch_min
    stations: dict[str, StationPlan]         # servers per hour, self_serve, service-time params
    crowd: CrowdPlan                         # total multiplier, event preset, wave shifts
    portions: dict[str, PortionPlan]
    order_caps: dict[str, int] | None        # total portions available per dish
    population: str                          # fingerprint of personas_calibrated.json (§7)
    n_nights: int = 30
    engine_version: str                      # set by the server, not the user
    data_version: str                        # hash of the input data files used
```

**Normalization** (before hashing): sort dishes and stations by id, round floats to fixed precision (e.g. multipliers to 0.01), snap slider values to their step size, drop fields that equal the defaults. Two configs that mean the same thing then hash to the same key.

```python
config_hash = sha256(canonical_json(config_without_n_nights)).hexdigest()[:16]
```

`n_nights` is left out of the hash on purpose: results are stored **per night**, so asking for 60 nights after 30 only runs nights 31–60 (§5.3).

---

## 3. How one night is simulated

One night is one `simpy.Environment`, with time in minutes from open to close (plus a short drain period).

### 3.1 Before the clock starts: the pre-drawn population
For each seed, all **scenario-independent** randomness is drawn up front with NumPy, using separate streams for each purpose:

```python
ss = np.random.SeedSequence([seed, DC_ID, DATE_ORDINAL])
arrival_rng, people_rng, taste_rng, service_rng, dwell_rng = map(np.random.default_rng, ss.spawn(5))
```

- **Arrival times**: a non-homogeneous Poisson process λ(t) (the sum of Gaussian waves after class-block end times, §7.1 of the spec), sampled by *thinning*. Crowd levers scale and shift λ(t). Arrivals are thinned from a fixed pool of candidate arrivals, so +10% crowd keeps the same students and adds new ones; it does not reshuffle everyone.
- **Persona and attributes**: each student is assigned a persona using the calibrated shares (§7.3). They then draw their own diet, patience (longest line they'll join), plate size (2–4 items), seconds propensity and dwell time from that persona's distributions.
- **Taste noise**: for each student × dish, a Gumbel draw seeded by `hash(seed, student_id, dish_id)`. Each dish has its own draw, so **adding a new dish doesn't change anyone's feelings about the old dishes**. That makes demand-shift results ("curry takes 60% from meatloaf") clean.

Each student ranks the eligible dishes by `utility = log(affinity[persona][dish] × correction[dish]) + gumbel_noise`, where the affinities come from the LLM persona set and the corrections come from the survey adjustment (§7.3). Ranking by log-popularity plus Gumbel noise is mathematically the logit choice model in the spec, P(i) = pᵢ / Σpⱼ. It also gives a full preference order for free: substitution means "take the next one on my list."

These pre-draws are the **common random numbers**. Baseline and scenario see the *same students arriving at the same times with the same tastes*, so any difference between them comes from the decision, not from luck.

### 3.2 SimPy processes and resources

| Thing | SimPy object | Behavior |
| --- | --- | --- |
| Student | process (one per student) | arrive → plate-building loop → seat → eat → maybe seconds → dish return → exit |
| Station line | `simpy.Resource(capacity=servers)` | FIFO queue. Service time is lognormal per item. Self-serve stations: capacity = serving spots. Servers per hour change capacity on a schedule. |
| Pan (per dish) | custom `Pan` object | holds **lots** `[(portions, ready_at, expires_at)]`, served first-in first-out |
| Kitchen (per dish) | process, woken by pan events | starts a batch when `ready + cooking ≤ reorder_point`, if `batches < max` and `now + cook_min ≤ last_batch_min` |
| Oven / cook capacity (optional) | `simpy.Resource` | limits how many batches cook at once |
| Seating | per-zone free-seat sets | nearest free seat in the preferred zone. If the hall is full, the student waits or takes food to go. |
| Walking | `env.timeout(walk_time[a][b])` | from a **precomputed all-pairs walking-time matrix** over the layout's nav graph |

**Student process (simplified):**
```python
def student(env, s, hall):
    yield env.timeout(walk(ENTRANCE, first_station))
    plate = []
    for dish in s.preferences:                       # ordered by utility
        if len(plate) >= s.plate_size: break
        st = hall.station_of[dish]
        if hall.queue_len(st) > s.patience:          # balk
            rec.log(BALK, s, st); continue
        with st.line.request() as req:
            yield req
            if hall.pan[dish].take(1):
                yield env.timeout(service_time(s, st))
                plate.append(dish); rec.log(SERVED, s, st, dish)
            else:
                rec.log(EMPTY_PAN, s, st, dish)      # move on to the next preference
    if not any(is_hot(d) for d in plate): rec.log(MISSED_MEAL, s)
    yield from sit_eat_maybe_seconds(env, s, hall)
    yield env.timeout(walk(seat, DISH_RETURN)); rec.log(EXIT, s)
```

**As built (engine 0.4.1): deciding from a distance.** The sketch above walks to each station and balks there; the engine no longer does. `model.py` `_choose` looks at every dish the student would eat (best first) from where they stand: visibly empty pans and lines over their patience (× `favorite_patience_mult` for the favorite) are skipped without walking, the first acceptable station wins, and the student then walks over and **stays in that line**. With an empty plate and every line too long they join the shortest; once they have something they sit. Seconds and the favorite retry are decided from the seat the same way. `balk` now means "saw that line and chose not to join it".

**Service periods (0.4.1).** Each dish has a `period`: `dinner` (`open_min`..`close_min`, 4:30–9:00 pm) or `late_night` (`close_min`..`late_close_min`, 9:00 pm–12:00 am; ids `late.<station>.<name>`, from `menus/<date>_late_night.json`). Pans open with starting stock when their period starts and close at its end. At 9:00 dinner dishes take no new students, and lines already formed are served from what is left (`LAST_CALL_MAX_MIN` 30). At midnight serving stops; students still in line get a `closed` event, and seated students stay until they are done. Late night has its own crowd (`late_crowd`, its own random stream, so dinner arrivals don't move) and snack-sized plates (`late_plate_size`). A student ranks only the dishes of the period they arrive in. The planner sizes each dish over its own period.

**Late-night taper (0.4.2).** A `BatchPlan` may carry `taper_from_min`, `taper_batch_size` and `taper_reorder_point`: from that time the kitchen cooks the smaller batch with the lower reorder point (one re-check is scheduled at the taper time). The planner sets them for late-night dishes (`kitchen_defaults.json → planner_by_period.late_night`): the taper starts once fewer than `taper_fill` × batch portions are still to be eaten in the pilot runs.

**Crowd by date (0.4.2).** `crowd_by_date.json` scales the dinner and late-night crowds by weekday and date events (placeholders, E5). `overrides.json → station_by_date` moves a dish to another station on one date (Nov 18 carving line, E7).

**Simulation core (0.5.0).**
- **Kitchen capacity:** `ScenarioConfig.kitchen_resources` (from `kitchen_defaults.json → resources`). `_cook` takes a cook from the period's pool for `prep_min` (role), then one unit of the dish's equipment (role / station / name keyword) for `cook_min − prep_min`. Without contention a batch takes `cook_min`, as before. Records `batch_wait_min`, cook busy minutes and equipment peaks. `kitchen_resources=None` (planner pilot runs) is the old unlimited kitchen.
- **Empty-pan wait:** in `_visit`, a student whose dish is empty while a batch cooks waits on the pan's refill event or `empty_pan_wait_min` (× favorite multiplier), whichever comes first.
- **Seats:** one `simpy.Resource` per seating zone; capacity = seats when `seat_limits`, else unlimited but counted (`peak_seated`). SIT events carry the zone actually used.
- **Staffing by hour:** a station with `shifts` uses a `PriorityResource` sized to its largest shift; `_staffing` holds "off duty" tokens (priority 0, ahead of students) for servers not on shift. A token waits for the current student, so a server leaves after serving them. Visible line lengths ignore tokens. `staffing` in results = servers needed per station and hour for ≤ 85% busy.
- **Guards:** `tests/test_golden.py` (Nov 13 pinned) and `tests/test_calibration_targets.py` (team observations).

**Engine 0.6.0.**
- **Pans as Containers:** `PanStock(simpy.Container)` holds portions on the line; `_do_get` takes from the oldest lots, so hold times stay exact. Pan size: at most `pan_capacity[role]` on the line, the rest in the hot-hold `cabinet`; when the line pan runs out, a swap (`pan_swap_min`) refills it and the pan looks empty meanwhile. `pan.level` = line + cabinet (what stockouts and series use); `pan.on_line` is what students see.
- **Series:** `metrics.night_series` samples the exact step functions per minute; `runner.series_summary` → `?include=series`.
- **Population:** allergies (`ScenarioConfig.allergies`, survey shares, `Stream.ALLERGY`) remove labeled dishes from a student's list; pescetarian diet (data.py tags meat-free fish/seafood dishes `pescetarian`); groups (`ScenarioConfig.groups`, `Stream.GROUP`) share the first member's arrival time and seat zone.
- **Entrance:** `swipe_readers` card readers (`simpy.Resource`) before `enter`.
- **Stockout vs empty pan:** stockout-minutes and series use `pan.level` (line + cabinet), so a pan swap is not a stockout; students see `on_line`, so a swap does count in `empty_seen` (ISSUES R5).
- **0.6.1 tuning:** dinner plans stop 15 min before close (`planner_by_period.dinner`, E3); late-night taper batches are 50% and there are 5 late-night cooks (ISSUES R2).
- **Kitchen:** preset `forecast_reorder` sets `BatchPlan.forecast_reorder`: threshold = 1.2 × expected portions over the next `cook_min + 5` min (planner `expected_demand` × the period's arrival curve) + 1.65·√that; a 5-min re-check cooks ahead of a rush.

**Kitchen is event-driven, not polled.** `Pan.take()` checks the reorder condition and, if met, triggers the kitchen process. When a lot is added, one expiry event is scheduled at `expires_at` that removes whatever is left of that lot and logs it as *expired*. At close, everything left in pans is logged as *leftover*. Nothing ticks every minute, which keeps the event count low.

### 3.3 The rest of the day
Breakfast and lunch at the hourly level don't need SimPy. Expected arrivals per 10 minutes are λ(t) integrated over each window, computed with vectorized NumPy in about a millisecond. The range comes from sampled crowd and wave parameters. Portion estimates for those meals are `arrivals × items_per_student × popularity_share`.

---

## 4. How data is recorded

### 4.1 During the run: an append-only recorder
SimPy is single-threaded Python, so recording has to be cheap. The `Recorder` appends **integer-coded tuples** to plain Python lists. No dicts, no pandas, no I/O inside the run.

| Log | Columns | Written when |
| --- | --- | --- |
| `events` | t, student_id, event_code, station_id, dish_id, vessel_code | enter, queue_join, balk, served, empty_pan, sit, seconds, retry, closed, dish_return, exit, missed_meal |
| `pan_changes` | t, dish_id, level, cause_code | every take, batch ready, expiry, close |
| `queue_changes` | t, station_id, length | every join/leave |
| `kitchen` | t, dish_id, batch_no, event_code | batch start, batch ready |

These logs record **changes**, not samples, so they form exact step functions. "Minutes out of stock" is the total time a pan's level is 0, computed exactly with no sampling error.

### 4.2 After the run: reduce to `NightMetrics`
At the end of each night the logs become NumPy arrays and are reduced to a small summary (~2–5 KB):

```python
NightMetrics(
  seed, waste_portions_total, waste_by_dish{expired, leftover},
  stockout_minutes_by_dish, runout_time_by_dish,
  missed_first_choice_rate,  # never got their first choice during the whole visit, return trips included
  missed_meal_count,         # got nothing, or first choice was hot and left with no hot food
  wait_p50, wait_p90, max_queue_by_station,
  served_by_dish, demand_by_dish (first choices),
  arrivals_per_10min, queue_len_per_5min_by_station, pan_level_per_5min_by_dish,  # downsampled series for charts
  flags[]   # e.g. no vegetarian hot entrée after 19:30
)
```

The raw logs are then **discarded** unless this night was picked for the trace (§4.4). This keeps memory flat however many nights run.

### 4.3 Across nights: aggregation
- Each headline metric: **median, p10, p90** across nights.
- **Paired comparison vs. baseline:** because seeds are shared, compute `diffᵢ = scenarioᵢ − baselineᵢ` for each seed i. Report the median diff, its range, and the **win rate** (share of nights where the scenario is better). "Better" requires win rate ≥ 60%; otherwise the UI says *inconclusive*.
- **Noise band (P1):** run the baseline against itself on a second seed set and show the spread as a grey band ("changes smaller than this are noise").
- Run-out time ranges per dish: the p10–p90 of the run-out minute across nights where the dish ran out, plus how often it ran out ("runs out in 24/30 nights, 6:40–7:10 pm").

### 4.4 The 3D trace
After aggregation, the engine picks the **median night** (the seed closest to median waste) and **re-runs only that seed with `trace=True`**. That costs one extra night, about 0.2 s. The trace (per-student event list plus pan events, format in spec §8.3) is serialized as **MessagePack + gzip** (~200–600 KB for a dinner). Re-running is cheaper than keeping the raw logs of all 30 nights around.

---

## 5. Caching and storage

### 5.1 Three cache tiers
| Tier | What | Lifetime | Lookup time |
| --- | --- | --- | --- |
| **L1: in-process LRU** | last ~200 aggregated results + ~20 traces | until API restart | < 1 ms |
| **L2: on-disk store** | every result ever computed (SQLite + files) | persistent | 5–30 ms |
| **L3: static demo bundle** | presets + precomputed lever grid exported as JSON into `web/public/cache/` | committed to the repo | served by the frontend, no API needed |

Lookup order: L1 → L2 → (L3 if offline) → compute.

### 5.2 On-disk layout
```
cache/
  results.sqlite
  series/<config_hash>.parquet           # downsampled time series per night (charts)
  traces/<config_hash>_s<seed>.msgpack.gz
```

`results.sqlite` tables:

```sql
CREATE TABLE configs (
  config_hash   TEXT PRIMARY KEY,
  engine_version TEXT, data_version TEXT,
  config_json   TEXT,               -- normalized config, for audit and "show settings"
  created_at    TEXT, last_hit_at TEXT, hits INTEGER
);
CREATE TABLE nights (                -- one row per (config, seed): enables incremental runs
  config_hash TEXT, seed INTEGER, metrics_json TEXT,
  PRIMARY KEY (config_hash, seed)
);
CREATE TABLE aggregates (            -- materialized summary for a given night count
  config_hash TEXT, n_nights INTEGER, baseline_hash TEXT,
  summary_json TEXT,                 -- percentiles, win rate, flags, cook sheet
  PRIMARY KEY (config_hash, n_nights, baseline_hash)
);
CREATE TABLE scenarios (             -- user-facing: saved / adopted what-ifs
  scenario_id TEXT PRIMARY KEY, name TEXT, config_hash TEXT,
  status TEXT CHECK (status IN ('saved','adopted')), for_date TEXT, created_at TEXT
);
CREATE TABLE backtests (             -- Waste page: plan re-simulated on each recorded night
  plan_hash TEXT, record_date TEXT, recorded_waste REAL, simulated_json TEXT,
  PRIMARY KEY (plan_hash, record_date)
);
CREATE TABLE llm_cache (             -- every LLM response, so the same request never hits the network twice
  request_hash TEXT PRIMARY KEY,     -- sha256(prompt_version + model + normalized inputs)
  kind TEXT,                         -- persona | dish_prior | narration | ask
  model TEXT, prompt_version TEXT,
  response_json TEXT, created_at TEXT
);
```

Why SQLite plus files: no setup, a single portable file, safe concurrent reads, and easy to ship or reset. Parquet keeps time series compact and readable by pandas. If this ever runs for real across many DCs, the same schema moves to Postgres plus object storage.

### 5.3 Incremental reuse
Results are stored per `(config_hash, seed)`, so:
- 30 → 60 nights: run seeds 31–60 only.
- Baseline is computed once per date and reused by every comparison that day.
- Back-test for a plan: each `(plan_hash, record_date)` is computed once.

### 5.4 Invalidation
The hash includes `engine_version` (bumped manually when model logic changes) and `data_version` (sha256 of the input files the config uses: menu snapshot, dish params, popularity, **calibrated personas**, layout, class schedule). If either changes, old entries stop matching. Nothing is deleted by hand, and stale results can never be served. A `make cache-clean` target deletes rows whose versions are no longer current.

### 5.5 In-flight dedupe
If two requests ask for the same `config_hash` while it is running (e.g. a user drags a slider back and forth), the second request **joins the existing job** instead of starting a new one. The job registry is a dict `config_hash → asyncio.Future`.

---

## 6. Making responses feel immediate

The goal is that the UI never sits on a spinner. Most interactions should be **cache hits (< 50 ms)**. A true cold run should show **provisional numbers in < 1 s** and final numbers in **1–3 s**.

### 6.1 Make most requests cache hits
1. **Precompute presets** for today's date on API startup (and in a nightly job): Baseline plus the 6 presets × 30 nights.
2. **Snap sliders to steps** (pans: integers; reorder point: steps of 5 portions; crowd: steps of 5%; servers: integers). This makes the lever space finite and cacheable.
3. **Precompute the likely grid.** A background worker fills the cache with one-lever-at-a-time variations around the baseline: each dish's starting pans ±2, reorder point ±2 steps, crowd ±25%, servers ±1 per station. That's a few hundred configs, which a laptop finishes in minutes while idle. Most single-slider moves then hit the cache.
4. **Speculative execution:** when the user focuses a slider, the UI asks the API to warm up the neighboring values (low priority, cancellable).

### 6.2 Make cold runs fast
| Technique | Effect |
| --- | --- |
| **Warm `ProcessPoolExecutor`** started at boot, with layout, walking matrix and data already loaded in each worker | no per-request process spawn or data loading |
| **Parallel nights** across cores | 30 nights on 8 cores ≈ 4 rounds |
| **Event-driven kitchen and recorders** (no per-minute ticks) | a dinner is roughly 30–60k events instead of hundreds of thousands |
| **Precomputed walking-time matrix** (Dijkstra once per layout, cached to disk) | walking is one lookup |
| **Vectorized pre-draws** (all randomness drawn in NumPy before the run) | no RNG calls in the hot loop |
| **Integer codes + `__slots__` classes, no pandas inside the loop** | lower Python overhead |
| **Trace off by default**, one extra seed for the trace afterwards | 30 nights don't pay for trace building |
| **Only send what the page needs**: summary first (~10 KB), series and trace fetched lazily | small responses |

**Estimate to verify on day 1 with a benchmark** (`engine/bench.py`): a Worcester dinner with ~2,000 students should take about 0.1–0.3 s per night in pure Python, so 30 nights on 8 cores ≈ 0.5–1.5 s. If it's slower, profile with `py-spy` before optimizing anything else.

### 6.3 Stream progressive results
`POST /simulate` returns immediately with either the cached result or a `job_id`. The UI then subscribes to `GET /jobs/{id}/stream` (Server-Sent Events):

1. **First 8 nights done (~0.3–0.5 s):** provisional result; the UI shows numbers with a "refining…" tag.
2. **Every further batch of 8:** updated ranges.
3. **30 done:** final result and win rate. The trace is fetched in the background for "Watch in 3D".

Seeds run in a fixed order (1, 2, 3, …), so provisional results are reproducible too.

### 6.4 Optimizer and back-test (heavier jobs)
- **Optimizer:** successive halving. Screen many candidate plans on 5 nights, keep the best 25% for 15 nights, then confirm the top few on 30+ nights. Every evaluated night lands in the per-night cache, so the confirmation step reuses earlier work. It runs as a background job with progress shown in the UI.
- **Back-test:** one job per plan across all recorded nights, computed once and cached in `backtests`. Precompute it for the recommended plan so the Waste page loads instantly.
  - **As built:** `dcsim backtest` (not an API job) writes `data/synthetic/worcester/backtest.json`, which the bundle copies to `waste/backtest.json`. Besides the headline and per-night rows it has, since 2026-11-13, `dishes` (per dish: recorded vs. plan lb per night served, change %, $ saved per week) and `by_station` on every back-tested night, for the Waste Record's top-dishes table and station filters. The optimizer is not built; the plan is the `smaller_batches` preset.

### 6.5 Optional (P2): instant preview from a surrogate model
Once the cache holds a few hundred configs, fit a small regression model (e.g. gradient boosting) from lever values to headline metrics. While a slider is being dragged, show the surrogate's estimate instantly (labeled "estimate"), then replace it with the real simulation result. Do this only after everything else works.

### 6.6 Demo safety
`make demo-bundle` exports every preset, the lever grid around them, the median-night traces and the back-test as static JSON into `web/public/cache/` with a `manifest.json` (`config_hash → file`). The frontend checks the manifest first, so the whole demo works with **no API and no network**.

---

## 7. LLM layer and persona calibration

### 7.1 Where the LLM is allowed
| When | Job | Calls | Affects numbers? |
| --- | --- | --- | --- |
| Before (offline) | Generate the persona set | ~20, **once per semester**, then frozen | Yes, via the survey adjustment |
| Before (per menu) | Persona affinities for **new** dishes only | 1 per new dish, cached | Yes, labeled "AI estimate" |
| Before (stretch) | `/ask`: plain English → lever settings | 1 per question, cached | Indirectly (the manager sees and can edit the settings) |
| During | **None** | 0 | — |
| After (on click) | Narrate one student's recorded night | 1 per click, cached | No |
| After (stretch) | Explain why a scenario won or lost | 1 per comparison, cached | No |

LLM-per-decision inside SimPy was rejected: ~10–20k calls per night, not repeatable, can't be calibrated.

### 7.2 Persona generation
- **Input:** the menu categories, what we know about the UMass population (residential areas, diet mix guesses), and the persona schema.
- **Output** (Pydantic, validated and kept within limits): `id, label, description, proposed_share, diet, patience (dist), plate_size (dist), seconds_prob, dwell (dist), arrival_bias (earlier/later), category_affinity{}, dish_affinity{}`.
- **Limits:** patience 0–40 students, seconds 0–0.6, affinities 0.1–10. Invalid output → re-ask once → otherwise drop that persona.
- **Frozen:** a human reviews `personas_raw.json` (removes stereotypes, checks variety) and commits it. Regenerating is a deliberate action, never a side effect.

### 7.3 Survey adjustment ("LLM proposes, survey decides")
Survey (≈3 min, anonymous, 50–100 responses): diet, residential area, longest line you'd join (0 / 5 / 10 / 20+), how often you get seconds, usual dinner time (MWF and TuTh), 8–12 pairwise "which would you pick?" questions from this week's menu.

1. **Persona shares:** find the shares closest to the LLM's proposed shares (minimum cross-entropy / raking) that exactly match the survey totals for diet, patience, seconds and dinner time.
2. **Dish popularity:** the persona mixture implies a win rate for every dish pair. Fit **one correction factor per dish** so the implied win rates match the survey's pairwise answers (Bradley-Terry likelihood with an L2 penalty on log-corrections, so they stay near 1.0). The LLM keeps the differences between personas; the survey sets each dish's overall level.
3. **Held-out check:** keep 20% of pairs aside and compare LLM-only, survey-only and LLM + survey. The engine uses the best one and writes the result to `calibration_report.json`. If LLM + survey doesn't win, use survey-only and say so.
4. **Ranges:** bootstrap respondents (200×) to get ranges for shares and corrections. These feed the uncertainty sweep, so small-survey uncertainty shows up in the results' ranges.
5. **New dishes:** the LLM gives persona affinities, the correction factor is the average of the "similar to" dishes, and the range is widened.
6. **Later:** MetaFoodX consumption replaces the survey pairs as the target in step 2, with no engine change.

Output: `data/personas_calibrated.json`. Its fingerprint is `ScenarioConfig.population` and part of `data_version`, so a re-adjustment invalidates old results automatically.

**Fallback until the survey arrives:** the frozen LLM persona set with hand-checked shares, labeled "uncalibrated".

### 7.4 Narration on click
1. The click returns the student's **event timeline immediately** from the trace, with no LLM.
2. `GET /narrate/{config_hash}/{seed}/{student_id}` streams 2–3 first-person sentences. The prompt contains **only** the persona description plus that student's recorded events (times, dishes, line lengths, pan states).
3. **Check:** every dish or station named in the output must appear in the event list; otherwise use the template narration.
4. **Cache key:** (config_hash, seed, student_id, prompt_version) in `llm_cache`.
5. **Identity:** "Student #412 · vegetarian first-year". No made-up real names.
6. **Demo:** pre-warm the 10–15 most interesting students in the demo night (missed meal, three trips, switched because of the vegetarian gap).

### 7.5 Repeatability and safety
- Temperature 0 or low; every response cached; structured output only.
- **Mock provider:** fixed personas and template narration, used in tests, CI and the offline demo. `LLM_PROVIDER=groq|mock`.
- The UI labels where each number comes from: "Popularity: survey", "AI estimate (wider range)", "uncalibrated".

### 7.6 As built (engine 0.3.4, `engine/src/dcsim/ai/`)
> Current status, configuration, the persona pause and the to-do list live in [`LLM.md`](LLM.md). LLM persona generation is **paused**; the engine uses the hand-written `personas.json`.

| Module | Job | Check | Fallback |
| --- | --- | --- | --- |
| `llm.py` | One client: structured JSON (`beta.messages.parse` + Pydantic), streamed text, `llm_cache` table, one re-ask with the exact problems | — | `mock()` answer, labeled `source: "fallback"`, not cached |
| `narrate.py` | Card (timeline) instantly, then narration **sentence by sentence**; `highlights()` picks students worth clicking (missed meal, came back, vegetarian gap, switched, empty pan, three lines) | Each sentence is checked before it is sent: no dish or station outside the student's events; ≤600 chars | Template narration (also what the mock returns) |
| `personas.py` | `dcsim personas generate` → `personas_raw.json`; `dcsim personas adopt` → `personas.json` after review; 2 per call, the first three batches each must include a vegetarian, vegan and halal persona | Limits per persona: patience 0–40, seconds 0–0.9, dwell 5–90, affinities 0.1–10, probabilities sum to 1, keys exist | None: generation fails loudly, never writes the hand set as if the model proposed it |
| `dish_prior.py` | Per-persona affinity for a new dish; level borrowed from "similar to" dishes, range ×0.6–1.5 | All eligible personas, 0.1–10 | Similar-dish average |
| `ask.py` | Plain English → `ScenarioRequest` + readable settings list + what it couldn't express; nothing runs. With Gemini, **function calling** (13 lever functions, 2026-11-14), else JSON schema | The request must build a valid config; bad calls re-asked once with their errors | JSON schema, then keyword parser |
| `agent.py` (2026-11-14) | **Ask Sam**: goals planned by Gemini function calling over `run_simulation` / `finish`, the decision setting functions and (via `tiger_mcp.py`) Tiger lookups; what-ifs through `ask.py`; history questions from Tiger | `check_goal` is code; ≤ 5 simulations; re-checked on unseen seeds; Tiger SQL guarded to one read-only SELECT | Rule-based search; "needs Gemini" for questions |
| `explain.py` | 3 sentences on why a scenario won or lost, from the paired comparison | Every number must be in the facts; no "saved / measured / will" | Template from the verdicts |

- **Provider (2026-11-13):** Google Gemini (`google-genai` SDK, `gemini-3.5-flash-lite` on the free tier, JSON-schema output), the default when `GEMINI_API_KEY` is in `engine/.env`. `/ask`, narration and explain verified live. Groq, below, remains available with `LLM_PROVIDER=groq`. Details in LLM.md.
- **Provider:** Groq (`groq` SDK, chat completions with `json_schema` output). The key lives in `engine/.env` (`GROQ_API_KEY=...`, not committed); with a key the provider defaults to `groq`, without one to `mock`. Tests always force `mock`.
- **Model:** `qwen/qwen3.8-27b`, the only text model enabled on the team's Groq project (`DCSIM_LLM_MODEL` overrides). Temperature 0 for JSON, 0.3 for narration. Thinking is off for low-effort jobs (narration, `/ask`, dish priors, explain, persona batches).
- **Free-tier limit:** 1,000 output tokens per minute, and a request that *asks* for more is rejected. Every call is capped at 1,000 (`DCSIM_LLM_MAX_OUTPUT`), the SDK waits out 429s (6 retries), and personas are generated 2 per call, each batch seeing the ones already proposed.
- **New-dish lever:** `levers.add_dishes` (`NewDish`) carries the affinities as plain numbers (snapped to 0.1), so the run stays deterministic and cacheable. The cook sheet marks it `popularity_source: "ai_estimate"`.
- **Trace additions:** `queue_join` and `balk` events carry `line` (students already waiting); going back for a favorite after walking away is a `retry` event, separate from `seconds`.
- **Change from §7.2:** the seconds ceiling is 0.9, not 0.6, because the team confirmed about half of students go back.
- **Web UI (2026-11-13):** the What-If Lab calls `/ask`, `/ai/similar-dishes`, `/ai/dish-prior` and `/explain`; the Hall calls `/traces/{hash}/highlights` and `/narrate` (non-streaming) for students without a pre-built narration.
- **Ask Sam and function calling (2026-11-14):** see `docs/SPONSORS.md` §1–2 and `docs/LLM.md`. Image input is out of scope.
- **Not built yet:** survey adjustment (§7.3), `POST /personas/calibrate`, the uncertainty sweep that would widen ranges for AI estimates.

---

## 8. API surface (engine-facing)

As built (engine 0.3.4, `engine/src/dcsim/api.py`). Every route is under `/api`. Column "Web UI" says which page uses it (the pages read the static bundle first and call the API only when it answers, see `web/README.md`).

| Endpoint | Behavior | Web UI |
| --- | --- | --- |
| `GET /health` | `{ok, engine_version, cache, ledger_writes}` (the Vite proxy answers `{ok:false}` when the API is down; `ledger_writes` false when Tiger isn't set up or `DCSIM_LEDGER_WRITES=0`) | all pages; `smoke-api.mjs` refuses `ledger_writes: true` |
| `POST /simulate` | `ScenarioRequest {date, preset, levers, label, n_nights}` → `200 {status: done, result}` if cached, else `202 {job_id, config_hash, stream}` | Lab |
| `GET /jobs/{id}` | job status + latest result | Lab (polls) |
| `GET /jobs/{id}/stream` | SSE: `progress`, `provisional`, `final`, `error` | — |
| `GET /results/{hash}` | result from cached nights (`?n_nights=`) | Dashboard, Lab (API scenarios) |
| `POST /compare` | `{baseline_hash, scenario_hash}` → paired diff, win rate | — (results carry `vs_baseline`) |
| `GET /traces/{hash}` | median-night (or `?seed=`) trace, **gzipped JSON** (`Content-Encoding: gzip`) | Hall (scenarios without a bundled trace) |
| `GET /traces/{hash}/highlights` | students worth clicking on that night, with the reason | Hall |
| `GET /narrate/{hash}/{seed}/{student}` | SSE `card` → `sentence`… → `final` (cached; template if the mock is on or the check fails); `?stream=false` for JSON | Hall (student card) |
| `POST /ask` | `{question, date}` → lever settings + readable list + what it couldn't express (does not run). With Gemini: function calling (13 lever functions, ids as enums), then JSON schema, then keywords; `method` says which | Lab (plain-English box) |
| `POST /agent` | **Ask Sam**: `{goal, date, max_tries ≤ 5, n_nights}` → `202 {job_id, stream}`; a goal is planned (simulations + goal check + unseen-seed check), a what-if simulated as asked, a question answered from Tiger | Ask Sam |
| `GET /agent/{id}/stream`, `GET /agent/{id}` | SSE: `start`, `goal`, `tiger`, `try_start`, `progress`, `try`, `whatif`, `notice`, `thought`, `validate_start`, `final`, `error`; status + result | Ask Sam |
| `GET /agent/history[/{id}]`, `DELETE /agent/history/{id}` | Sam's final analyses (SQLite `agent_history`); DELETE needs the admin token when set | Ask Sam (History panel) |
| `GET /waste/outlook` | `?n_nights=` → simulated pre-consumer waste (lb, $, stockouts) of every bundled scenario on the coming dates (`outlook.py`); the bundle has `waste/outlook.json` | Insights › Waste history (next 8 nights) |
| `POST /ai/similar-dishes` | `{name, date, station, role}` → menu dishes like a new one | Lab (new-dish form, "suggest") |
| `POST /ai/dish-prior` | `{date, dish}` → persona affinities for a new dish, ready for `levers.add_dishes` | Lab ("AI estimate") |
| `POST /explain` | `{scenario_hash, baseline_hash?}` → short explanation of the paired comparison | Lab ("Why?") |
| `GET /ai/status` | provider, models, load limits | — |
| `GET/POST /scenarios`, `POST /scenarios/{id}/adopt`, `DELETE /scenarios/{id}` | saved / adopted scenarios; adopting logs a forecast to the Tiger ledger when configured | Lab (Save, Adopt, Saved list) |
| `GET /forecasts` | forecast ledger, scored once the night is observed | `web/public/calibration.html` |
| `GET /tiger/dashboard` | `?night=` → waste by night and pans by hour from the continuous aggregates, the one-query join, ledger, hypertable and columnstore facts; 503 without Tiger ([TIGER.md §8](TIGER.md)) | API page `/tiger` only |
| `GET /tiger/bench` | `?repeat=` (1–10) → the same questions timed on raw hypertables and on their aggregates | API page `/tiger` only |
| `GET /similar-nights` | `?date=&students=&weather=&event_night=&menu_date=&limit=` → recorded nights before `date` most like it, with waste and stockouts; Tiger pgvector, else local files ([SIMILAR_NIGHTS.md](SIMILAR_NIGHTS.md)) | Insights › Similar nights |
| `GET /dates`, `/menu/{date}`, `/arrivals/{date}`, `/layout`, `/presets`, `/facts/waste` | reference data (the bundle has static copies) | — |
| `GET /waste/history`, `/waste/backtest`, `/waste/calibration` | synthetic history, back-test (with per-dish rows and per-station splits), held-out calibration | — (bundle copies) |

**Result fields (engine 0.5.0; `series` only with `?include=series`, 0.6.0):** `open`, `close` (serving stops: end of late night), `dinner_close`, `late_close`; `headline` (p10/p50/p90 of waste, stockout-minutes, missed first choice, wait p90, missed meal, students, plus `batch_wait_min`, `cook_busy_share`, `empty_wait_min`, `seat_wait_min`, `peak_seated`); `cook_sheet` (with `period` per dish); `flags`; `stations`; `arrivals`; `staffing` (station → hour → servers needed for ≤ 85% busy); `median_seed`; `baseline_hash`; `vs_baseline`; `waste_lb` (added 2026-11-14: pre-consumer lb/$/portions and stockout minutes, 10/50/90%, in the recorded history's units). Levers added in 0.5.0: `kitchen_staff {cooks, equipment}` and `stations.<id>.shifts [[from_min, servers], ...]`.

Built in 0.6.0–0.6.2: `?include=series` on results; `POST/GET /api/warm` and `POST/GET /api/backtest` as admin background jobs; an optional admin token (`DCSIM_ADMIN_TOKEN`) on DELETE scenarios and the admin jobs; a per-IP limit on the AI routes (`DCSIM_AI_RATE`); finished jobs expire (`DCSIM_JOB_TTL_S`). Verdicts in comparisons: `better` / `worse` / `inconclusive` / `same` (below `runner.min_meaningful`). Added after 0.6.2 (no version bump, the simulation didn't change): `GET /api/similar-nights`, `GET /api/tiger/dashboard`, `GET /api/tiger/bench`, and the page `GET /tiger` (outside `/api`, hidden from the OpenAPI docs; the web app doesn't link to it). Added on 2026-11-14 (no version bump): `POST /api/agent` with its stream, status and history routes (Ask Sam), `GET /api/waste/outlook`, `waste_lb` on results, `ledger_writes` in health, and function calling behind `POST /api/ask`. Planned but **not built:** `POST /personas/calibrate`. Traces are gzipped JSON, not msgpack.

---

## 9. Correctness checks built into the engine

- **Determinism test:** same config and seed → byte-identical `NightMetrics`.
- **Conservation test:** portions cooked = served + expired + leftover, for every dish and every night.
- **Common-random-numbers test:** a no-op change (e.g. re-saving the baseline) gives a paired diff of exactly zero.
- **Golden baseline:** a fixed baseline summary is committed. Any change in it after an engine change must be intentional (bump `engine_version`).
- **Face-validity assertions** on the demo baseline: the favorite runs out during the rush in most nights; the slow seller has leftovers at close in most nights.
- **Calibration check:** the calibrated persona mix reproduces the survey's diet shares exactly, and its implied pairwise win rates beat LLM-only on held-out pairs (or the fallback is used).
- **Narration check:** narration never names a dish or station that isn't in the student's events (test with the mock provider plus a few recorded real responses).

---

## 10. Build order for the engine

1. `ScenarioConfig`, normalization, hashing, `run(config, seed)` for one night with a stub layout. Persona schema + mock provider + frozen LLM persona set v1 (hours 0–4).
2. Recorder → `NightMetrics`, conservation and determinism tests, benchmark (4–8).
3. Process pool, per-night SQLite cache, aggregation, paired comparison, `/simulate` + SSE (8–12).
4. Trace export, preset precompute on startup. Survey adjustment v1 replaces the uncalibrated personas (12–18).
5. Lever-grid warm-up, `/warm`, back-test, click narration + pre-warm, demo bundle (18–26).
6. Optimizer with successive halving; surrogate only if time remains (26+).
