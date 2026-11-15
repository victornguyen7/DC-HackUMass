# Sponsor prizes: what we're going for and how to build it

Five sponsor prizes that fit DC What-If, what already exists in the repo for each one, and the steps to finish it.
Pick one, put your name in the **Owner** column, and update **Status** as you go.

Status as of 2026-11-13, engine 0.6.2, after merging PRs #1–#3 (README §14). Engine 0.6.1 notes: kitchen capacity since 0.5.0 makes the optimizer meaningful: small batches now cost cook and oven time; a `forecast_reorder` preset exists to compare against). Roll-up of everything done / open: [STATUS.md](STATUS.md).

---

## Summary

| # | Prize | Our feature | Status | Effort left | Owner |
| --- | --- | --- | --- | --- | --- |
| 1 | Best Use of Gemini API | Plain-English what-if box on **Gemini function calling**; Sam's tool loop | ✅ built | — | |
| 2 | Best Agentic Hack (Sentient) | **Planning agent, Sam the Minuteman**: goal → Tiger lookups (MCP) + ≤ 5 simulations → checks its own results → production plan; questions answered from Tiger; history | ✅ built 2026-11-14 | S (demo polish) | |
| 3 | Best Use of Tiger Data | Every simulated and "observed" night as time series, side by side; similar-night and dish search; every plan search kept and queried in SQL | ✅ built (500-night query done 2026-11-14) | S (demo) | |
| 4 | Best Use of Vultr | API + simulation workers in the cloud; **a temporary fleet: every menu date optimized at once on rented 16-vCPU servers (3.3 min, $0.07)**; 1,000-night uncertainty sweep | ✅ **deployed 2026-11-14** at https://155-138-211-252.sslip.io (`deploy/`) | S (time the 1,000-world sweep there, write-up) | |
| 5 | Best Domain Name (GoDaddy Registry) | A real domain for the demo link | ✗ not done (the server uses a free sslip.io name until then) | ~10 min | |

✅ built · ◐ partly built · ✗ not started

**Prizes 1 and 2 share most of their code.** The agent is Gemini function calling plus a loop, so one feature can enter both.
**Prizes 4 and 5 go together.** Point the domain at the Vultr server (`155.138.211.252`) and re-run setup with it, and the demo link becomes `https://<our-domain>` (today: https://155-138-211-252.sslip.io).

**Check at the event (don't assume):**
- [ ] Sentient track rules: does it require Sentient's own platform or SDK? We couldn't find the rules.
- [ ] GoDaddy Registry: which domain extensions qualify, and how to claim the free domain (see §5).
- [ ] Vultr: how to claim the hackathon credits.
- [ ] How many prizes one project may enter, and whether each sponsor needs its own submission or write-up.

---

## 1. Best Use of Gemini API

**Pitch:** "Type a what-if in plain English and Gemini calls the simulator's own settings functions to set it up. Give Sam a goal and Gemini drives the simulator through function calls until the plan works."

### What already exists
Gemini is already the main LLM (`gemini-3.5-flash-lite`, key in `engine/.env`). Full details: [`GEMINI.md`](GEMINI.md), rules: [`LLM.md`](LLM.md).

| Feature | Where | Status |
| --- | --- | --- |
| Plain English → settings (`/api/ask`, Lab's "Ask in plain English" box) | `engine/src/dcsim/ai/ask.py` | ✅ built: **function calling** with Gemini, JSON-schema and keyword fallbacks |
| Student stories (Hall → click a student) | `ai/narrate.py`, `/api/narrate/...` | ✅ built, streaming |
| "Why?" explanations | `ai/explain.py`, `/api/explain` | ✅ built |
| New-dish AI estimate | `ai/dish_prior.py`, `/api/ai/dish-prior` | ✅ built (`gemini-4-hydrogen`) |
| Similar dishes (embeddings) | `ai/similar.py`, `/api/ai/similar-dishes` | ✅ built |
| Function calling | `ai/ask.py`, `ai/agent.py` (Sam), `LLM.tool_turn` | ✅ built |

### Built: function calling for the what-if box (2026-11-14)
With a Gemini key, the Lab's "Ask in plain English" box (`/api/ask`, `dcsim ask`) answers through **function calling** (`ai/ask.py`, `ask-fc-v1`):
- 13 declared functions, one per lever: `apply_preset`, `set_crowd`, `shift_arrivals`, `set_servers`, `set_service_time`, `set_shift`, `set_cooks`, `set_batch`, `scale_batch`, `set_popularity`, `remove_dish`, plus `note_unsupported` and `explain`. Station and dish arguments are enums of that date's real ids, so the model can't invent one.
- Mode `ANY` (the model must answer with calls; several per turn). The calls are collected into the same `AskPlan` the JSON path uses and checked by building the config. Invalid calls go back as function responses with their errors for one re-ask.
- Fallbacks: still invalid, or Gemini down → the JSON-schema path → keyword matching. The reply's `method` says which answered (`function_calling`, `json_schema`, `keywords`); the Lab shows it under the answer.
- Cached per conversation in `llm_cache`. Tests: `engine/tests/test_ask_fc.py` (fake client: request shape and enums, re-ask with the error, fallback to JSON, batch math).
- Live on `gemini-3.5-flash-lite`, 1.0–1.3 s per question: "one more server at Grill and 10% busier", "cut the hot pot batches in half and add a cook at dinner", "remove the eggplant parmesan and make the black bean burger twice as popular", "one extra Grill server from 6:30 pm to 8 pm only" (two shifts) all mapped exactly; "raise prices and open at 4 pm" went to unsupported.

Ask Sam (§2) uses function calling too, for its tools.

Image input (a menu-photo import) is **out of scope** for this project.

**Rules (LLM.md):** never inside the simulation, a fallback for every job, a check before anyone sees the output, cached answers, and AI-made values labeled "AI estimate".

---

## 2. Best Agentic Hack (Sentient)

**Pitch:** "The manager gives a goal: *cut waste 20% without any vegetarian dish running out*. The agent runs simulations itself, reads the results, adjusts, tries again, and hands back a production plan with its reasoning."

It counts as a real agent because it has a goal, tools, and a loop that checks its own results. It's built on Gemini function calling, so it also strengthens prize 1.

> ⚠️ Confirm the Sentient track rules at the event. If they require Sentient's platform, the tools below can be exposed through it instead.

### Built (2026-11-14): Sam the Minuteman

The agent has the UMass mascot as its persona: **Sam the Minuteman**, drawn as an original tricorn-hat avatar in UMass maroon (not the official logo).

| Piece | Where |
| --- | --- |
| Agent loop, tools, goal check, rule-based fallback, hold-out check | `engine/src/dcsim/ai/agent.py` |
| One Gemini function-calling turn (mode ANY, cached per conversation, so a demo replays instantly) | `LLM.tool_turn` in `ai/llm.py` |
| API: `POST /api/agent {goal, date, max_tries, n_nights}` → 202; SSE `GET /api/agent/{id}/stream` (start, goal, try_start, progress, try, notice, validate_start, final / error); `GET /api/agent/{id}` | `api.py` |
| CLI: `uv run dcsim agent "cut waste 20% without any vegetarian dish running out"` | `cli.py` |
| Web: **Ask Sam** page (`/sam`): goal box, live tries with Sam's notes and pass/fail checks, final plan with reasoning, the unseen-night check, cook-sheet changes, **Adopt**, **Open in the Lab**, **Watch a night** | `web/src/pages/Sam.tsx`, `lib/agent.ts`, `components/SamAvatar.tsx`, `sam.css` |
| Tests | `engine/tests/test_agent.py` (fake Gemini, cache replay, fallback, API), `web/src/lib/agent.test.ts` |

How it works: Gemini parses the goal into targets (`Goal`). Each `run_simulation` call takes scale factors for groups (`unprotected`, `protected`, `all`, a station, a dish) plus exact per-dish values. The engine runs 30 paired nights, and `check_goal` (code) checks four things: waste cut ≥ target; no protected dish runs out more than today (share of nights +0.10, median empty minutes +1); missed meals not "worse"; long wait not "worse". The tool result includes a factual `diagnosis`. `finish(try_id, reasoning)` ends the run. The code overrides a pick that fails when another try passes. Then the plan is re-run on unseen seeds 31–60.

**Added 2026-11-14 (later):**
- **At most 5 tries.** `MAX_TRIES = 5` in `agent.py`; the API rejects `max_tries > 5` (422), the CLI takes `--tries 1-5`.
- **Tiger Data through the Tiger MCP server.** The engine is an MCP client of `tiger mcp start stdio` (Python `mcp` package, `ai/tiger_mcp.py`). Sam gets `query_tiger(sql, why)` and `tiger_schema()`, at most 6 lookups per run, and they don't use simulation tries. Before its first try, Sam looks up which protected dishes ran out on past nights with tonight's weekday. Only the server's `db_schema` and `db_query` are ever called (never `service_*`). `db_query` would run any SQL on this DEV service, so every query first passes `guard_sql`: one SELECT/WITH, no write, session or `INTO` keywords. It then runs inside `BEGIN TRANSACTION READ ONLY` with a 20 s timeout, and Sam sees at most 40 rows. Needs `TIGER_SERVICE_ID` and the Tiger CLI login; `DCSIM_TIGER_MCP=0` turns it off.
- **Questions.** A question instead of a goal ("Which dishes ran out on rainy Fridays?") is answered from Tiger only, with no simulations. The goal parser sets `kind: question`. Live: 3 rainy Fridays, Cumin Basmati Rice and Savory Baked Chicken Thigh, matching TIGER.md §6.
- **History.** Every finished run keeps only its final analysis (prompt, menu date, time asked, reasoning or answer, checks, cook-sheet changes, Tiger lookups; no per-try details). It is stored in the API's SQLite (`agent_history`; `GET /api/agent/history`, `GET /api/agent/history/{id}`, `DELETE` with the admin token) and copied in the browser (localStorage, last 40), so past analyses open with the API down. The CLI saves there too. The Ask Sam page lists them on the right.

**Added 2026-11-14 (later still):**
- **Setting functions in plans.** Next to `run_simulation`'s batch scaling, Sam can call the what-if box's setting functions (`ai/ask.py`): `apply_preset`, `set_servers`, `set_shift`, `set_service_time`, `set_cooks`, `set_batch`, `scale_batch`, `remove_dish`, `note_unsupported`, `explain`. Calls made in a turn apply to that turn's `run_simulation` only (merged with the scaling; explicit per-dish values win). `set_crowd`, `shift_arrivals` and `set_popularity` describe the night, not the plan, so they are not offered when planning (otherwise Sam could "meet" a waste goal by assuming fewer students), and removing a protected dish is refused. The prompt tells Sam staff costs money: add servers or cooks only when batches alone can't meet the goal.
- **What-ifs in the Ask Sam box.** The goal reader has a third kind, `whatif` ("what if we add a Grill server and it's 10% busier?"): Sam reads it with the same function calling as the Lab's box (all 13 functions), simulates it as asked (30 nights) and compares it with tonight's plan (waste, missed meals, wait, empty-pan minutes), with Adopt / Open in the Lab / Watch a night, and keeps it in the history. No search and no goal check. The Lab's "Ask in plain English" box stays as it is.
- Tests: `engine/tests/test_agent_levers.py`; `web/src/lib/agent.test.ts`.

Settings: `DCSIM_AGENT_MODEL` (default `strong` = `gemini-4-hydrogen`; it falls back to the main model, then to the rules), `DCSIM_AGENT_EFFORT` (default `low`), `DCSIM_AGENT_MAX_RUNNING` (default 2).

First live result (Nov 13, "cut waste 20% without any vegetarian dish running out", before the 5-try cap): 8 tries in ~50 s. The best try cut waste 18–22% and protected every vegetarian dish, but missed meals came out "worse" by about 4 students, so Sam honestly reports **"closest plan, goal not fully met"**. "Cut waste 15% and keep every halal dish stocked" went the same way: try 5 hit −15% with all 42 halal dishes protected, but the long wait came out "worse" (+0.6 min), so the goal is not met. Each goal checks every guard (waste, protected dishes, missed meals, wait), and so far none has passed all four in 8 tries. For a demo, either show the honest "closest plan" or, before the demo, find a goal that passes and replay it from the cache.

### What already existed
| Piece | Where |
| --- | --- |
| Run N paired nights for any config (cached by config hash) | `Service.ensure_nights`, `Service.result` in `engine/src/dcsim/service.py`; `POST /api/simulate` |
| Compare scenario vs baseline (win rate, better / worse / inconclusive) | `Service.compare_hashes`; `POST /api/compare` |
| Every lever as data | `ScenarioRequest`, `Levers`, `StationLever` in `engine/src/dcsim/levers.py` |
| Per-dish results with **diet tags and stockout minutes** | each result's `cook_sheet[]`: `diet_tags`, `stockout_min`, `runout_share`, `waste`, `plan` |
| Background jobs with progress streaming (SSE) | `api.py` jobs: `/api/jobs/{id}/stream` |
| Optimizer | ✗ not built (ISSUES N6). Design notes: [`IMPROVEMENTS.md`](IMPROVEMENTS.md) §2.1 |

So "no vegetarian dish runs out" can be checked today: every dish in `cook_sheet` where `'vegetarian' in diet_tags` must have `stockout_min.p50 == 0` (or `runout_share` under a threshold).

### To build (M–L)
1. **Tools** (new `engine/src/dcsim/ai/agent.py`). Each is a thin wrapper over code that already exists, and each returns a *small* JSON summary, not the full result:
   - `get_menu(date)`: dishes, stations, diet tags, current batch plan.
   - `run_simulation(date, levers)`: headline ranges plus the dishes that run out or waste the most.
   - `compare(date, levers)`: the scenario vs the baseline, with verdicts.
   - `check_goal(result)`: **deterministic code**, not the LLM. It checks waste reduction ≥ target and that no vegetarian dish has stockouts.
   - `optimize(date, constraint)` *(optional, after IMPROVEMENTS §2.1)*: a search the agent can hand off to.
2. **Loop:** Gemini with these tools, at most about 8 steps. Each step the model picks levers, calls `run_simulation`, reads `check_goal`, and adjusts. Stop when the goal is met or the budget runs out, and say so honestly if it wasn't met.
3. **Output:** the final cook sheet (reuse `reports.py`), the goal check, and a step-by-step log ("tried halving Grill batches → waste −12%, but Veggie Burger ran out at 7:40 → raised its reorder point…").
4. **API + UI:** `POST /api/agent` as a background job, streaming each step over SSE (the jobs system already does this). In the Lab: a "Plan for a goal" box that shows the steps live.
5. **Validate:** re-run the final plan on unseen seeds (31–60), like IMPROVEMENTS §2.1 says, so the agent doesn't overfit its own nights.
6. **Rules:** the LLM proposes levers. **Every number comes from the engine**, and `check_goal` is code. Cache each step (configs repeat, so results come from the cache).

**Demo line:** "Same goal, three tries, 90 simulated nights, and here's the plan it settled on and why."

---

## 3. Best Use of Tiger Data

**Pitch:** "Every simulated night and every observed night is time-series data in Tiger, in the same tables, so one SQL query asks *did our forecast hold?*"

### What already exists (most of it)
Full details: [`TIGER.md`](TIGER.md). Code: `engine/src/dcsim/tsdb.py`, `engine/src/dcsim/calibration.py`.

| Feature | Status |
| --- | --- |
| Hypertables: `nights`, `pan_windows` (per dish per 30 min), `waste_log`; `planned_batches` | ✅ built |
| Observed (**synthetic**) nights and simulated runs side by side, told apart by `scenario` | ✅ built (`make tsdb-load`) |
| Held-out calibration (fit weeks 1–4, score weeks 5–6) | ✅ built (`make calibrate`, ~40 s) |
| Continuous aggregate `dish_nightly`, view `forecast_check` | ✅ built |
| Append-only **forecast ledger**; adopting a plan in the Lab writes to it | ✅ built |
| Calibration page (`/calibration.html`) | ✅ built |
| Columnstore compression | ✅ built (`make tsdb-compress`) |
| **Similar-night search**: pgvector + pgvectorscale DiskANN index over 77-dim night vectors (weekday, weather, event, menu), with a held-out check (`uv run dcsim tsdb similar --date 2026-11-14 --weather rain`) | ✅ built (`nightsearch.py`, TIGER.md §7) |
| **Similar nights in the app**: Insights › Similar nights (`/similar-nights`) and `GET /api/similar-nights`. Same vectors and DiskANN index as `dcsim tsdb similar` (ISSUES T5), local fallback when Tiger isn't set up | ✅ built (`similar_nights.py`, [SIMILAR_NIGHTS.md](SIMILAR_NIGHTS.md)); Tiger's copy of the history is current (ISSUES T3 fixed 2026-11-14) |
| **Live dashboard** (API page `http://127.0.0.1:8000/tiger`, not in the web app): waste by night and pans by hour from two real-time continuous aggregates with refresh policies, raw-vs-aggregate timings (`dcsim tsdb bench`: 557 ms → 82 ms on the by-hour question; the columnstore wins the per-dish one), one SQL query across aggregates + hypertable + plan + forecasts + ledger, columnstore 76 MB → 9.6 MB | ✅ built 2026-11-14 (TIGER.md §8: demo script, pitch, what is left) |
| **Calibration over time**: every night scored into hypertable `forecast_results` by a nightly Tiger job (`add_job`), weekly real-time continuous aggregate `forecast_weekly` (`uv run dcsim tsdb track`) | ✅ built 2026-11-14 (TIGER.md §4) |
| **Weather/event flags** in the crowd error, kept only if held-out CRPS improves (`uv run dcsim calibrate --situations-only`) | ✅ built 2026-11-14; not kept on this history (205.8 vs 200.0), TIGER.md §5 |
| **Tiger MCP for live questions**: schema comments for the assistant, question set answered live (*"which dishes ran out on rainy Fridays?"*) | ✅ built 2026-11-14 (TIGER.md §6); service is DEV, so the MCP is not read-only |
| **Dish search + popularity check**: look-alike dishes across every menu (pgvector + DiskANN over 111-dim dish vectors) with what they sold; a new dish's popularity spread in plausible worlds comes from them. Per-dish popularity error measured from observed vs replay: none on this synthetic history (τ 0.021), recovered on a fixture with a known spread (`uv run dcsim tsdb dishes --name "Butter Chicken" --role entree`) | ✅ built 2026-11-14 (TIGER.md §9; Lab new-dish form, `POST /api/dish-evidence`) |
| **Plan searches in Tiger**: every Optimize plan / Plausible worlds run kept (`plan_searches`, hypertable `search_trials`, `search_worlds`); "which lever keeps showing up on the frontier" in one SQL query (`uv run dcsim tsdb searches`) | ✅ built 2026-11-14 (TIGER.md §10; Lab Optimize card, `/tiger` dashboard card, `GET /api/searches`) |
| **500-night question**: 500 simulated nights of one menu date in Tiger (scenario `sweep`); one SQL statement gives each dish's average / p90 minutes out of stock, share of nights it runs out and when (`uv run dcsim tsdb sweep --date 2026-11-13 --nights 500`, `--dish cauliflower`; `dcsim tsdb query sweep`). Sam answers it through the MCP | ✅ built 2026-11-14 (TIGER.md §11): Nov 13 loaded, 500 nights, 29,500 dish rows, ~115 ms |

Setup: `tiger db save-password <service-id>`, or put `DCSIM_TSDB_URL=postgresql://...` in `engine/.env`.

### To build or polish (S)
1. ✅ **Demo query from the pitch** (TIGER.md §11): General Tso's isn't on the real menus, so it's a real dish: *"across 500 simulated nights of Friday's menu, Roasted Cauliflower at late night is out of stock 5.8 min on average, 13.6 min on a bad night (p90), on 69% of nights, first around 10:47 pm"*, in one SQL statement (~115 ms). Run `uv run dcsim tsdb sweep --date 2026-11-13 --report-only --dish cauliflower`, or ask Sam the same question. Bonus story: the chicken thigh is out the last 5 minutes every night because its last pan hits the 60-minute hold limit at 8:55 and no batch may start after 8:45.
2. **Tiger MCP on stage** (TIGER.md §6): ask the question set there live. The MCP's read-only mode covers PROD services only; this one is DEV, so keep per-call approval on and deny the `service_*` tools.
3. **Similar nights on stage:** "Which past nights look like tonight, and what happened on them?" is a good live demo of pgvector + pgvectorscale on Tiger. Show it in the app (Insights › Similar nights, the source chip reads "Searched in Tiger · pgvector"), then the CLI's held-out check. Tiger's history matches the local files (re-run `dcsim tsdb load` after any `make waste`), and say plainly that it's a hand-built feature vector, not a learned embedding.
4. **Show the aggregates:** open `http://127.0.0.1:8000/tiger` (API page, not in the web app) and follow the 2-minute script in TIGER.md §8: a new night appears within 5 s, then raw vs aggregate timings.
5. **Show it in the app:** link the calibration page from Insights › Waste history, and show "forecast logged to Tiger ✓" after **Adopt**.
6. **Plan searches and new dishes on stage** (TIGER.md §9–10): run Optimize in the Lab, point at "Saved to Tiger ✓" and "What keeps showing up on the frontier", then open the `/tiger` Plan searches card. In Menu › Add a new dish, type a name and show the "In Tiger: dishes like this … sold …" line. Tiger already holds one saved run per menu date (2026-11-14), so the lever table shows a pattern: Seasons and Mediterranean batches smaller on every date's frontier. Say plainly that the popularity check finds no per-dish error because the history is synthetic.
7. **Be honest on stage:** the "observed" nights are **synthetic**. Say so (README §8).

---

## 4. Best Use of Vultr

**Pitch:** "Running 1,000 nights for the uncertainty sweep happens on a cloud server, not a laptop."

### Done (2026-11-14): the app runs on Vultr
**Live link: https://155-138-211-252.sslip.io**. Runbook, settings and troubleshooting: [`deploy/README.md`](../deploy/README.md).

| Piece | Where / state |
| --- | --- |
| Server | Vultr, Ubuntu 24.04, `155.138.211.252`; the app in `/srv/dc-whatif`, deployed from `main` |
| One-command setup | `deploy/setup.sh` (as root; safe to re-run, that's how it updates): Node 24, uv, build, `/etc/dcsim.env`, systemd, Caddy, firewall (22/80/443). Commits `1e7c8c5`, `e3ffbc2`, `3ebc72c` and the Tiger-password follow-up |
| Secrets and cache | `deploy/push-secrets.sh root@<ip>` from a laptop: `engine/.env` plus `DCSIM_TSDB_URL` from `tiger db uri`, and the 275 MB `cache/` (the server shows 729 configs, 16,168 nights, 992 LLM responses cached) |
| API | `dcsim.service` (user `dcsim`, `127.0.0.1:8000`, `DCSIM_WORKERS` = core count) |
| HTTPS, one origin | Caddy serves `web/dist` and passes `/api/*`, `/tiger`, `/docs` to the API (SSE unbuffered). Let's Encrypt certificate for the sslip.io name, valid until 2027-02-13 |
| Public-site safety (ISSUES A1) | admin token set, AI rate limit 10/min per visitor, **`DCSIM_LEDGER_WRITES=0`** so visitors' Adopt clicks don't write to the append-only Tiger ledger |
| Ask Sam's Tiger lookups | work on the server: Tiger CLI logged in as `dcsim`, password in `~/.pgpass` (`deploy/tiger-password.sh`; a server has no keyring) |
| Checked from outside | the app and direct links load, the bundle is served, `/api/health` OK, `/tiger` loads, Gemini answers, Sam answers "Which dishes ran out on rainy Fridays?" from Tiger |
| Uncertainty sweep | ✅ built (`worlds.py`, `POST /api/worlds`, `dcsim worlds --worlds 1000`); 200 worlds take 54 s on an 8-core laptop, so 1,000 is the cloud story (IMPROVEMENTS §2.2) |

### Built 2026-11-15: a temporary fleet for plan searches (`dcsim fleet`)
One command creates temporary 16-vCPU Vultr servers, runs the optimizer for every menu date on them at once, saves each search to Tiger and deletes the servers; the `/tiger` dashboard's **Fleet (Vultr)** card shows it live. Code: `engine/src/dcsim/fleet.py`; runbook: [deploy/README.md](../deploy/README.md#fleet-many-plan-searches-at-once-on-temporary-servers-dcsim-fleet).

**First run (2026-11-15, run `f72caee8`):** all 8 menu dates, 80 plans each, saved to Tiger in **3.3 min** on 3 × 16-vCPU CPU-optimized servers (`voc-c-16c-32gb-300s-amd`, Atlanta, $0.438/h); one after another on those servers they'd take 6.0 min, and on the 8-core laptop about 13 min. Each server was reachable and set up (uv, clone, `uv sync`) about 60 s after it was created; each date's search took 37–50 s (the laptop: 86–160 s). 9 server-minutes, about **$0.07**; every server deleted. 8 servers were asked for, but **the account's monthly-fee limit allowed only 3**: the other 5 creates were refused and the 3 servers shared the dates. Ask Vultr support for a limit increase to run all 8 at once (then about 2 min).

**Pitch:** "Optimizing the whole week takes 13 minutes on a laptop. One command rents Vultr servers for three minutes, runs every day at once, saves each answer to Tiger and gives the servers back: about seven cents."

### Left (S)
0. **Fleet at full size:** request a monthly-fee limit increase from Vultr, then `uv run dcsim fleet optimize` again for 8 servers at once (TIGER.md §10 then holds three searches per date).
1. **Evidence for the judges:** on the server, `sudo -iu dcsim bash -lc 'cd /srv/dc-whatif/engine && time uv run dcsim worlds --worlds 1000'`. Keep the time next to the laptop's 200 worlds in 54 s, plus screenshots of `htop` during the run and the Vultr dashboard.
2. **Before the demo:** `DCSIM_LEDGER_WRITES=1` in `/etc/dcsim.env`, then `systemctl restart dcsim` (deploy/README.md, Day to day), so Adopt logs to Tiger on stage.
3. **Write-up:** "The API, the simulation workers and the 1,000-world sweep run on a Vultr CPU-optimized server", with the link.
4. **Domain:** when §5 is done, re-run setup with `DOMAIN=<domain>`; it updates the allowed origin and gets a new certificate.

---

## 5. Best Domain Name from GoDaddy Registry

**Pitch:** a clean, memorable demo link on the slides instead of `localhost:5173`.

### To do (~10 min)
1. **Check which extensions qualify first.** The prize is for a domain from *GoDaddy Registry*. Get the claim link or code from MLH at the event and see which extensions it covers. Not every extension counts. For example, `.tech` is run by a different registry, so `dcwhatif.tech` may **not** qualify.
2. Pick a name: short, says what it does, easy to say on stage. Ideas: `dcwhatif`, `whatifdining`, `platewise`, `panplanner`. Check what's available.
3. Register it with the team's shared account, not someone's personal one if avoidable, and write down who owns it.
4. DNS: an `A` record for `@` (and `www`) pointing at the Vultr server, `155.138.211.252` (§4). Then on the server: `DOMAIN=<domain> TIGER_CLI=1 bash /srv/dc-whatif/deploy/setup.sh`, and Caddy gets the HTTPS certificate. Until then the server answers at https://155-138-211-252.sslip.io (a free name for the IP; it doesn't count for this prize).
5. Put the link on the title slide, in the Devpost submission, and in the README.

---

## Suggested order

1. ✅ **Vultr server (§4)**: live 2026-11-14 at https://155-138-211-252.sslip.io. **Domain (§5)** still open: DNS takes a while, so do it early.
2. **Agent (§2)**: the biggest new feature, and it enters two prizes. Start with 3 tools and the loop. Add `optimize` only if there's time.
3. ✅ **Gemini (§1)**: function calling is built for the what-if box and for Sam.
4. **Tiger polish (§3)**: the 500-night query (track record, situational fit and MCP questions are done).
5. **Uncertainty sweep on Vultr (§4, Left 1)**: the sweep is built and the server is up; time the 1,000-world run there.

## One demo story that hits all five

> Open **https://our-domain** (5, served from Vultr: 4; until the domain exists, https://155-138-211-252.sslip.io). Type *"one more server at Grill and 10% busier"* → Gemini's function calls set it up and the Lab runs it (1).
> Type *"cut waste 20% without any vegetarian dish running out"* → the agent runs simulations on the server, shows each try, and hands back a cook sheet (2, 1, 4).
> Adopt it → the forecast is logged to Tiger; open the calibration page and ask the Tiger MCP *"which dishes ran out on rainy Fridays?"* (3).
