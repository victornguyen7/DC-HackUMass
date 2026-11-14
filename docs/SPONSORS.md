# Sponsor prizes: what we're going for and how to build it

Five sponsor prizes that fit DC What-If, what already exists in the repo for each one, and the steps to finish it.
Pick one, put your name in the **Owner** column, and update **Status** as you go.

Status as of 2026-11-13, engine 0.6.2, after merging PRs #1–#3 (README §14). Engine 0.6.1 notes: kitchen capacity since 0.5.0 makes the optimizer meaningful: small batches now cost cook and oven time; a `forecast_reorder` preset exists to compare against). Roll-up of everything done / open: [STATUS.md](STATUS.md).

---

## Summary

| # | Prize | Our feature | Status | Effort left | Owner |
| --- | --- | --- | --- | --- | --- |
| 1 | Best Use of Gemini API | Plain-English what-if box on Gemini; **menu photo → menu JSON** | ◐ box built, photo import not | S–M | |
| 2 | Best Agentic Hack (Sentient) | **Planning agent**: goal → tool calls → checks its own results → production plan | ✗ not built | M–L | |
| 3 | Best Use of Tiger Data | Every simulated and "observed" night as time series, side by side; similar-night search | ◐ mostly built | S (polish + demo) | |
| 4 | Best Use of Vultr | API + simulation workers in the cloud; 1,000-night uncertainty sweep | ✗ not built | S–M | |
| 5 | Best Domain Name (GoDaddy Registry) | A real domain for the demo link | ✗ not done | ~10 min | |

✅ built · ◐ partly built · ✗ not started

**Prizes 1 and 2 share most of their code.** The agent is Gemini function calling plus a loop, so one feature can enter both.
**Prizes 4 and 5 go together.** Point the domain at the Vultr server and the demo link becomes `https://<our-domain>`.

**Check at the event (don't assume):**
- [ ] Sentient track rules: does it require Sentient's own platform or SDK? We couldn't find the rules.
- [ ] GoDaddy Registry: which domain extensions qualify, and how to claim the free domain (see §5).
- [ ] Vultr: how to claim the hackathon credits.
- [ ] How many prizes one project may enter, and whether each sponsor needs its own submission or write-up.

---

## 1. Best Use of Gemini API

**Pitch:** "Type a what-if in plain English and Gemini turns it into simulation settings. Or photograph the menu board and Gemini turns it into tonight's menu."

### What already exists
Gemini is already the main LLM (`gemini-3.5-flash-lite`, key in `engine/.env`). Full details: [`GEMINI.md`](GEMINI.md), rules: [`LLM.md`](LLM.md).

| Feature | Where | Status |
| --- | --- | --- |
| Plain English → settings (`/api/ask`, Lab's "Ask in plain English" box) | `engine/src/dcsim/ai/ask.py` | ✅ built, uses **JSON-schema output** (`AskPlan`), not function calling |
| Student stories (Hall → click a student) | `ai/narrate.py`, `/api/narrate/...` | ✅ built, streaming |
| "Why?" explanations | `ai/explain.py`, `/api/explain` | ✅ built |
| New-dish AI estimate | `ai/dish_prior.py`, `/api/ai/dish-prior` | ✅ built (`gemini-4-hydrogen`) |
| Similar dishes (embeddings) | `ai/similar.py`, `/api/ai/similar-dishes` | ✅ built |
| Function calling | — | tested on our key and works (GEMINI.md §3.3), **not used yet** |
| Image input (menu photo) | — | tested on our key and works (GEMINI.md §3.3), **not built** |

### To build

**A. Function calling for the what-if box** (S). Do this only if we want to say "function calling" on stage. The JSON version already works.
1. In `ai/ask.py`, declare one function per lever, with arguments matching the `Levers` fields in `engine/src/dcsim/levers.py`:
   `set_servers(station, servers)`, `scale_batch(dish_id, multiplier)`, `set_crowd(multiplier)`, `shift_arrivals(minutes)`, `set_popularity(dish_id, multiplier)`, `remove_dish(dish_id)`, `apply_preset(preset)`.
2. Pass them as `tools=` in the Gemini config. Map each returned call onto a `Levers` field, and reject unknown station or dish ids with the same check `ask.py` uses today.
3. Keep the JSON-schema path as the fallback, and keep the keyword parser for `LLM_PROVIDER=mock`.
4. Tests: add a fake-client case in `engine/tests/test_ai.py`. Tests never call Google.

**B. Menu photo → menu JSON** (M). This is the "wow" moment, and it solves "UMass Dining has no menu API".
1. New module `engine/src/dcsim/ai/menu_photo.py`: send the image with `Part.from_bytes(data, mime_type)` and a Pydantic schema shaped like the `dishes` entries in `data/worcester/menus/<date>_dinner.json` (`id`, `name`, `station`, `diet_tags`, …).
2. **Check before saving:** every station must exist in `data/worcester/stations.json`, and diet tags must come from the known set. Then reuse the classification in `engine/src/dcsim/menu_import.py` to assign roles (entree / side / addon …).
3. Save as a **draft** (for example `data/llm_drafts/menus/`) for a person to review. Never write straight into `data/worcester/menus/`. LLM.md rule: AI output is a draft until reviewed.
4. API: `POST /api/ai/menu-from-photo` (multipart upload). CLI: `uv run dcsim menu-photo path/to/photo.jpg --date 2026-11-21`.
5. UI: an "Import menu from photo" button in the Lab, with the draft shown as a table to approve.
6. Demo: photograph the actual Worcester menu board on the day of judging.

**Rules (LLM.md):** never inside the simulation, a fallback for every job, a check before anyone sees the output, cached answers, and AI-made values labeled "AI estimate".

---

## 2. Best Agentic Hack (Sentient)

**Pitch:** "The manager gives a goal: *cut waste 20% without any vegetarian dish running out*. The agent runs simulations itself, reads the results, adjusts, tries again, and hands back a production plan with its reasoning."

It counts as a real agent because it has a goal, tools, and a loop that checks its own results. It's built on Gemini function calling, so it also strengthens prize 1.

> ⚠️ Confirm the Sentient track rules at the event. If they require Sentient's platform, the tools below can be exposed through it instead.

### What already exists
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
| **Similar-night search**: pgvector + pgvectorscale DiskANN index over 76-dim night vectors (weekday, weather, event, menu), with a held-out check (`uv run dcsim tsdb similar --date 2026-11-14 --weather rain`) | ✅ built (`nightsearch.py`, TIGER.md §7) |
| **Similar nights in the app**: Insights › Similar nights (`/similar-nights`) and `GET /api/similar-nights`. 48-dim condition vectors in `dcsim.night_embeddings`, exact cosine ranking in Postgres, local fallback when Tiger isn't set up | ✅ built (`similar_nights.py`, [SIMILAR_NIGHTS.md](SIMILAR_NIGHTS.md)); Tiger's copy of the history is stale until `dcsim tsdb load` (ISSUES T3) |
| **Live dashboard** (`/tiger.html`): waste by night and pans by hour from two real-time continuous aggregates with refresh policies, raw-vs-aggregate timings (`dcsim tsdb bench`: 557 ms → 82 ms on the by-hour question; the columnstore wins the per-dish one), one SQL query across aggregates + hypertable + plan + forecasts + ledger, columnstore 76 MB → 9.6 MB | ✅ built 2026-11-14, not committed yet (TIGER.md §8: demo script, pitch, handoff) |
| Calibration over time, weather/event flags, Tiger MCP for live questions | ✗ ideas (TIGER.md "Not built yet"; MCP not installed) |

Setup: `tiger db save-password <service-id>`, or put `DCSIM_TSDB_URL=postgresql://...` in `engine/.env`.

### To build or polish (S)
1. **Demo query from the pitch:** "average empty-pan minutes for General Tso's across 500 nights". Add it as a named query in `tsdb.QUERIES` (`uv run dcsim tsdb query <name>`) built on `dish_nightly`, and run enough simulated nights (≥ 500) for that date.
2. **Tiger MCP server** (TIGER.md #6): `tiger mcp start`, then judges ask questions in plain English against the tables live. Keep `read_only` on.
3. **Similar nights on stage:** "Which past nights look like tonight, and what happened on them?" is a good live demo of pgvector + pgvectorscale on Tiger. Show it in the app (Insights › Similar nights, the source chip reads "Searched in Tiger · pgvector"), then the CLI's held-out check. Run `dcsim tsdb load` first (ISSUES T3), and say plainly that it's a hand-built feature vector, not a learned embedding.
4. **Show it in the app:** link the calibration page from Insights › Waste history, and show "forecast logged to Tiger ✓" after **Adopt**.
5. **Be honest on stage:** the "observed" nights are **synthetic**. Say so (README §8).

---

## 4. Best Use of Vultr

**Pitch:** "Running 1,000 nights for the uncertainty sweep happens on a cloud server, not a laptop."

### What already exists
| Piece | Where |
| --- | --- |
| FastAPI server | `make serve` → `uv run dcsim serve` (`engine/src/dcsim/api.py`) |
| Parallel simulation workers | `ProcessPoolExecutor`, size set by `DCSIM_WORKERS` (default: CPU count) |
| Allowed web origins | `DCSIM_CORS` (default `http://localhost:5173`) |
| The web app calls a relative `/api/...` | `web/src/lib/data.ts`, so serving the app and the API on one domain needs no code change |
| Uncertainty sweep | ✗ not built (ISSUES N7; design in IMPROVEMENTS §2.2) |
| Deploy files (Dockerfile etc.) | ✗ none |

### To build (S–M)
1. **Server:** claim the Vultr credits and create a CPU-optimized instance (more cores = more parallel nights), Ubuntu LTS.
2. **Install:** `uv`, Node 24, clone the repo, `cd engine && uv sync`, `cd web && npm ci && npm run build`.
3. **Run the API** as a systemd service: `uv run dcsim serve --host 127.0.0.1 --port 8000`. Set `DCSIM_WORKERS` to the core count and copy `engine/.env` over **by hand** (never commit it).
4. **One domain, HTTPS:** put Caddy in front. It serves `web/dist` and proxies `/api/*` to `127.0.0.1:8000`, and Caddy gets the HTTPS certificate automatically once the domain from §5 points at the server.
   ```
   our-domain.example {
       handle /api/* { reverse_proxy 127.0.0.1:8000 }
       handle { root * /srv/dc-whatif/web/dist; try_files {path} /index.html; file_server }
   }
   ```
5. **⚠️ Security before going public:** the API has **no auth** (ISSUES A1), and LLM routes spend our Gemini quota. Add a token for write routes and a rate limit on `/api/ai/*`, `/api/ask`, `/api/narrate`, or keep the server private until the demo.
6. **The reason to use the cloud:** build the uncertainty sweep (IMPROVEMENTS §2.2): 1,000 nights × (baseline + plan) on the server, shown as "the plan wins in X% of plausible worlds". Also run `make demo-bundle` there with `LLM_PROVIDER=mock`, or with the key, if you can wait for narration.

---

## 5. Best Domain Name from GoDaddy Registry

**Pitch:** a clean, memorable demo link on the slides instead of `localhost:5173`.

### To do (~10 min)
1. **Check which extensions qualify first.** The prize is for a domain from *GoDaddy Registry*. Get the claim link or code from MLH at the event and see which extensions it covers. Not every extension counts. For example, `.tech` is run by a different registry, so `dcwhatif.tech` may **not** qualify.
2. Pick a name: short, says what it does, easy to say on stage. Ideas: `dcwhatif`, `whatifdining`, `platewise`, `panplanner`. Check what's available.
3. Register it with the team's shared account, not someone's personal one if avoidable, and write down who owns it.
4. DNS: an `A` record for `@` (and `www`) pointing at the Vultr server's IP from §4. Caddy then serves HTTPS automatically.
5. Put the link on the title slide, in the Devpost submission, and in the README.

---

## Suggested order

1. **Domain (§5)** and **Vultr server (§4)** early. DNS and certificates take a while to take effect, and every later demo benefits.
2. **Agent (§2)**: the biggest new feature, and it enters two prizes. Start with 3 tools and the loop. Add `optimize` only if there's time.
3. **Gemini extras (§1)**: function calling falls out of the agent work. The menu photo import is separate and quick to demo.
4. **Tiger polish (§3)**: the 500-night query and the MCP server.
5. **Uncertainty sweep on Vultr (§4.6)** if there's time left.

## One demo story that hits all five

> Open **https://our-domain** (5, served from Vultr: 4). Photograph tonight's menu board → Gemini turns it into the menu (1).
> Type *"cut waste 20% without any vegetarian dish running out"* → the agent runs simulations on the server, shows each try, and hands back a cook sheet (2, 1, 4).
> Adopt it → the forecast is logged to Tiger; open the calibration page and ask the Tiger MCP *"which dishes ran out on rainy Fridays?"* (3).
