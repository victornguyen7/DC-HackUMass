import { useId, useRef, useState, type FormEvent } from "react";
import { InsightsTabs, TopBar } from "../components/Shell";
import { dateLabel, fmt1, fmtInt } from "../lib/format";
import { useApiUp, useManifest } from "../lib/hooks";
import {
  classifyFailure, isoDay, percent, searchSimilarNights, spread, WEATHER_LABELS, WEATHERS,
  type SearchFailure, type SimilarMatch, type SimilarNightsParams, type SimilarNightsResult, type Weather,
} from "../lib/similarNights";
import { useApp } from "../lib/store";

const LIMITS = [3, 5, 10] as const;
const MAX_STUDENTS = 100_000;

type Status =
  | { state: "idle" }
  | { state: "loading"; params: SimilarNightsParams }
  | { state: "done"; params: SimilarNightsParams; result: SimilarNightsResult }
  | { state: "failed"; params: SimilarNightsParams; failure: SearchFailure };

/** Insights › Similar nights: recorded nights most like the one being planned (docs/SIMILAR_NIGHTS.md). */
export default function SimilarNights() {
  const appDate = useApp((s) => s.date);
  const manifest = useManifest();
  const apiUp = useApiUp();
  const menuDates = manifest.data?.dates ?? [];

  // Plan the night that's selected elsewhere in the app; fall back to tomorrow.
  const [date, setDate] = useState(() => appDate || isoDay(1));
  const [students, setStudents] = useState("4000");
  const [weather, setWeather] = useState<Weather>("rain");
  const [eventNight, setEventNight] = useState(false);
  const [menuDate, setMenuDate] = useState<string | null | undefined>(undefined); // undefined: not chosen yet
  const [limit, setLimit] = useState<number>(5);
  const [status, setStatus] = useState<Status>({ state: "idle" });
  const run = useRef(0);
  const resultsRef = useRef<HTMLElement>(null);
  const ids = useId();

  // Until the manager picks, use the selected night's own prepared menu when there is one.
  const menu = menuDate !== undefined ? menuDate : menuDates.includes(date) ? date : null;
  const studentsN = Number(students);
  const studentsError = !students.trim() || !Number.isInteger(studentsN) || studentsN < 1 || studentsN > MAX_STUDENTS
    ? `Enter a whole number from 1 to ${fmtInt(MAX_STUDENTS)}.` : null;
  const dateError = /^\d{4}-\d{2}-\d{2}$/.test(date) ? null : "Pick a date.";
  const loading = status.state === "loading";

  async function search(params: SimilarNightsParams) {
    const mine = ++run.current;
    setStatus({ state: "loading", params });
    try {
      const result = await searchSimilarNights(params);
      if (mine === run.current) setStatus({ state: "done", params, result });
    } catch (e) {
      if (mine === run.current) setStatus({ state: "failed", params, failure: classifyFailure(e) });
    }
    // Stacked layout (tablet/phone): the results start below the form, so bring them into view.
    if (mine === run.current && window.matchMedia?.("(max-width: 960px)").matches) {
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      requestAnimationFrame(() => resultsRef.current?.scrollIntoView?.({ behavior: reduce ? "auto" : "smooth", block: "start" }));
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (loading || studentsError || dateError) return;
    search({ date, students: studentsN, weather, eventNight, menuDate: menu, limit });
  }

  return (
    <>
      <TopBar title="Insights" sub="Find recorded nights that looked like the one you're planning" showBadges={false}
        dateControl={<span className="chip warn similar-provenance">Synthetic history</span>} />
      <div className="content insights-content similar-content">
        <InsightsTabs />
        <div className="similar-layout">
          <form className="card similar-form" onSubmit={onSubmit} noValidate aria-labelledby={`${ids}-title`}>
            <h2 id={`${ids}-title`}>Describe the night</h2>
            <p className="similar-lede">We'll rank every recorded night before this date by how alike the conditions were.</p>

            <div className="similar-field">
              <label htmlFor={`${ids}-date`}>Date</label>
              <input id={`${ids}-date`} type="date" value={date} required aria-invalid={!!dateError}
                onChange={(e) => setDate(e.target.value)} />
              {dateError && <p className="similar-field-error">{dateError}</p>}
            </div>

            <div className="similar-field">
              <label htmlFor={`${ids}-students`}>Expected students</label>
              <input id={`${ids}-students`} type="number" inputMode="numeric" min={1} max={MAX_STUDENTS} step={50}
                value={students} onChange={(e) => setStudents(e.target.value)} aria-invalid={!!studentsError}
                aria-describedby={`${ids}-students-hint`} />
              <p id={`${ids}-students-hint`} className={studentsError ? "similar-field-error" : "similar-field-hint"}>
                {studentsError ?? "A typical Worcester dinner is 3,000–4,500."}
              </p>
            </div>

            <fieldset className="similar-field">
              <legend>Weather</legend>
              <div className="similar-choices">
                {WEATHERS.map((w) => (
                  <label key={w} className="similar-choice" title={w === "other" ? "Weather is left out of the match" : undefined}>
                    <input type="radio" name={`${ids}-weather`} value={w} checked={weather === w} onChange={() => setWeather(w)} />
                    <span>{WEATHER_LABELS[w]}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <label className="similar-toggle">
              <input type="checkbox" checked={eventNight} onChange={(e) => setEventNight(e.target.checked)} />
              <span className="similar-switch" aria-hidden />
              <span><b>Event night</b><small>Theme dinner, game day, or a campus event</small></span>
            </label>

            <div className="similar-field">
              <label htmlFor={`${ids}-menu`}>Menu</label>
              <select id={`${ids}-menu`} value={menu ?? ""} onChange={(e) => setMenuDate(e.target.value || null)}
                aria-describedby={`${ids}-menu-hint`}>
                <option value="">Unknown menu</option>
                {menuDates.map((d) => <option key={d} value={d}>Prepared menu · {dateLabel(d)}</option>)}
              </select>
              <p id={`${ids}-menu-hint`} className="similar-field-hint">
                {menu ? "Dishes count toward the match, and each result shows how much of the menu it shared." : "Matches on conditions only."}
              </p>
            </div>

            <fieldset className="similar-field">
              <legend>Show</legend>
              <div className="seg" role="group" aria-label="Number of results">
                {LIMITS.map((n) => (
                  <button key={n} type="button" aria-pressed={limit === n} onClick={() => setLimit(n)}>{n} nights</button>
                ))}
              </div>
            </fieldset>

            <button className="btn primary similar-submit" type="submit" disabled={loading}>
              {loading ? <><span className="similar-spinner" aria-hidden />Searching…</> : "Find similar nights"}
            </button>
            {!apiUp && status.state === "idle" && (
              <p className="similar-field-hint similar-offline-hint">
                Search runs on the live engine. If it isn't connected, you'll see a retry option.
              </p>
            )}
          </form>

          <section ref={resultsRef} className="similar-results" aria-live="polite" aria-busy={loading} aria-label="Search results">
            <Results status={status} retry={() => status.state === "failed" && search(status.params)} />
          </section>
        </div>
      </div>
    </>
  );
}

function Results({ status, retry }: { status: Status; retry: () => void }) {
  if (status.state === "idle") {
    return (
      <div className="card similar-empty">
        <svg width="40" height="40" viewBox="0 0 40 40" fill="none" aria-hidden>
          <rect x="5" y="8" width="30" height="26" rx="4" stroke="currentColor" strokeWidth="1.6" />
          <path d="M5 15h30M13 5v6M27 5v6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          <circle cx="17" cy="24" r="4" stroke="currentColor" strokeWidth="1.6" /><path d="M20 27l4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <h2>Which past nights looked like this one?</h2>
        <p>Describe the night, then search. You'll see the closest recorded nights, most similar first, with the waste and stockouts each one actually had.</p>
      </div>
    );
  }
  if (status.state === "loading") {
    return (
      <div className="similar-skeleton" role="status">
        <span className="sr-only">Searching recorded nights…</span>
        {[0, 1, 2].map((i) => <div key={i} className="card similar-skeleton-row" />)}
      </div>
    );
  }
  if (status.state === "failed") return <Failure failure={status.failure} retry={retry} />;

  const { result } = status;
  const q = result.query;
  return (
    <>
      <div className="card similar-summary">
        <p className="overline">Most like</p>
        <h2>{dateLabel(q.date, { weekday: "long", month: "short", day: "numeric" })}</h2>
        <p className="similar-conditions">
          {WEATHER_LABELS[q.weather] ?? q.weather} · {fmtInt(q.students)} students
          {q.event_night ? " · Event night" : ""} · {q.menu_date ? `Menu of ${dateLabel(q.menu_date)}` : "Menu unknown"}
        </p>
        <div className="row similar-meta">
          <span className="chip warn">Synthetic history</span>
          <span className="chip info">{result.source === "tiger-pgvector" ? "Searched in Tiger · pgvector" : "Searched local history files"}</span>
          <span className="faint">{result.matches.length} of {fmtInt(result.history_count)} recorded nights shown</span>
        </div>
        {result.matches.length > 0 && <Outcomes matches={result.matches} date={q.date} />}
      </div>

      {result.matches.length === 0 ? (
        <div className="card similar-empty">
          <h2>No recorded nights before {dateLabel(q.date)}</h2>
          <p>Similar nights only compares against nights that happened before the date you picked. Try a later date.</p>
        </div>
      ) : (
        <ol className="similar-list" aria-label="Similar nights, most similar first">
          {result.matches.map((m, i) => <MatchRow key={m.date} m={m} rank={i + 1} />)}
        </ol>
      )}

      <details className="similar-method">
        <summary>How matching works</summary>
        <p>{result.method}</p>
        <p>{result.note}</p>
        <p>The percentage is a similarity score for the conditions, not the chance that tonight turns out the same.</p>
      </details>
    </>
  );
}

function Outcomes({ matches, date }: { matches: SimilarMatch[]; date: string }) {
  const waste = spread(matches, "waste_lb");
  const stock = spread(matches, "stockout_min");
  const people = spread(matches, "students");
  const cell = (label: string, s: ReturnType<typeof spread>, f: (n: number) => string, unit: string) => (
    <div className="similar-outcome">
      <span className="similar-outcome-label">{label}</span>
      {s ? <>
        <strong className="tnum">{f(s.median)}<small> {unit}</small></strong>
        <span className="similar-outcome-range tnum">{s.n > 1 ? `${f(s.min)}–${f(s.max)} across ${s.n} nights` : "1 night"}</span>
      </> : <strong>—</strong>}
    </div>
  );
  return (
    <>
      <div className="similar-outcomes">
        {cell("Waste, median", waste, fmt1, "lb")}
        {cell("Stockouts, median", stock, fmt1, "min")}
        {cell("Students, median", people, fmtInt, "")}
      </div>
      <p className="similar-caption">What happened on these recorded nights. It isn't a forecast for {dateLabel(date)}.</p>
    </>
  );
}

function MatchRow({ m, rank }: { m: SimilarMatch; rank: number }) {
  return (
    <li className="card similar-match">
      <span className="similar-rank tnum" aria-hidden>{rank}</span>
      <div className="similar-match-main">
        <h3>{dateLabel(m.date, { weekday: "long", month: "short", day: "numeric" })}</h3>
        <ul className="similar-tags" aria-label="How it compares">
          <li className={m.same_weekday ? "chip good" : "chip"}>{m.same_weekday ? <Check /> : null}{m.same_weekday ? "Same weekday" : m.weekday}</li>
          <li className={m.same_weather ? "chip good" : "chip"}>{m.same_weather ? <Check /> : null}{WEATHER_LABELS[m.weather] ?? m.weather}{m.same_weather ? ", same weather" : ""}</li>
          {m.event_night && <li className="chip maroon">Event night</li>}
          {m.menu_overlap !== null && <li className="chip info">Menu overlap {percent(m.menu_overlap)}</li>}
        </ul>
      </div>
      <div className="similar-score">
        <span className="similar-score-value tnum">{percent(m.similarity)}</span>
        <span className="similar-score-label">similar</span>
        <span className="bar-track" aria-hidden><span className="bar-fill" style={{ width: percent(Math.max(0, m.similarity)) }} /></span>
      </div>
      <dl className="similar-facts">
        <div><dt>Students</dt><dd className="tnum">{fmtInt(m.students)}</dd></div>
        <div><dt>Waste</dt><dd className="tnum">{m.waste_lb === null ? "—" : `${fmt1(m.waste_lb)} lb`}</dd></div>
        <div><dt>Stockouts</dt><dd className="tnum">{m.stockout_min === null ? "—" : `${fmt1(m.stockout_min)} min`}</dd></div>
      </dl>
    </li>
  );
}

function Failure({ failure, retry }: { failure: SearchFailure; retry: () => void }) {
  if (failure.kind === "invalid") {
    return (
      <div className="alert bad similar-alert" role="alert">
        <b>Check the details of the night</b>{failure.message}
      </div>
    );
  }
  const title = failure.kind === "history" ? "Historical search is temporarily unavailable" : "Can't reach the search service";
  const body = failure.kind === "history"
    ? "The history database answered but the search didn't finish. Your inputs are kept, so you can try again."
    : `${failure.message} Similar nights needs the live engine, which may still be starting. Your inputs are kept.`;
  return (
    <div className="alert warn similar-alert" role="alert">
      <b>{title}</b>
      <p>{body}</p>
      <button className="btn" type="button" onClick={retry}>Try again</button>
    </div>
  );
}

const Check = () => (
  <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden>
    <path d="M2.5 6.5l2.2 2.2L9.5 3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
