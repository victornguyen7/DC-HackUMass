# Similar Nights frontend handoff

Updated November 13, 2026.

Similar Nights finds historical dining nights with conditions similar to a target night. Managers enter attendance, weather, event status, and optionally a prepared menu. Results show similarity and recorded waste and stockout outcomes. Current historical observations are synthetic.

**Status (Nov 13, 2026, evening):** implemented on `main`. The page follows the redesign's visual system ([FRONTEND_REDESIGN_PLAN.md](FRONTEND_REDESIGN_PLAN.md) "Visual system") and lives under **Insights** as a second tab next to Waste history, so the top navigation keeps its three destinations. Open `/similar-nights`, or Insights › Similar nights.

## Implementation map

| File | Responsibility |
| --- | --- |
| `engine/src/dcsim/similar_nights.py` | Input validation, feature vectors, history loading, ranking, response |
| `engine/src/dcsim/api.py` | GET endpoint and HTTP error mapping |
| `engine/src/dcsim/tsdb.py` | Tiger connection and historical tables |
| `web/src/pages/SimilarNights.tsx` | Form, loading / empty / error states, summary, ranked match cards |
| `web/src/lib/similarNights.ts` | Shared request/response types, query builder, error classification, outcome spread |
| `web/src/lib/data.ts` | Shared `api<T>()` client and `ApiError` |
| `web/src/lib/hooks.ts` | `useManifest()` for prepared menu dates |
| `web/src/main.tsx` | Route `/similar-nights` |
| `web/src/components/Shell.tsx` | `InsightsTabs` (Waste history / Similar nights); Insights stays active on `/similar-nights` |
| `web/src/pages/Waste.tsx` | Shows the same `InsightsTabs` |
| `web/src/similar-nights.css` | `similar-*` and `insights-tabs` styles |
| `web/src/pages/SimilarNights.test.tsx` | UI states: defaults, unknown/selected menu, empty, 422, API down + retry, history 503 |
| `engine/tests/test_similar_nights.py` | Local API regression coverage |

## API contract

Send `GET /api/similar-nights` with query parameters.

| Parameter | Type | Required/default | Meaning |
| --- | --- | --- | --- |
| `date` | ISO date string | Required | Target date, use YYYY-MM-DD |
| `students` | Integer | Required | Expected attendance, 1–100000 |
| `weather` | String | Default clear | clear, rain, snow, cloudy, windy, other |
| `event_night` | Boolean | Default false | Send true or false |
| `menu_date` | ISO date string | Optional | Prepared menu snapshot date |
| `limit` | Integer | Default 5 | Maximum matches, 1–20 |

Weather is trimmed and lowercased by the backend. Target dates can be outside the prepared-menu calendar. A supplied menu date must exist in that calendar.

For Unknown menu, omit `menu_date` entirely. An empty string fails validation. Omission skips menu scoring and returns null overlap. The backend never infers a menu from the target date.

Use the bundle manifest's `dates` array, as the current page does, or `GET /api/dates` to populate the menu selector.

### Frontend request example

```ts
import { api } from "../lib/data";

const params = new URLSearchParams({
  date: targetDate,
  students: String(expectedStudents),
  weather,
  event_night: String(eventNight),
  limit: "5",
});
if (menuDate) params.set("menu_date", menuDate);
const result = await api<SearchResult>(`similar-nights?${params}`);
```

The shared client prefixes /api/ and throws ApiError on unsuccessful responses. The types (`SimilarNightsResult`, `SimilarMatch`) and the request helper `searchSimilarNights()` live in `web/src/lib/similarNights.ts`.

### Response shape

```ts
type Match = {
  date: string;
  similarity: number;
  students: number;
  weather: string;
  event_night: boolean;
  weekday: string;
  waste_lb: number | null;
  stockout_min: number | null;
  menu_overlap: number | null;
  same_weekday: boolean;
  same_weather: boolean;
};

type SearchResult = {
  query: {
    date: string;
    weekday: string;
    students: number;
    weather: string;
    event_night: boolean;
    menu_date: string | null;
  };
  matches: Match[];
  source: "tiger-pgvector" | "local-synthetic";
  synthetic: boolean;
  history_count: number;
  method: string;
  note: string;
};
```

| Field | Rendering and meaning |
| --- | --- |
| similarity | Cosine feature similarity, rounded to four decimals. Multiply by 100 for a match percentage. This measures similarity of conditions. |
| menu_overlap | Exact dish-ID intersection divided by union, rounded to three decimals. Hide the overlap label when null. Zero is a valid overlap. |
| waste_lb | Recorded waste in pounds, rounded to one decimal. Render a placeholder for null. |
| stockout_min | Recorded aggregate stockout minutes, rounded to one decimal. Render a placeholder for null. |
| history_count | All loaded historical nights, including dates excluded by the target-date filter |
| matches | Ranked results, potentially fewer than limit. Empty is a successful search with no eligible history. |
| query | Inputs actually used by the completed request. Use these for result headings if form inputs change. |
| source | Actual storage/search path used |
| synthetic | Currently always true on both paths |
| method, note | Explanatory metadata supplied by the backend |

Only nights strictly earlier than the target date are eligible. Results sort by descending similarity, then ascending date for equal scores.

### Errors and UI states

| State | Handling |
| --- | --- |
| Initial | Show editable inputs |
| Loading | Indicate progress and prevent duplicate submissions |
| Success | Render matches in returned order and show synthetic provenance |
| Empty | Explain that no recorded nights precede the submitted target date |
| HTTP 422 | Show input validation feedback. FastAPI detail may be a string or an array. The shared client handles both. |
| HTTP 503 | Historical search failed. Backend detail is Historical search is temporarily unavailable. |
| Network/proxy failure | Show an API availability error with retry |

The page clears old results on submit, disables the search button while loading (skeleton rows, `aria-busy`), and keeps the inputs on failure so **Try again** repeats the same request. Defaults: the date selected elsewhere in the app (else tomorrow), 4000 students, rain, not an event night, that date's prepared menu when one exists (else Unknown menu; the manager can switch to Unknown), and five results. The weather choice "Other" covers recorded "cold" nights. The 503 detail above is what tells "history failed" apart from "API not running" (the dev proxy also answers 503, with a different detail). User-facing errors never show terminal commands. On tablet and phone widths the page scrolls to the results after a search.

The summary card shows the median and range of waste, stockouts and students across the matches, captioned as recorded outcomes, not a forecast for the target date.

Keep synthetic provenance visible and describe the percentage as a similarity score. Waste and stockout values belong to the matched historical nights. Forecast confidence and predicted outcomes for the target date are separate quantities.

## Search implementation

Each night becomes a normalized 48-dimensional vector.

| Zero-based dimensions | Feature |
| --- | --- |
| 0–6 | Weekday one-hot, weight 1.7 |
| 7–13 | Neighboring weekday slots, each weight 0.25 |
| 14–19 | Weather one-hot, weight 1.8 |
| 20–21 | Event false/true one-hot, weight 0.9 |
| 22–23 | Attendance features, weight 1.7 with clipping |
| 24–47 | Dish IDs hashed with SHA-256 into 24 buckets |

Attendance components are 1.7 times clamp(students / 1500, 0, 2.5) and 1.7 times clamp((2600 - students) / 1500, 0, 2.5). Counts at or above 3750 therefore share the same attendance components. Each dish contributes 1 / sqrt(menu size) to its bucket. Hash collisions can affect similarity. Displayed menu overlap uses exact IDs.

When the query menu is unknown, local scoring omits candidate dishes as well. Tiger compares the first 24 dimensions using pgvector subvector and cosine distance. This prevents candidate menu size from affecting an unknown-menu query.

Waste and stockout outcomes are attached after ranking. They do not enter the feature vector.

### History and persistence

The service first attempts a Tiger connection through tsdb.connect(). A connection failure uses local synthetic files. A failure after connecting, including missing schema or vector support, returns HTTP 503.

Local input files live in data/synthetic/worcester.

- nights.csv supplies attendance and outcomes.
- consumption.csv supplies historical dish IDs.
- _ground_truth.json supplies observable weather and event flags.

Tiger reads dcsim.nights and dcsim.pan_windows with both source and scenario equal to observed. Each search creates the vector extension and dcsim.night_embeddings table (`night` primary key, `embedding vector(48)`, `menu_date`, `updated_at`) if needed, upserts current embeddings, and commits. It requires the corresponding database permissions. Search uses exact cosine ordering and filters embeddings against currently observed nights, so stale embeddings are excluded.

This implementation refreshes all historical embeddings on each request. Live database search was verified against the configured Tiger service. Concurrency and scale remain verification work. Local fallback still requires the Python API. Similar Nights has no precomputed browser-only search bundle.

## Run and verify

From the repository root, start the API and web server in separate terminals.

```sh
make serve
make web
```

Open /similar-nights on the web dev server.

```sh
curl 'http://127.0.0.1:8000/api/similar-nights?date=2026-11-14&students=4000&weather=rain'
curl 'http://127.0.0.1:8000/api/similar-nights?date=2026-11-14&students=4000&weather=rain&menu_date=2026-11-13'
cd engine
.venv/bin/python -m pytest tests/test_similar_nights.py -q
```

Run npm run build from web/ to check TypeScript and production bundling.

Verified on `main`, November 13, 2026 (evening):

- `engine/tests/test_similar_nights.py`: 21 passed (vector layout, ranking and ties, past-night filter, unknown vs selected menu, 11 invalid-input cases, empty history, Tiger failure → 503). Full engine suite: 149 passed, 2 skipped.
- Web: 46 tests passed (9 for Similar Nights), `tsc -b` and `npm run build` clean.
- Live Tiger: the pgvector ranking is identical to the local ranking on the same history (max difference 0.0 over 3 queries). Requests take about 0.4–0.8 s.
- Browser (Chrome via Playwright, live API + Tiger): 1440 px and 390 px widths, no horizontal scroll, no console errors.

Earlier verification (teammate's version, before it was rebuilt on `main`):

- Local API regression passed, covering unknown and selected menus, ranking order, date filtering, invalid inputs, and empty history results.
- Frontend production build passed.
- Git diff whitespace check passed.

A live Tiger/pgvector request returned five matches after the data upload. Existing general test suites passed before these fixes. The focused API test and web build were run after them.

## Integrating the teammate's UI

1. Read the teammate's components and layout when available and use those as the visual implementation.
2. Connect the form to the GET contract above through the existing API client.
3. Map returned fields to the teammate's result components, preserving null handling, units, ordering, and synthetic labels.
4. Keep menu selection explicit and use the response query for completed-search context.
5. Verify selected menu, unknown menu, invalid inputs, empty results, API unavailable, and successful results.
6. Check desktop and mobile layout and then update this document with the final component paths and verification results.

Done: the page uses the redesign's tokens (burgundy primary action, amber "Synthetic history" label, green paired with text, tabular numbers, 8 px rhythm) and sits under Insights. Still open: a moderated check with a dining manager, and keyboard-only and screen-reader passes beyond the automated checks.

## Changes relative to main

Comparison baseline is commit 0ba140e, which includes the frontend redesign. The original implementation stayed local and uncommitted; PR #3 published only this handoff. The feature was then rebuilt on `main` from this contract (same 48-dim vector, API, and Tiger table).

| Area | Feature additions relative to the baseline |
| --- | --- |
| Backend | New similar_nights.py search module and GET /api/similar-nights route |
| Search | Deterministic 48-dimensional cosine ranking, past-night filtering, optional menu matching, Tiger search and local synthetic fallback |
| Correctness | Unknown menu excludes dish dimensions and returns null overlap, current observed-night filter excludes stale embeddings |
| Frontend | New SimilarNights.tsx page, /similar-nights route, top-navigation link, and feature styles |
| Validation | API regression coverage for selected and unknown menus, invalid inputs, ordering, and empty results |
| Documentation | This handoff plus README and Tiger feature-status links |

After integrating with the redesigned navigation, the web production build, all 29 frontend tests, and the focused Similar Nights API test passed.

### Uploaded Tiger data

The upload to service ht1fm93al5 verified 131 project data files by checksum in dcsim.project_data_files. Original file bytes are in content, relative paths in path, and parsed JSON in json_content. The upload also loaded 42 historical nights, 19,027 pan-window records, 10,459 waste records, and 512 batch plans using the existing dining schema.

The subsequent main-branch update changed local synthetic data and configuration. Those later changes have not been reuploaded, so these counts describe the verified upload snapshot. A new data synchronization should be explicit when needed.

**Stale Tiger copy (Nov 13):** 24 of the 43 observed nights in Tiger differ from `data/synthetic/worcester` (students or dishes), so Tiger and local results differ slightly; the algorithm itself matches exactly. Reload the history into Tiger (`dcsim tsdb load`, or the team's upload script) when you want them to agree.

**Not the same as `dcsim tsdb similar`:** that CLI command (`nightsearch.py`, TIGER.md §7) is a separate 76-dim search with a held-out accuracy check, stored in `dcsim.night_vectors`. The two don't share tables.
