# LLM layer

Status as of 2026-11-13 (engine 0.6.1; the LLM layer last changed in 0.6.1: `/ask` prompt `ask-v3` can set cooks per period and server shifts, and `describe.py` lists kitchen staff, shifts and forecast reordering; narration wording changed for 0.4.1 events). **Provider: Google Gemini** (switched from Groq on 2026-11-13; Groq still works with `LLM_PROVIDER=groq`). Models, tested features and ideas: [GEMINI.md](GEMINI.md). Code: `engine/src/dcsim/ai/`. Design background: ENGINE.md §7 (plan) and §7.6 (as built).

**Personas are paused.** The engine uses the hand-written `data/worcester/personas.json`. LLM persona generation is turned off until a different approach replaces it (see [Personas](#personas-paused)). Everything else in this file is live.

---

## 1. Rules that hold everywhere

1. **Never inside the simulation.** SimPy makes every decision from seeded random numbers. The LLM only prepares inputs before a run or describes results after one.
2. **LLM output reaches the numbers only through inputs you can see:** lever settings from `/ask`, and persona affinities for a new dish. Both are plain numbers inside the `ScenarioRequest`, so they are part of the config hash and a run stays repeatable.
3. **Every job has a deterministic fallback.** The app works with no key, no network and a failing model.
4. **Every output is checked** before anyone sees it. Failing the check means the fallback is used. Nothing is retried endlessly.
5. **Every real response is cached** (`llm_cache` table), so the same request never calls the API twice and a demo repeats the same words.
6. **Labels:** responses say where they came from (`source`: `llm`, `cache`, `template`, `mock`, `fallback`). New-dish values are labeled "AI estimate". Simulated results are never called "saved" or "measured".

---

## 2. Configuration

| Setting | Where | Default | Notes |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | `engine/.env` (file mode 600, in `.gitignore`) | — | Main key, free tier (replaced the first key, which had no credit and returned 402). Rotate after the hackathon: it was pasted into a chat. |
| `GROQ_API_KEY` | `engine/.env` | — | Previous provider, kept so you can switch back. Also rotate. |
| `LLM_PROVIDER` | env or `.env` | `gemini` if `GEMINI_API_KEY` is set, else `groq` if `GROQ_API_KEY` is set, else `mock` | `gemini`, `groq` or `mock`. The Anthropic provider was removed. |
| `DCSIM_LLM_MODEL` | env or `.env` | Gemini: `gemini-3.5-flash-lite`; Groq: `qwen/qwen3.8-27b` | Tested on the free key 2026-11-13: `gemini-3.5-flash-lite` answers in 0.5–1.4 s. `gemini-3.8-flash` works but often returns 503 "high demand" and rejects thinking level `MINIMAL`. `gemini-3.5-flash` 403, `2.5-flash(-lite)` 404, `gemma-4-31b-it` 500. On Groq only Qwen is enabled. |
| `DCSIM_LLM_MAX_OUTPUT` | env | Gemini `8000`, Groq `1000` | Groq free tier: 1,000 output tokens per minute, and a request that *asks* for more is rejected. Gemini counts thinking tokens against this, so calls get extra headroom: 1,500 tokens at low effort, 4,000 otherwise. |
| `DCSIM_LLM_FALLBACK_MODELS` | env | Gemini: `gemini-3.1-flash-lite` | Comma list tried in order when the main model is busy (408/429/5xx/timeout). Streaming switches only before the first word. Set it to empty to turn this off. |
| `DCSIM_LLM_TIMEOUT` | env | `60` | Seconds a request may stall before it fails (Gemini and Groq clients), and the cap on a whole narration stream; the student then gets the template, which is not cached (ISSUES L5). The Gemini SDK retries a timed-out call, so one stalled call can take about 3× this. |
| `DCSIM_LLM_MODEL_STRONG` | env | Gemini: `gemini-4-hydrogen` | Model for one-off jobs that can wait (dish prior). No backup chain. |
| `DCSIM_LLM_MAX_LIVE` / `DCSIM_LLM_RPM` | env | `4` / `15` | Live narration load gate. Over either limit a click gets the template at once (`reason: "LLM busy (load limit)"`, not cached). Google doesn't report the free-tier limit per key, so 15/min is a guess. |
| `DCSIM_EMBED_MODEL` | env | `gemini-embedding-001` | Similar-dish suggestions. 256 dimensions, each vector cached (`kind: "embed"`). |
| `DCSIM_PERSONAS_LLM` | env | unset | Must be `1` for `dcsim personas generate/adopt` to run (paused). |

Real environment variables win over `.env`. Tests set `LLM_PROVIDER=mock` in `tests/conftest.py` and never call Gemini or Groq.

---

## 3. What exists

### 3.1 Client: `ai/llm.py`
- **Gemini** (`google-genai` SDK): `generate_content` with `response_mime_type: application/json` and `response_json_schema` from the Pydantic model; system prompt as `system_instruction`; assistant turns sent as `model`. Low effort uses `thinking_level: LOW` (3.x models) or `thinking_budget: 0` (2.x flash); other jobs use the model's default. Retries only 408/429/5xx, 3 attempts, so a click never hangs long and a 402/403 falls back in about a second. Streaming uses `client.aio.models.generate_content_stream`. `MAX_TOKENS` or an empty reply counts as an error.
- The Groq details below still apply when `LLM_PROVIDER=groq`.
- `LLM.structured(kind, prompt_version, system, user, schema, mock, check, ...)`
  - Groq chat completion with `response_format: json_schema` (from the Pydantic model), temperature 0, then validated with Pydantic.
  - `check(value)` returns a list of problems. With problems, the model is re-asked once with the exact list.
  - On an API error, invalid JSON or the token cap: returns `mock()` with `source: "fallback"`. That result is **not cached**, so the next request tries the model again. Pass `fallback=False` to raise instead.
- `LLM.stream_text(system, user)`: streamed text (temperature 0.3). Raises `LLMError` on failure.
- `low` effort turns thinking off (`reasoning_effort: "none"`), and thinking is never returned (`reasoning_format: "hidden"`).
- The Groq SDK retries 429s itself (6 retries, honoring retry-after).
- Cache key: `sha256(kind, prompt_version, model, inputs)`. Changing the model or bumping a prompt version means old rows never match again.

### 3.2 Jobs

Live results on Gemini (2026-11-13, second round after the changes below):
- **Narration v3:** 6 of 6 from the model and passed the stricter check, about 1.5 s each. One hit the token cap until low-effort calls got 1,500 tokens of thinking headroom.
- **`/ask`:** right on "one more Grill server, Hot Pot batches in half, 15% busier". **Wrong on** "close the Sushi station and make 20% more pizza": it set Sushi servers 3 → 1 *and* listed "close the Sushi station" as unsupported. Closing a station is not a lever.
- **Dish prior v2** on `gemini-4-hydrogen`: 7.6 s, reasons within 8 words, small changes (regular 1.0 → 1.2 for a smash burger).
- **Explain v3:** names the runout window ("runs out earlier, from 8:04 pm to 8:22 pm"). The wording can be clumsy ("reduced ... by a median of -67.5").
- **Similar dishes:** "Smash Burger" → Halal Burger Patty, Black Bean Burger. "Chocolate Chip Cookie" needs `role=dessert` to rank desserts first.

The "Live result" column below is from Groq Qwen (first round).


| Job | Entry points | Prompt version | Check | Fallback | Live result on Qwen 27B |
| --- | --- | --- | --- | --- | --- |
| **Click narration** (`narrate.py`) | `GET /api/narrate/{hash}/{seed}/{student}` (SSE: `card`, `sentence`…, `final`; `?stream=false` for JSON), `dcsim narrate` | `narrate-v3` | Each sentence is checked **before it is sent**: no dish or station outside the student's events; clock times within 1 min of an event; every other number from the events (or stay length, lines skipped, items got); a student who got nothing can't "eat" or "grab" anything; ≤600 chars | Template narration | Good. 75 of 75 passed the check; about 1.5 s each |
| **Highlights** (`narrate.py`) | `GET /api/traces/{hash}/highlights` | — | — | — | No LLM involved |
| **Plain English → levers** (`ask.py`) | `POST /api/ask`, `dcsim ask "..."` | `ask-v2` | The request must build a valid config; describing changes without setting any is re-asked | Keyword parser | Good after v2 (all fields required). v1 put the changes only in the explanation |
| **New-dish affinities** (`dish_prior.py`) | `POST /api/ai/dish-prior` → drop `lever` into `levers.add_dishes` | `dish-prior-v2` (strong model) | Every eligible persona, 0.1–10, reasons ≤8 words | Similar-dish average | Works; conservative (kept the similar-dish values); reasons are wordy |
| **Explain a comparison** (`explain.py`) | `POST /api/explain`, `dcsim explain --preset X` | `explain-v3` (adds earliest runout windows) | Every number must appear in the facts; no "saved / measured / will"; ≤700 chars | Template from the verdicts | Good after v2 added `change_pct_of_baseline`; v1 computed its own numbers |
| **Similar dishes** (`similar.py`) | `POST /api/ai/similar-dishes`, `dcsim similar "name" [--role --station]` | `embed-v1` | — (suggestions only; the manager confirms `similar_to`) | Shared words + same role/station | Good with a role given; see above |
| **Status** | `GET /api/ai/status`, `dcsim ai-status` | — | — | — | Provider, models, backups, load |
| **Personas** (`personas.py`) | `dcsim personas generate / adopt` | `personas-v3` | Per-persona limits, unique ids, required diets | None: writes an `incomplete` file that `adopt` refuses | **Paused**, see below |

Supporting pieces:
- `similar.near_duplicates(llm, {id: text}, threshold)`: flags pairs that mean nearly the same thing. Built for the persona replacement's duplicate check; **not wired in** while personas are paused.
- `describe.py`: readable list of what a config changes vs the baseline. Used by `/ask` and explain.
- `levers.NewDish` / `add_dishes`: adds a dish with borrowed popularity (geometric mean of the "similar to" dishes), per-persona affinity snapped to 0.1, and a batch plan copied from a similar dish. It is flagged `estimate` and shows on the cook sheet as `popularity_source: "ai_estimate"`.
- Trace fields the narration relies on: `queue_join` and `balk` carry `line`; going back for a favorite is a `retry` event. Since engine 0.4.1 `balk` means "saw that line from where they stood and chose not to join it" (no walking away), `retry` means "looked at the line again", and `closed` means the dish's service period ended while they were in line (9 pm or midnight); `_event_text` in `narrate.py` words them that way.
- Web UI (2026-11-13): the What-If Lab uses `/api/ask` (plain-English box; settings are shown before running), `/api/ai/similar-dishes` ("suggest"), `/api/ai/dish-prior` ("AI estimate" in the new-dish form) and `/api/explain` ("Why?"). The Hall uses `/api/traces/{hash}/highlights` and `/api/narrate/...?stream=false` for students without a pre-built narration. Each one is disabled with a message when the API is down.
- Demo bundle: `web/public/cache/narration/<hash>_s<seed>.json` holds the highlight students' card and narration for **every exported trace** (8 date baselines + 5 Nov 13 presets). Rebuilt 2026-11-13: 156 narrations, all from Gemini (`narrate-v3`). The builder waits for the rate limit instead of falling back to the template. `manifest.llm` records the provider.

### 3.3 Housekeeping
- `dcsim cache llm-prune` deletes cached responses from old prompt versions and **all persona responses**. Last run deleted 15 rows: 10 persona batches and 5 old ask/explain/narration answers, including the "ate whatever I grabbed" narration.
- The cache now also holds Gemini answers (narration v3, ask v2, dish-prior v2, explain v3) and dish embeddings. `llm-prune` keeps `embed-v1` and would drop the old Groq v2/v1 rows (not run; the bundle files don't need them).

### 3.4 Tests (`engine/tests/test_ai.py`, 19 tests)
- A fake Gemini client checks request shape (roles, schema, thinking level), re-ask, caching, streaming, the 402 fallback and the provider default.
- A fake Groq HTTP server (httpx `MockTransport`) exercises the real SDK path: request shape, re-ask, streaming, caching, falling back on a 500.
- Narration check, template safety on 20 highlight students, the "invented dish" fallback.
- Mock persona set validity and diet-share matching, persona limits, `adopt` refusing an incomplete set.
- New-dish lever: conservation of portions in a real night.
- Every API route, using the mock.

---

## 4. Personas (paused)

**What happened (2026-11-12).** Groq `qwen/qwen3.8-27b`, 2 personas per call under the 1,000-token cap:
- Near-duplicates: "Late Hearty Eater" ×4, "Early Light Eater" ×4, "Late Slow Feaster" ×2, with ids like `_v2`, `_v3`, `_v4`.
- The vegetarian batch came back as broken JSON twice, and two empty personas were dropped. The set has no vegetarian persona, so it is marked `incomplete`.
- 3 personas per call ran out of tokens; at 2 per call the model repeated itself.

**Where things are now:**
- The rejected file is at `data/llm_drafts/rejected/2026-11-12_personas_raw_qwen3.8-27b.json`, outside the engine's inputs. Nothing reads it.
- Its cached responses are deleted, so a rerun can't replay them.
- `dcsim personas generate/adopt` refuse to run without `DCSIM_PERSONAS_LLM=1`.
- `data/worcester/personas.json` is still hand-written: 7 personas when this was written, 8 since engine 0.6.0 (a 2% `pescetarian` persona from the survey, ISSUES N8). `PersonaDraft.diet` accepts `pescetarian`.

**Contract for a replacement.** Whatever produces personas next (another model, a hand-curated list, survey-derived personas) only has to write a file that `personas.adopt()` accepts:

```jsonc
{
  "status": "uncalibrated",            // "incomplete" is refused
  "source": "...", "model": "...", "generated_at": "...",
  "personas": [{
    "id": "snake_case", "label": "short", "description": "one sentence (used in narration prompts)",
    "share": 0.12,                     // all shares sum to 1
    "diet": "none | vegetarian | vegan | halal",
    "patience": [[0, 0.1], [10, 0.6], [20, 0.3]],   // longest line they'd join, 0-40; probs sum to 1
    "plate_size": [[2, 0.5], [3, 0.5]],             // items on the first trip, 1-4
    "seconds_prob": 0.5,               // 0-0.9
    "dwell_median_min": 25,            // 5-90
    "category_affinity": {"entree": 1.4},           // item roles; 0.1-10; omit = 1.0
    "station_affinity": {"grill": 1.3}              // station ids; 0.1-10
  }]
}
```

Required: at least one vegetarian and one vegan persona. Their group shares are rescaled to the sourced 8.3% and 1.2% (`match_diet_shares`). Fields starting with `_` are kept for review and dropped on adopt. Adopting changes `personas.json`, so every cached result made with the old personas stops matching on its own.

---

## 5. What should be added or changed

### Must, before the demo
1. **Persona replacement.** Plug in the new approach behind the contract in §4. Then review, adopt and re-run `make demo-bundle`. If it is still LLM-based, it needs a model that can produce varied JSON, e.g. enable `openai/gpt-oss-120b` in Groq project settings, and the check should reject near-duplicate labels and ids ending in `_v2`, `_v3`.
2. **Rotate the Gemini and Groq keys** and keep the new one only in `engine/.env` or the host's secret store.
3. **Frontend wiring.**
   - Student card: render `card` instantly, append `sentence` events, then replace everything with `final.text` (it differs when `replaced: true`).
   - Offline: read `narration/*.json` from the manifest.
   - `/ask`: show `settings` and `unsupported`, let the manager edit, then `POST /api/simulate` with `request`.

### Should
4. ~~Rate limit inside the app~~ **Done:** live load gate (`DCSIM_LLM_MAX_LIVE`, `DCSIM_LLM_RPM`) plus a backup model on 503.
5. ~~Narration check, more facts~~ **Done** (`narrate-v3`): times and numbers.
6. ~~Dish-prior reasons~~ **Done** (`dish-prior-v2`): at most 8 words, display only, strong model.
7. ~~Explain runout windows~~ **Done** (`explain-v3`).
7b. **`/ask` contradiction check:** reject a plan that both sets a lever and lists the same request as unsupported, and add a "close a station" lever (remove every dish at it) if managers ask for it.
8. **One shared `LLM` per process.** The CLI builds its own today, which is fine. The bundle and API each hold one.
9. **Prompt files.** Move system prompts to `ai/prompts/*.md` with the version in the file, so prompt edits are reviewable on their own.

### Later (from ENGINE.md §7, not built)
10. **Survey adjustment** (§7.3): raking persona shares to survey totals, Bradley-Terry corrections per dish, a held-out check, and `POST /personas/calibrate`. This is independent of how personas are proposed.
11. **Uncertainty sweep** that actually widens result ranges for AI-estimated dishes (today only the popularity range is reported).
12. **Pre-warm narration** for a newly run scenario's median night in the background (like the trace warm-up in `api.py`).
13. **Provider interface.** If another provider comes back, implement `_parse` and `stream_text` for it in `llm.py`. Everything else (cache, checks, fallbacks) is provider-neutral.

### Engine issues found through narration (not LLM problems), both fixed in engine 0.4.1
- ~~Students who arrive a few minutes before the 9:00 pm close keep walking between stations after close, past empty pans (for example Student #15730 on Nov 13, night 11).~~ Late night (9 pm–12 am) is now modeled; dinner lines formed at 9 pm are served, serving stops at midnight (ISSUES E2).
- ~~Low-patience students may walk away from 15–20 lines in a row within about 4 minutes during the 7:30 rush.~~ Students now decide from where they stand and stay in the line they pick: at most a few skipped lines, none walked away from (ISSUES E1).
