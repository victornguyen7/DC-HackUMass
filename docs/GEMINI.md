# Gemini API

Everything about the Google Gemini API in this project: how it is set up, what the team's free-tier key can do (tested, not assumed), what the engine uses today, and what else is worth using.

Status as of 2026-11-13 (engine 0.6.1; nothing Gemini-specific changed since engine 0.3.4; the `/ask` prompt is `ask-v3` since 0.6.1, see LLM.md). Code: `engine/src/dcsim/ai/llm.py`. Job details and rules: [LLM.md](LLM.md).

---

## 1. Summary

- **Provider:** Gemini is the main LLM provider. Groq is kept as a fallback provider (`LLM_PROVIDER=groq`), and `mock` is used for tests and when there is no key.
- **Model:** `gemini-3.5-flash-lite`. It is the only fast text model that was reliably available on the free tier during testing (0.5–2.4 s per call).
- **Live jobs:** `/ask`, click narration and explain all run on Gemini and pass their checks. Personas stay paused.
- **Also free on this key and useful:** embeddings, function calling, image input, URL reading, token counting, and `gemini-4-hydrogen` (a slower thinking model).
- **Not on the free tier:** Google Search grounding, image generation, `gemini-4-carbon` / `-argon`, `gemini-omni-flash-preview`, context caching and the Batch API.

---

## 2. Setup in this project

| Item | Value |
| --- | --- |
| Key | `GEMINI_API_KEY` in `engine/.env` (file mode 600, in `.gitignore`). Free tier. Never in code or docs. **Rotate after the hackathon**: it was pasted into a chat. |
| SDK | `google-genai` 3.3.0 (`from google import genai`), added with `uv add google-genai`. |
| Provider choice | `gemini` if `GEMINI_API_KEY` is set, else `groq` if `GROQ_API_KEY` is set, else `mock`. Override with `LLM_PROVIDER`. |
| Model | `gemini-3.5-flash-lite`, overridable with `DCSIM_LLM_MODEL`. |
| Output cap | `DCSIM_LLM_MAX_OUTPUT`, default 8,000. Gemini counts thinking tokens against `max_output_tokens`, so medium-effort calls get 4,000 extra tokens. |
| Thinking | Low-effort jobs (narration, `/ask`) send `thinking_level: LOW`. Other jobs use the model's default. 2.x models would use `thinking_budget` instead. |
| Retries | 3 attempts, only on 408/429/500/502/503/504. A 402/403/404 goes straight to the job's fallback (about 1 s). |
| Cache | Every real answer goes into the `llm_cache` table, keyed by kind, prompt version, **model** and inputs. Switching models starts a fresh cache, and old Groq rows stay. |
| Tests | `tests/test_ai.py` uses a fake Gemini client (request shape, re-ask, cache, streaming, 402 fallback, provider default). Tests never call Google. |

**How the client calls Gemini (`llm.py`):**
- **Structured jobs:** `client.models.generate_content(model, contents, config)`, with:
  - `system_instruction` = the system prompt;
  - `response_mime_type="application/json"` and `response_json_schema` = the Pydantic model's JSON schema;
  - `temperature=0`;
  - the reply validated with Pydantic.
- **Re-ask:** one re-ask sends the earlier answer back as a `model` turn with the list of problems.
- **Narration:** `await client.aio.models.generate_content_stream(...)` at temperature 0.3, yielding `chunk.text`.
- **Errors:** `google.genai.errors.APIError` becomes `LLMError`. A `MAX_TOKENS` finish or an empty reply also becomes `LLMError`, and the caller falls back.

---

## 3. What the key can do (tested 2026-11-13)

One short call per model or feature, 25 s timeout, no retries. Free-tier availability changes from day to day, so treat 503s as "busy now", not "never".

### 3.1 Text models

| Model | Context in / out | Result | Notes |
| --- | --- | --- | --- |
| `gemini-3.5-flash-lite` | 1M / 65k | **OK**, 0.5–1.5 s | **In use.** JSON schema, streaming and thinking levels LOW/MEDIUM/HIGH all work (MINIMAL works here too). |
| `gemini-3.1-flash-lite` | 1M / 65k | **OK**, 1.4–2.4 s | Good backup if 3.5-flash-lite is busy. |
| `gemini-4-hydrogen` | 1M / 65k | **OK**, 1.4–4.5 s | Thinks by default (about 700 thinking tokens on a small task). Strongest model that answered. Candidate for offline jobs. |
| `gemini-3.8-flash` | 1M / 65k | 503 "high demand" most of the time | Rejects `thinking_level: MINIMAL` (400). One call succeeded earlier. |
| `gemini-3.7-flash` | 1M / 65k | 503 | |
| `gemini-3.6-flash` | 1M / 65k | 504 timeout | |
| `gemini-3.5-flash` | 1M / 65k | 403 PERMISSION_DENIED | Not enabled for this project. |
| `gemini-4-carbon`, `gemini-4-argon` | 1M / 32k | 429, **free-tier limit 0** | Paid only. |
| `gemini-omni-flash-preview` | 131k / 65k | 429, limit 0 | Paid only. |
| `gemini-2.5-flash`, `-flash-lite`, `-pro` | 1M / 65k | 404 "no longer available to new users" | Retired for new projects. |
| `gemma-4-31b-it`, `gemma-4-26b-a4b-it` | 262k / 32k | 500 INTERNAL | Open-weight models; failing on the API today. |
| `antigravity-preview-*`, `deep-research-*` | 1M / 65k | Not tested | Agent and research products, not a fit for this project. |

### 3.2 Other models

| Model | Result | Notes |
| --- | --- | --- |
| `gemini-embedding-001` | **OK**, 0.3 s | 3,072 dimensions by default; `output_dimensionality` can shrink it (256 tested). Accepts a list of texts in one call. `task_type="SEMANTIC_SIMILARITY"` is supported. |
| `gemini-embedding-2` | **OK**, one text per call | A list of texts is combined into **one** embedding (it is a multimodal model), so call it per item. 3,072 dimensions. |
| Image models (`gemini-3.1-flash-lite-image`, Nano Banana family) | 429, free-tier limit 0 | No image generation on this key. |

Sample embedding check: Teriyaki Salmon vs Teriyaki Tofu similarity 0.70; Teriyaki Salmon vs Blueberry Crisp 0.55. That gap is enough to rank similar dishes.

### 3.3 Features (on `gemini-3.5-flash-lite`)

| Feature | Result | Notes |
| --- | --- | --- |
| JSON-schema output | **OK** | Used by every structured job. |
| Streaming | **OK** | Used by narration. |
| Function calling | **OK**, 0.6 s | "Put 3 servers on the grill" → `set_servers(station="grill", servers=3)`. |
| Image input | **OK**, 2.2 s | Read a tiny test PNG correctly. Photos of menus would work the same way. |
| URL context (`url_context` tool) | **OK**, 1.8 s | Read the public Worcester menu page and summarized it. |
| Code execution | **OK**, 1.1 s | Runs Python on Google's side. Not needed: the engine does the math. |
| Token counting (`count_tokens`) | **OK**, 0.1 s, free | Useful for budgeting prompts. |
| Google Search grounding | 429, limit 0 | Paid only. |
| Context caching (`caches.create`) | 404 | Not available on this key. |
| Batch API (`batches.create`) | 400 FAILED_PRECONDITION | Needs billing. |
| Rate limits | Not published per key through the API | Watch for 429s. Current usage: ai.dev/rate-limit. |

---

## 4. What the engine uses today

| Job | Gemini features | Live result |
| --- | --- | --- |
| Plain English → levers (`/ask`, `dcsim ask`) | JSON schema, thinking LOW | Mapped "one more Grill server, Hot Pot batches in half, 15% busier" to exactly those three settings (2.6 s). |
| Click narration (`/api/narrate`, `dcsim narrate`) | Streaming, thinking LOW | 3 of 3 passed the fact check, about 1.5 s each. |
| Explain a comparison (`/api/explain`, `dcsim explain`) | JSON schema | Used only the numbers it was given; called the wait change inconclusive. |
| New-dish affinities (`/api/ai/dish-prior`) | JSON schema on `gemini-4-hydrogen` | 7.6 s; reasons within 8 words. |
| Similar dishes (`/api/ai/similar-dishes`, `dcsim similar`) | `gemini-embedding-001`, 256 dims, cached | "Smash Burger" → the two Grill burgers. |
| Backup model | `gemini-3.1-flash-lite` on 408/429/5xx/timeout | Tested with a fake 503; not yet seen live. |
| Load gate | Live clicks over 4 in flight or 15/min get the template | — |
| Personas | — | Paused (LLM.md §4). |

The demo bundle's narrations were rebuilt with Gemini on 2026-11-13: 156 narrations (12 highlight students for each of the 13 exported traces), none fell back to the template.

The web app calls these jobs from buttons when the API is up: the Lab's plain-English box (`/api/ask`), "suggest" and "AI estimate" in the new-dish form, and "Why?" (`/api/explain`); the Hall's student card (`/api/narrate`). Each click is one call, so the load gate above applies during a live demo.

---

## 5. What else is worth using

Ranked by value to the project. Every idea follows the LLM.md rules: never inside the simulation, a fallback for every job, a check before anyone sees the output, a cached answer, and AI-made values labeled "AI estimate".

### High value, free on this key

1. ✅ **Built.** **Embeddings to find similar dishes** (`gemini-embedding-001`).
   - Before, the new-dish lever required the manager to name `similar_to` dishes by hand. Now the Lab's new-dish form has a "suggest" link that fills them in.
   - Instead, embed every menu item once (48 dishes per night, one call), store the vectors next to the menu, and suggest the 3 nearest dishes for a new one.
   - The numbers stay deterministic: the borrowed popularity is still the geometric mean of the chosen dishes.
   - Fallback: same station and same category.
2. ◐ **Function built** (`similar.near_duplicates`), not wired in while personas are paused. **Embeddings to reject near-duplicate personas.** The paused persona run failed partly on repeats ("Late Hearty Eater" ×4). Whatever produces personas next, a check can embed `label + description` and refuse pairs above a cosine threshold, giving the "reject near-duplicates" rule in LLM.md §5.1 a concrete test. It works for hand-written and survey-derived personas too.
3. **Function calling for `/ask`.** Each lever (servers, batch size, pans, crowd, popularity, remove/add dish) becomes a declared function with typed arguments. The model's calls map one-to-one onto `Levers` fields, and an unknown station is rejected by the argument check. The JSON-schema version already works, so do this only if `/ask` misreads requests.
4. ✅ **Built for the dish prior** (`DCSIM_LLM_MODEL_STRONG`). **`gemini-4-hydrogen` for offline jobs** (dish prior, explain, any future persona job).
   - It answered on the free tier and gave distinct labels in a 5-persona test.
   - It is 2–3× slower than flash-lite, which is fine for jobs that run before the demo.
   - Set the model per job instead of globally; this needs a small `model=` option on `structured()`.
5. ✅ **Built.** **Fallback model chain.** On 503/504, retry once on `gemini-3.1-flash-lite` before using the template. The free tier's busy models are the main live-demo risk.

### Medium value

6. **Menu import from a photo or screenshot** (image input). A manager photographs the posted menu, and the model returns dishes as `MenuItem` JSON. The draft is checked against `stations.json` and reviewed before saving to `data/worcester/menus/`. Today the 8 menu files are entered by hand.
7. **Menu import from the dining website** (`url_context`). Same output and the same review step. The model reading a page is not an exact scraper, so the result is a draft to review, never used directly.
8. ◐ **Runout windows added** (explain-v3); the full cook sheet is not sent yet. **Long context for explain.** With a 1M-token context, explain can see the full cook sheet and runout windows, so it can say *when* a dish ran out (LLM.md §5.7). Check token counts with `count_tokens` first.
9. **Token budgeting.** Call `count_tokens` before large prompts, and serve the template instead of a call that would hit the output cap.

### Needs billing (not on the free tier)

| Feature | What it would give | Why wait |
| --- | --- | --- |
| Batch API | About half-price, asynchronous runs: rebuild all bundle narrations in one job | Free calls are enough at our volume. |
| Context caching | Cheaper repeat prompts that share a long prefix (menu + rules) | Prompts are short today. |
| Google Search grounding | Answers with web sources | Simulation facts must come from team inputs, not the web. |
| Image generation | Station or dish art for the frontend | Use hand-made or licensed art, and never present generated images as real food. |
| `gemini-4-carbon` / `-argon` | Larger models | `gemini-4-hydrogen` already answers on the free tier. |

### Not useful here
- **Code execution:** the engine already computes every number, and LLM-computed numbers are what the explain check exists to block.
- **Deep Research / Antigravity agents:** not a fit for a deterministic simulator.

---

## 6. Gotchas found while testing

- **Thinking levels differ by model.** `gemini-3.8-flash` rejects `MINIMAL` with a 400; `LOW` is accepted by every 3.x model tested. The client sends `LOW` for fast jobs.
- **Thinking tokens count as output.** A small `max_output_tokens` with thinking on can finish with `MAX_TOKENS` and an empty answer. The client adds 4,000 tokens of headroom for medium-effort jobs.
- **Busy models.** `3.6/3.7/3.8-flash` returned 503/504 on the free tier for most of the day, and a retry loop on a busy model can take tens of seconds. The client retries only 3 times.
- **`gemini-embedding-2` combines a list into one vector.** Send one text per call, or use `gemini-embedding-001` for lists.
- **The 2.5 models are retired** for new projects (404). Don't copy old examples that use them.
- **First import warning.** On Python 3.14 the SDK prints a `SyntaxWarning` from `google/genai/types.py` on first import. It is harmless and gone once Python has cached the compiled file.
- **The first key had no credit.** It returned 402 "prepayment credits are depleted" on every model. The current key is free tier.
- **Free-tier data use.** Google's terms let it use free-tier prompts and answers to improve its products. Send only menu data, synthetic data and simulated students, never real student information.

---

## 7. Suggested next steps

1. ~~Fallback model chain~~, ~~similar-dish embeddings~~, ~~dish prior on Gemini~~: done 2026-11-13.
2. Wire `near_duplicates` into whatever persona approach comes next.
3. Function calling for `/ask` if the contradiction seen live keeps happening (LLM.md §5.7b).
4. ~~Decide whether to rebuild the bundle narrations with Gemini~~: done 2026-11-13 (the committed bundle has 156 Gemini narrations). Bundle builds now time out stalled calls and print progress (ISSUES L5).
5. Rotate both API keys after the hackathon.
