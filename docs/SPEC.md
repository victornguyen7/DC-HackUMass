# DC What-If — Product & Build Spec (v2)

Nov 11, 2026 · Vic (Tri Hien Nguyen) and team · HackUMass

> This spec builds on the original *UMass Dining What-If Simulator* spec (Nov 9). The simulation model, ecosystem fit, data rules and judging strategy carry over. What changes: the product is now **four pages around one shared simulation engine**, the day is modeled hour by hour (dinner in full depth), and the hall is a **3D model of Worcester DC** with animated students.

---

## 1. Decisions locked in

| Topic | Decision |
| --- | --- |
| Dining common | **Worcester DC**. Layout built from our own photos, a measured sketch, and the satellite footprint. |
| Time scope | **Full day at the hourly level** (arrivals, staffing, menu by meal). **Dinner (≈4:30–9:00 pm) at the full dish level** (plates, lines, batches, waste, 3D playback). Breakfast and lunch get the full treatment only as a stretch goal. |
| Menu data | **Real menu** for the selected date from umassdining.com, saved as a snapshot file. A fixed demo menu is the fallback. |
| Waste data | **Synthetic**, shaped like MetaFoodX/LeanPath exports, and **labeled "synthetic" in the UI** everywhere it appears. |
| 3D figures | Low-poly rigged characters with walk/idle/queue/sit animations. **Empty-handed until served**, then carry a **plate or bowl** (UMass is trayless). |
| Engine | **Python only.** SimPy discrete-event simulation behind a FastAPI. The browser **never** runs the simulation; it only plays back results. |
| AI in the simulation | **Hybrid: SimPy + LLM.** The LLM works *before* a run (proposes student personas and new-dish priors) and *after* a run (narration). It is **never called inside the simulation loop**, so runs stay fast and exactly repeatable. |
| Student personas | **LLM proposals, adjusted to a student survey.** The LLM proposes ~20 personas; the survey sets how common each one is and how popular each dish is. Survey wins on any disagreement. |
| Narration | **Only on click.** Clicking a student in 3D shows their event timeline instantly, then a 2–3 sentence first-person story from the LLM. No live speech bubbles. |

Everything else in this document is a default we can change.

---

## 2. Product overview

The product has four pages, all reading from one simulation engine:

> **Update (2026-11-13):** the frontend redesign (PR #1, [FRONTEND_REDESIGN_PLAN.md](FRONTEND_REDESIGN_PLAN.md)) groups these pages into three destinations: **Simulation** (Hall, with the What-If Lab as its advanced settings), **Kitchen plan** (Dashboard) and **Insights** (Waste Record, now "Waste history", plus the new **Similar nights** search, [SIMILAR_NIGHTS.md](SIMILAR_NIGHTS.md)). The sections below keep the original page names.

```
                 ┌───────────────────────────────┐
                 │  Simulation engine (SimPy)     │
                 │  arrivals · plates · kitchen   │
                 └──────────────┬────────────────┘
                                │ results + agent traces
   ┌──────────────┬─────────────┼───────────────┬──────────────┐
   ▼              ▼             ▼               ▼
 1. Hall 3D    2. Dashboard   3. Waste Record  4. What-If Lab
 "watch it"    "today's plan" "past vs plan"   "try a change"
```

The pages share one **scenario context**: a date, a meal and a scenario (Baseline or a saved what-if). If you pick a scenario in the What-If Lab and click **"Watch in 3D"**, the Hall page plays that scenario. The Dashboard always shows the scenario that is currently **adopted** for today.

Global top bar on every page: date picker · meal (Breakfast / Lunch / Dinner) · active scenario chip · "Synthetic data" badge whenever synthetic inputs are in use.

---

## 3. Page 1 — Hall (3D Worcester DC)

**Purpose.** Show what a night looks like: where lines form, which pans run empty and when, where the crowd sits. This page wins the demo, and it must stay faithful to the numbers.

### 3.1 What the user sees
- A 3D model of Worcester DC with an orbit camera, plus three preset views: **Overview** (isometric), **Serving line** (low angle), **Top-down plan**.
- Students enter at the swipe entrance, walk to stations, queue, get food, find a seat, eat, sometimes go back for seconds, drop dishes at the dish return, and leave.
- Each station shows a floating label with its dish names and a **pan fill bar** per dish (green → amber → red when empty). A small "cooking" icon appears with a countdown while a batch is in progress.
- **Timeline scrubber** across the bottom, from open to close, with play/pause and speed controls (1×, 10×, 30×, 60×). Event markers on the scrubber: dish runs out, batch ready, food expired.
- **Side panel (collapsible):** live counters at the current playback time (students inside, in line, seated; longest line and where it is; pans empty right now).
- Clicking a station opens its detail: queue length over time, served count, a run-out marker.
- Clicking a student opens their card: persona ("Student #412 · vegetarian first-year"), the **event timeline immediately** (entered 5:52, stir-fry line 14 people, left the line, pasta, sat zone 2…), then an **LLM narration** that streams in about 1 s later, e.g. *"Wanted the stir-fry but the line wrapped past the salad bar, so I grabbed pasta and came back for dessert."* No live speech bubbles; narration happens only on click (ENGINE.md §7.4).
- **Compare mode (stretch):** split screen, Baseline on the left and the selected scenario on the right, scrubbers synced.

### 3.2 Figures and props
| State | Animation | Hands |
| --- | --- | --- |
| Entering / walking to a station | walk | empty |
| Waiting in line | idle (small sway) | empty or holding plate/bowl if already served elsewhere |
| Being served | idle facing counter | the item appears in hand when service completes |
| Walking with food | walk (carry pose on upper body) | **plate** (entrées, sides, pizza, salad) and/or **bowl** (soup, stir-fry, cereal, pasta if served in bowls) |
| Seated eating | sit + eat loop | items on the table in front of them |
| Seconds trip | walk | empty (dishes left at the seat) |
| Leaving | walk to dish return, then to exit | carrying dishes to the return, then empty |

- Holding two items: plate in one hand, bowl in the other. A third item is shown on the plate, not as a new prop.
- Item → vessel mapping lives in `menu.json` (`vessel: "plate" | "bowl" | "cup"`).
- Diversity: 6–10 character variants (body/hair/clothing color) cloned from 2–3 base rigged models. Use free CC0 low-poly packs (for example Quaternius or Kenney) and record the license in `CREDITS.md`.
- Optional color overlay toggle: color students by diet type, by wait time, or by "missed first choice".

### 3.3 Accuracy of the Worcester model
"High accuracy" here means **the floor plan reads right to someone who eats at Worcester**: the right footprint shape, stations in the right order and place, entrances and seating zones where they really are. It does not mean photoreal.

| Element | Target accuracy | Source |
| --- | --- | --- |
| Building footprint / dining room outline | ±1 m | Google Maps / campus map satellite trace, scaled with a known distance |
| Station positions and order | Correct relative order, ±0.5 m | Photo walk plus a pace or tape-measured sketch |
| Counter lengths, line paths | ±0.5 m | Tape/laser measure, or ceiling and floor tile counts |
| Seating zones, table counts | Count per zone within ±10% | Photos plus a count |
| Entrance / swipe point, dish return, exits | Exact location | Photo walk |
| Decor, materials | Approximate colors and wood/tile textures | Photos |

**Modeling workflow (owned by the Frontend 3D role):**
1. **Photo walk (≈1 hour, before the event or on day 1).** Photograph every station, the entrance, seating areas and the dish return. Count tables. Measure 3–5 reference distances. Note which stations serve which kinds of food. *Do not photograph students' faces; ask Dining staff before photographing back-of-house.*
2. Trace the footprint from satellite imagery, then draw the plan in Figma or on graph paper.
3. Encode the plan as **`layout/worcester.json`** in meters (schema in §8.4): walls, counters, stations, queue paths, seating zones, doors and a navigation waypoint graph.
4. **The 3D scene is generated from the layout JSON** (walls and counters are extruded boxes; tables and chairs are instanced props). A layout fix is a JSON edit, not a modeling job, and the engine uses the same file for walking distances.
5. Accuracy check: put a photo beside the matching camera view and fix anything a Worcester regular would call wrong.

> **Station list is now real** (from the Nov 13–20 menu snapshot): Grill, Tandoor, Mediterranean, Seasons, Latino, Street Food, Noodle Bowl, Sushi, Display, Soups, Breads, Desserts. Street Food, Sushi and Display cook to order. **To confirm on the photo walk:** where each station physically sits, counter lengths and queue paths.

### 3.4 Technical notes
- **React Three Fiber + drei**, GLTF models, `SkeletonUtils.clone` per student with shared `AnimationClip`s.
- **Playback, not simulation.** The engine exports a per-student **trace** (timed events, §8.3). The browser moves each figure along the waypoint graph between events with simple steering (no physics). Queues are slots along a queue path.
- **Crowd cap.** Worcester can have several hundred students inside at peak. Render up to ~350 figures at full detail. Above that, either (a) switch distant figures to an impostor/capsule LOD or (b) show a sample with an "1 figure ≈ N students" note. Target 45+ fps on a mid-range laptop.
- Which night to show: the **median night** (by waste) out of the N simulated nights, so the 3D view is typical rather than a lucky or unlucky night.

---

## 4. Page 2 — Dashboard ("Today")

**Purpose.** One screen a manager checks before and during service to run today and make decisions.

### 4.1 Layout (top to bottom)
1. **Header strip:** date, day of week, academic calendar tag (e.g. "Week 11 · normal", "Finals week", "Thanksgiving break"), hours open today, weather (optional; rain is a crowd modifier), active scenario.
2. **KPI tiles** for the selected meal, each a median with a 10–90% range:
   - Expected students (meal total)
   - Expected peak hour plus peak students per 10 minutes
   - Portions to prepare (total)
   - Forecast waste (portions and lb)
   - Dishes at risk of running out (count, click for the list)
   - Peak wait (p90 minutes)
3. **Expected arrivals chart (full day).** Bars per 10 minutes (with hourly totals on hover) from open to close, with a 10–90% band. Class-change times appear as faint vertical markers, so the "after-class surge, during-class lull" pattern is visible. Meal periods are shaded. Clicking a time bucket jumps the Hall page to that time.
4. **Today's menu by station** (real menu snapshot). Each dish shows diet tags (vegan, vegetarian, halal, gluten-free), its **predicted popularity rank**, **portions to prepare**, starting pans, and a **predicted run-out time** if there is one ("likely empty 6:40–7:10 pm").
5. **Production plan table** (what the kitchen cooks): dish, starting pans, batch size, reorder point, batch start times, last batch time. Export as `production_plan.csv` and print view.
6. **Staffing by station and hour:** servers planned versus servers the model says are needed for p90 wait < X minutes (X is a setting, default 8).
7. **Alerts / side-effect flags:** e.g. "No vegetarian hot entrée after 7:30 pm in this plan", "Stir-fry line > 15 students 5:50–6:20 pm", "General Tso's expected to run out during the rush".
8. **Surplus forecast at close** per dish, plus a Food Recovery Network pickup note (Franklin-style, shown as a template).

### 4.2 Data behind it
- Menu: `data/worcester/menus/<date>_dinner.json`, imported from the real umassdining.com snapshot in `data/raw/menu/` (add-ons such as sauces and cheese are listed but not simulated).
- Arrivals: the full-day arrival model (§7.1) run for the date's calendar context.
- Dish numbers: dinner simulation results for the adopted scenario (30+ nights).
- Breakfast and lunch in the MVP: arrivals, menu and **approximate** portions (popularity share × expected students × items per student), labeled "estimate, not simulated".

---

## 5. Page 3 — Waste Record

**Purpose.** Show what waste **was** (recorded data from the ecosystem tools) and how much the simulator's plans **reduce** it. This is the impact page for judges.

### 5.1 Two kinds of numbers, never mixed
| Kind | Meaning | Visual |
| --- | --- | --- |
| **Recorded** | What the waste tools logged (synthetic in the hackathon, MetaFoodX/LeanPath shape) | Solid bars/lines |
| **Simulated** | What the engine estimates would happen under a plan | Hatched or dashed, always with a range |

The page always shows a legend and a "synthetic data" badge. We never present simulated savings as measured savings.

### 5.2 Sections
1. **Waste history (recorded).** Daily waste (lb and portions) for the last 4–8 weeks. Filter by meal, station and dish. Split by cause: **expired on line** (past hold time), **left in pans at close**, **plate waste** (shown if the source has it, and labeled as *not affected by production planning*).
2. **Top waste dishes.** A ranked table with cost per portion (estimated), so "$ wasted" can be shown alongside lb.
3. **Back-test: "If we had used the plan."** For each past recorded night, the engine re-simulates that night's menu and crowd with the **recommended plan**. The page shows recorded waste vs. simulated waste under the plan, plus how many nights the plan wins. Headline format: "**Estimated reduction: X% (range a–b%)**, about **N lb/week**, **$X/week**", with stockout minutes shown next to it so the cut isn't bought with empty pans. (Current back-test: 6.4% (1.3–8.2%), about 326 lb and $824 per week, stockouts about the same as cooking to the forecast; the 18% in the first draft was a placeholder.)
4. **Adopted-plan tracker.** When a manager adopts a What-If scenario for a date, it is logged. Once that date has recorded waste, the page shows **predicted vs. recorded** for that night. This is how the simulator earns trust over time. In the demo, the synthetic data contains a few "adopted" nights.
5. **Where the waste goes** (one row, context only): prevented → donated (Food Recovery Network) → composted. rStream is mentioned, out of scope.
6. **Export:** `surplus_forecast.csv`, a waste summary CSV.

### 5.3 Synthetic waste generator rules
- It is generated by the **same engine** with added noise (staff over-prep, weather, random events), so recorded and simulated numbers are consistent but not identical. Ground-truth parameters are kept out of the UI.
- Realistic magnitudes are taken from public reports where possible. Every assumed constant is listed in `data/ASSUMPTIONS.md`.
- File shape mirrors MetaFoodX: per dish, per window, portions out / eaten / left (`consumption.csv`), plus a LeanPath-style `waste_log.csv` (timestamp, dish, weight_lb, reason).

---

## 6. Page 4 — What-If Lab

**Purpose.** A manager or chef tests a decision before service: new dish, special menu, batch plan, staffing, crowd. The page tells them **how much to cook and when**, and what goes wrong otherwise.

### 6.1 Layout
- **Left: scenario builder**
  - Start from: Baseline (today) · a preset · a saved scenario.
  - Lever groups (accordion):
    1. **Menu:** add / remove / swap dishes; **New dish wizard** (name, station, diet tags, vessel, batch size, cook time, hold time, "similar to" 1–3 existing dishes → popularity range. The LLM gives each persona an affinity for the new dish, the per-dish survey correction is borrowed from the similar dishes, and the range is widened and labeled "AI estimate"); special-menu toggle (e.g. theme night) that changes the crowd and dish popularity.
    2. **Batch plan** per dish: starting pans, batch size, reorder point, last batch time, max batches.
    3. **Portions:** portion size, self-serve vs. staff-served.
    4. **Stations & staffing:** dish → station assignment, servers per station per hour.
    5. **Crowd:** total multiplier, event preset (first week, pre-exam, rainy, theme night, home game), custom wave shift.
    6. **Order quantity:** total portions or ingredient lb available per dish.
  - **Plain-English box (stretch):** "What if we add a second stir-fry and cut meatloaf in half?" → the LLM fills in the levers, shows which settings it chose, then runs.
  - Buttons: **Run (30 nights)** · **Optimize plan** (stretch) · Save · **Adopt for date** · **Watch in 3D**.
- **Right: results vs. baseline**
  - The 4 headline metrics as median and range, with ▲▼ vs. baseline and a "wins in X of 30 nights" figure.
  - **Cook sheet** (the main answer to "how many should we make"): per dish, total portions to prepare, starting pans, batch times, last batch time, and ingredient quantity if a recipe yield is provided.
  - Run-out timeline per dish (Gantt-like bars of when each pan is empty).
  - Demand shift for a new dish: "Curry takes ~120 portions: 60% from meatloaf, 25% from pasta, …". This answers "which dishes lose demand".
  - Side-effect flags (diet coverage, long lines, late empty pans).
  - **Order risk curve** (stretch): order quantity vs. chance of running out.

### 6.2 Presets (from the original spec)
Baseline · Smaller batches of favorites · Trim slow sellers · Extra server at the busiest station · Theme night (+25% crowd) · Combined plan. All presets are **pre-computed and cached** so the demo works offline.

### 6.3 Behavior rules
- Every scenario uses the **same random seeds** as the baseline (common random numbers).
- A change counts as "better" only if it wins in most nights. The UI says "inconclusive" when it doesn't.
- A typical run (30 nights, dinner) should return in **< 5 seconds** on a laptop. While it runs, show progress, and return cached results instantly for a repeat config (hash of the config).

---

## 7. Simulation engine

> Full design (simulation steps, recording, caching, storage, speed): see **`ENGINE.md`**.

The model from the original spec is kept (arrivals → choices → kitchen, SimPy, minutes, 30–200 nights). Additions:

### 7.1 Full-day arrival model (hourly level)
- Arrival intensity λ(t) for the whole day = base level + a sum of Gaussian peaks aligned **just after class-block end times**, with dips while classes are in session. Each meal period also has its own appetite curve (lunch peak ~12:00–1:30, dinner peak ~5:30–7:00).
- Class block times come from `data/class_schedule.json` (UMass MWF and TuTh block times; **verify against the registrar's published schedule**). Each block has a weight: the share of students who get out at that time and are near Worcester (Southwest/Central residents weigh more).
- Calendar modifiers in `data/calendar.json`: first week, normal, pre-exam, finals, breaks, home games, holidays.
- Weather modifier (optional): rain or cold → more students stay near their residence hall DC.
- Output: arrivals per 10-minute window, with a range.

### 7.2 Dinner full-detail model
As in the original spec, plus:
- **Spatial walking:** walking times come from the layout's waypoint graph, so the 3D view and the numbers agree (a far station really does take longer to reach).
- **Seating:** seats per zone from the layout. Students pick the nearest free seat in a preferred zone. Dwell time ~ lognormal (median ~25 minutes).
- **Vessel tracking:** each served item records its vessel (plate/bowl) for the trace.
- **Personas:** each student is drawn from the calibrated persona set (§7.5), which sets their diet, patience, plate size, chance of seconds, dwell time and dish affinities.
- **Seconds:** a probability per student, more likely if their first choice was a favorite.
- **Missed meal:** a student who gets no hot item.

### 7.3 Outputs per run
- Aggregates over N nights: the headline metrics (median, p10, p90), per-dish run-out times, waste by dish and cause, wait distribution, station queue lengths over time.
- **One trace** (the median night) for 3D playback.
- Recommended plan (if the optimizer ran), side-effect flags.

### 7.4 Interface
`run(config: ScenarioConfig) -> ScenarioResult`. This is a pure function of config + seed set, so the result can be cached by config hash.

### 7.5 AI layer (SimPy + LLM hybrid)
| When | What the LLM does | Affects numbers? |
| --- | --- | --- |
| Before a run (offline, cached) | Proposes ~20 student personas (frozen per semester) and affinities for new dishes; turns plain-English questions into lever settings (`/ask`, stretch) | Yes, but only through **survey-adjusted** inputs |
| During a run | Nothing. SimPy samples every choice from the persona tables using the seeded random generator. | — |
| After a run (on demand, cached) | Narrates a clicked student's recorded events; explains results (stretch) | No |

**Survey adjustment:** persona shares are adjusted to match the survey totals, and each dish gets one correction factor fit to the survey's pairwise answers. A held-out check compares LLM-only, survey-only and LLM + survey, and the engine uses whichever predicts held-out answers best. Details in ENGINE.md §7.

**How it differs from similar projects:** LLM-driven student sims (e.g. Traycer) ask an LLM for every decision. That's lively but slow, costly, not repeatable, and they run one service at a time. This engine runs hundreds of calibrated nights in seconds and uses the LLM only where it adds value without adding noise.

---

## 8. Data contracts

All inputs are files, so real exports can replace them without code changes.

### 8.1 Inputs
| File | Contents |
| --- | --- |
| `data/raw/menu/worcester_menus_<start>_to_<end>.json` | Raw umassdining.com snapshot (all meals, nutrition, allergens, carbon rating) |
| `data/worcester/menus/<date>_dinner.json` | Imported dinner: `dishes[]` (id, name, station, role, diet_tags, vessel, hot, serving_size, carbon_rating) + `addons[]`, written by `dcsim prepare` |
| `data/worcester/kitchen/<date>_dinner.json` | Baseline production plan per batch-cooked dish, generated from neutral-popularity demand |
| `data/worcester/overrides.json` | Hand fixes: item roles, staff popularity multipliers |
| `data/dish_params.json` | per dish: batch_size, cook_min, hold_min, max_batches, cost_per_portion, popularity_prior (mean, sd) |
| `data/popularity.csv` | survey pairwise results → Bradley-Terry weights (or consumption-derived) |
| `data/class_schedule.json` | block end times by day pattern, plus weights |
| `data/calendar.json` | date → modifiers |
| `data/arrivals.csv` | date, meal, dc, window_start, entries (synthetic swipe-style, for calibration) |
| `data/consumption.csv` | MetaFoodX-style: date, meal, dc, station, dish, window_start, portions_out, portions_eaten, portions_left |
| `data/waste_log.csv` | LeanPath-style: timestamp, dc, dish, weight_lb, reason (expired / close / plate) |
| `layout/worcester.json` | spatial layout (§8.4) |
| `data/survey_responses.csv` | anonymous survey: diet, residential area, line patience, seconds frequency, usual dinner time (MWF / TuTh), 8–12 pairwise dish picks |
| `data/personas_raw.json` | LLM-proposed personas (reviewed by a human, committed, frozen per semester) |
| `data/personas_calibrated.json` | persona shares + per-dish corrections after survey adjustment, with bootstrap ranges (**what the engine reads**) |
| `data/calibration_report.json` | fit stats: held-out accuracy of LLM-only vs. survey-only vs. LLM + survey |

### 8.2 Outputs
`production_plan.csv`, `order_risk.csv`, `surplus_forecast.csv` (as in the original spec), plus `cook_sheet.csv` (portions to prepare per dish).

### 8.3 Trace format (engine → 3D)
```json
{
  "night_seed": 17,
  "students": [
    {"id": 412, "diet": "veg", "events": [
      {"t": 1052.0, "type": "enter"},
      {"t": 1053.5, "type": "queue_join", "station": "stirfry"},
      {"t": 1061.2, "type": "served", "station": "stirfry", "dish": "veg_lo_mein", "vessel": "bowl"},
      {"t": 1063.0, "type": "sit", "seat": "Z2-T14-S3"},
      {"t": 1089.0, "type": "dish_return"},
      {"t": 1090.5, "type": "exit"}
    ]}
  ],
  "pans": [{"t": 1100.0, "dish": "general_tsos", "portions": 0, "event": "empty"}]
}
```
`t` is minutes since midnight. Walking between events is interpolated by the client along the waypoint graph.

### 8.4 Layout format
```json
{
  "units": "m",
  "footprint": [[0,0],[42,0],[42,30],[0,30]],
  "walls": [{"from":[0,0],"to":[42,0],"height":4}],
  "doors": [{"id":"main","pos":[20,0],"type":"entrance_swipe"}],
  "stations": [{"id":"stirfry","name":"Stir-Fry","counter":[[5,8],[11,8]],
                "serve_point":[8,9],"queue_path":[[8,10],[8,14],[10,16]],"servers":2}],
  "seating_zones": [{"id":"Z2","polygon":[...],"tables":24,"seats_per_table":4}],
  "dish_return": {"pos":[38,4]},
  "nav_graph": {"nodes":{"n1":[20,2]},"edges":[["n1","n2"]]}
}
```

---

## 9. Architecture & stack

| Layer | Technology |
| --- | --- |
| Engine | Python 3.11, SimPy, NumPy, pandas; multiprocessing for nights |
| Optimizer / sweep | Optuna or random search (stretch) |
| Popularity & personas | Bradley-Terry via `choix`; persona calibration (raking + per-dish corrections) with SciPy; bootstrap for ranges |
| Menu ingest | Small Python scraper → snapshot JSON (run once per date, cached; respect the site's terms; fallback to the demo menu) |
| API | FastAPI: `GET /menu/{date}`, `GET /arrivals/{date}`, `POST /simulate`, `POST /compare`, `GET /waste/history`, `POST /waste/backtest`, `POST /scenarios` (save/adopt), `POST /ask` (stretch). **As built:** all under `/api`; the back-test is `dcsim backtest` + `GET /api/waste/backtest`; full list in ENGINE.md §8 |
| Cache | Results keyed by config hash on disk (JSON); presets pre-computed for the demo |
| Frontend | React + Vite + TypeScript, React Router (4 routes), Zustand (shared scenario context), Recharts, Tailwind. **As built (`web/`):** React 18 + Vite + TypeScript, React Router, Zustand; plain CSS with the Figma tokens and small hand-written SVG charts instead of Tailwind and Recharts (about 100 KB JS gzipped) |
| 3D | React Three Fiber, drei, three.js GLTF + SkeletonUtils. **Not built yet:** the Hall plays the night as a top-down 2D replay on the floor plan; "3D overview" is a static render (UI_ISSUES U2) |
| LLM | Google Gemini (`google-genai` SDK, `gemini-3.5-flash-lite`; Groq `qwen/qwen3.8-27b` as the alternative), structured JSON output validated with Pydantic. Used offline for personas and new-dish priors, on click for narration, and for `/ask` (stretch). SQLite response cache + deterministic mock provider. **Never called inside the simulation loop.** |
| Deploy | **Built 2026-11-14:** app and API on one Vultr server (https://155-138-211-252.sslip.io): Caddy serves the built app over HTTPS and proxies `/api`, systemd runs the API (`deploy/README.md`). The app still runs fully offline from cached results. |

Repo layout:
```
/engine        SimPy model, arrival model, optimizer, synthetic data generator
/ai            LLM client + cache, persona generator, survey calibration, narrator, mock provider
/api           FastAPI app
/web           React app (pages: hall, dashboard, waste, lab); static demo bundle in web/public/cache
/data          inputs + ASSUMPTIONS.md
/layout        worcester.json, reference photos (no faces), sketch
/cache         pre-computed scenario results
```

---

## 10. Scope and priorities

Four pages plus 3D is more than the original MVP, so each page has a **P0** (must work at demo time), P1 (should) and P2 (stretch).

| Page | P0 | P1 | P2 |
| --- | --- | --- | --- |
| Hall 3D | Worcester layout from JSON, students enter/queue/get plate or bowl/sit/leave, pan bars, scrubber, play speeds | Click station/student detail (event timeline + LLM narration on click), color overlays, seconds trips | Split-screen compare, impostor LOD for big crowds |
| Personas (engine input) | Frozen LLM persona set v1 with hand-checked shares (works without the survey) | Survey adjustment (shares + per-dish corrections), held-out check, bootstrap ranges | MetaFoodX consumption as the adjustment target |
| Dashboard | Date + menu by station, full-day arrival chart, dinner KPIs with ranges, production plan, alerts | Staffing table, surplus forecast, CSV export | Weather, breakfast/lunch dish-level sim |
| Waste Record | Recorded history chart (synthetic, labeled), top waste dishes, back-test headline reduction with range | Cause split, $ estimate, adopted-plan tracker | Donation/compost flow |
| What-If Lab | Presets, batch/menu/crowd/staffing levers, run vs. baseline, cook sheet, side-effect flags | New dish wizard with demand shift, save/adopt, "Watch in 3D" | Optimizer, plain-English box, order risk curve |

**Out of scope:** live integration with any UMass system, cameras/hardware, real student data, mobile app.

**Status 2026-11-13** (details in [UI_ISSUES.md](UI_ISSUES.md)):
- **Hall:** P0 and P1 built on a top-down 2D replay (not 3D). Station and student detail, narration on click, color overlays. Split-screen compare (P2) not built.
- **Dashboard:** P0 and P1 built for **dinner + late night** (engine 0.4.1; no breakfast/lunch); the staffing table shows servers and peak lines. "Servers needed" per hour is computed by the engine since 0.5.0 (`result.staffing`) but not shown yet (UI_ISSUES U5).
- **Engine (0.6.0) beyond this spec:** late night 9 pm–12 am, students choosing lines from a distance, cooks and equipment as limited resources, staffing by hour, waits at empty pans, hotel-pan sizes with a hot-hold cabinet, entrance swipe queue, groups, allergies and a pescetarian diet, crowd by date, a forecast-reorder preset, per-minute series. See ENGINE.md §3 and IMPROVEMENTS.md.
- **Waste Record:** P0 and P1 built, including per-dish "with plan" and $ per week.
- **What-If Lab:** P0 and P1 built (new-dish form with AI estimate and demand shift, save/adopt, Watch in 3D), plus the P2 plain-English box (Gemini function calling since 2026-11-14). Optimizer and order risk curve not built. Menu edits, new dishes and lever combinations need the API (`make server`).

**Added 2026-11-14, beyond this spec:**
- **Ask Sam** (`/sam`): a planning agent (Gemini function calling over the simulator and the Tiger MCP server). Goals are planned in at most 5 simulated tries with a goal check in code and an unseen-night check; what-ifs are simulated as asked; history questions are answered from Tiger; final analyses are kept in a history. Details: [SPONSORS.md §2](SPONSORS.md).
- **Waste Record looks ahead:** Insights › Waste history opens on the next 8 nights, simulated with each night's adopted plan, next to the recorded past (the past is one click away).
- **Decided:** breakfast and lunch stay out of scope for the demo (dinner + late night); image input (menu photos) is out of scope.

---

## 11. Build plan (36 hours, 4 people)

| Role | Owns |
| --- | --- |
| Simulation lead (Vic) | Engine: arrival model, dinner model, traces, presets, back-test, optimizer |
| Data & forecasting | Menu scraper + snapshot, dish params, student survey, persona survey adjustment, synthetic waste generator, ASSUMPTIONS.md |
| Backend & AI | FastAPI, caching, scenario save/adopt, LLM client + cache + mock, persona generation, click narration, `/ask` |
| Frontend & 3D | Worcester layout JSON + 3D scene, 4 pages, charts, pitch |

> The 3D hall is the biggest single work item. If the team has a fifth person, give them the 3D scene. Otherwise the backend person picks up the Dashboard and Waste pages after hour 12.

| Hours | Checkpoint (each one leaves a working demo) |
| --- | --- |
| Before event (if rules allow) | Photo walk at Worcester, layout sketch, choose character pack. Check HackUMass rules on prior work. |
| 0–4 | Lock config/result/trace/layout/persona schemas. Menu snapshot in. LLM persona set v1 generated, reviewed and frozen (the fallback). Survey link out. Engine runs one dinner. `worcester.json` v1 renders as boxes. |
| 4–12 | Full-day arrivals, dinner behavior, kitchen batches, 4 presets. API live. Dashboard shows real numbers. Figures walk the nav graph from a trace. Survey responses in → persona adjustment v1. |
| 12–18 | Plates/bowls, queues, seating in 3D. What-If runs vs. baseline + cook sheet. Synthetic waste + Waste page history and back-test. **Full demo path works end to end.** |
| 18–26 | P1 items: new dish wizard, adopt/watch-in-3D, staffing, station/student detail. Then the optimizer. |
| 26–32 | Pre-compute every demo scenario, accuracy pass on the 3D model vs. photos, sanity-check the baseline, performance pass. |
| 32–36 | Slides, rehearse twice, record a backup video. |

---

## 12. Demo flow (3 minutes)

**Numbers below are from the engine 0.3.4–0.4.1 bundle.** The engine is now 0.6.1 (Friday Nov 13 crowd ×0.9: about 3,720 students across dinner + late night; back-test waste about 8.8% lower, 38 of 39 nights), so re-read every number from the app after `make demo-bundle` before presenting (UI_ISSUES U1). Updated 2026-11-13 to what the built app and the Nov 13 bundle actually show (the first draft used placeholder dishes and an "≈18%" waste figure the data doesn't support; see UI_ISSUES U1).

1. **Story (20 s)** on the Hall page: Worcester at about 7:30 pm, the busiest 10 minutes (about 220 students arriving). The Grill line reaches about 36 people and the Paneer Jalfrezi pan at Tandoor goes red around 8:25 pm. Click one student: their timeline and story appear (pre-generated for 12 highlight students per night; any student with the API running).
2. **Dashboard (30 s):** Friday's real menu by station, the dinner arrival chart (click the 7:30 bar to jump the Hall there), about 3,000 expected students, 11,005 portions to prepare, the alert that Paneer Jalfrezi runs out in every simulated night, and what's expected to be left at close (waffles, clam chowder) for donation pickup.
3. **What-If (60 s):** pick **Smaller batches of favorites**: about 46 fewer portions wasted, better on all 30 paired nights, stockouts about the same (inconclusive). With the API, ask in plain English ("one more server at Grill and 10% busier") or add a new dish at Tandoor with an AI estimate: the cook sheet updates and "Where demand moves" shows which dishes lose students to it. Click **Watch in 3D** to replay that night.
4. **Waste Record (40 s):** recorded history vs. the plan back-test: "about **6% less waste** (1–8%), plan wins 34 of 39 nights, about $800 a week, with stockouts **about the same**", clearly labeled as simulated on synthetic records. Be ready to say that most of the reduction comes from cooking to the forecast (ISSUES B1).
5. **Fit (30 s):** built on data UMass already collects (MetaFoodX-shaped inputs, a production-plan output). The pilot path is one DC for one week.

**Positioning vs. similar projects:** "Other projects simulate one lunch with AI students to explore ideas. We simulate hundreds of nights to tell a manager exactly how many pans to cook. AI builds a realistic student body, a survey keeps it honest, and we use the data UMass already collects, no new cameras."

---

## 13. Validation & risks

Validation (unchanged from the original spec): face validity of the baseline night, comparison with 3–5 nights of shift notes, sensitivity sweep, feedback from a manager if possible. **New for 3D:** a Worcester regular looks at photo-vs-render pairs and lists what's wrong.

| Risk | Mitigation |
| --- | --- |
| 3D eats the schedule | Layout-driven procedural scene. Boxes first, models last. Capsule fallback ready. |
| Too many figures → low fps | Cap at ~350, LOD/impostors, "1 figure ≈ N students" note |
| Real menu site changes / blocks scraping | Snapshot files committed. Demo menu fallback. |
| Judges read synthetic waste as real | Badge + legend on every view, hatched simulated bars, ASSUMPTIONS.md |
| 3D and numbers disagree | Both read the same layout file. 3D plays engine traces only. |
| Photos of people | No faces, front-of-house only unless staff approve |
| Network fails on stage | Everything cached; frontend works against local API or static JSON |
| LLM personas are biased or made up | Survey adjustment + held-out check; fall back to survey-only if LLM + survey doesn't predict better |
| Narration describes things that didn't happen | Prompt contains only the student's recorded events; dish and station names in the output are checked against them, with a template fallback |
| LLM output changes between runs | Response cache keyed by prompt version + inputs + model; personas frozen per semester; mock provider for tests |
| Looks like a copy of an LLM-student project | Position on rigor: hundreds of calibrated nights, ranges, win rate, cook sheet |

---

## 14. Open questions (answer when possible, defaults in brackets)

1. Current Worcester station list and which station serves what. [Confirm on the photo walk.]
2. Worcester hours this semester, including late night. [Use the umassdining.com hours page.]
3. Can we get even rough real numbers from Dining (swipes per hour, pans per dish)? [No. Synthetic, labeled.]
4. Is code written before the event allowed? [Assume no. The pre-event work is only photos, sketches and schemas.]
5. Do we have a fifth teammate for 3D? [No.]
6. Default wait target for the staffing recommendation. [8 minutes p90.]
7. Which models for persona generation vs. narration? [Since 2026-11-13: Gemini `gemini-3.5-flash-lite` for everything. Before: Groq `qwen/qwen3.8-27b` for everything (the only text model enabled on the team's Groq project); thinking off for narration. Override with `DCSIM_LLM_MODEL`. See ENGINE.md §7.6.]
8. Can the student survey run before the hackathon, or only at the event? [At the event; the frozen LLM persona set is the fallback until responses arrive.]
9. Does the Data & forecasting teammate know Bradley-Terry and bootstrapping? [If not, ship survey adjustment step 1 + per-dish corrections without the held-out comparison.]
