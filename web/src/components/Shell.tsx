import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useAsync, useManifest } from "../lib/hooks";
import { loadDates } from "../lib/data";
import { dateLabel, scenarioLabel } from "../lib/format";
import { useApp, type Scenario } from "../lib/store";

const icons: Record<string, ReactNode> = {
  hall: <path d="M2 4l4-2 4 2 4-2v10l-4 2-4-2-4 2zM6 2v10M10 4v10" />,
  dashboard: <path d="M2 2h5v5H2zM9 2h5v5H9zM2 9h5v5H2zM9 9h5v5H9z" />,
  waste: <path d="M3 4h10M6 4V2.5h4V4M4.5 4l.7 9.5h5.6l.7-9.5" />,
  lab: <path d="M6 2h4M7 2v4l-4 7.5h10L9 6V2" />,
};
const Icon = ({ name }: { name: string }) => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" aria-hidden>
    {icons[name]}
  </svg>
);

export function Sidebar() {
  const location = useLocation();
  return (
    <header className="app-navigation no-print">
      <NavLink to="/hall" className="brand" aria-label="DC What-If home"><div className="brand-logo">DC</div><div><b>What-if</b><span>Worcester Commons</span></div></NavLink>
      <nav className="app-links" aria-label="Pages">
        <NavLink to="/hall" className={({ isActive }) => isActive || location.pathname === "/lab" ? "active" : ""}><Icon name="hall" /><span>Simulation</span></NavLink>
        <NavLink to="/dashboard"><Icon name="dashboard" /><span>Kitchen plan</span></NavLink>
        <NavLink to="/waste" className={({ isActive }) => isActive || location.pathname === "/similar-nights" ? "active" : ""}><Icon name="waste" /><span>Insights</span></NavLink>
      </nav>
      <details className="model-information"><summary>Model information</summary><div>
        <h3>Know what the model knows</h3>
        <p><b>Menu:</b> real umassdining.com snapshot.</p><p><b>Arrivals:</b> modeled; no swipe history connected.</p>
        <p><b>Waste history:</b> synthetic, not measured Worcester waste.</p><p><b>Personas:</b> uncalibrated assumptions.</p>
        <p>The replay shows one representative night. Comparison ranges describe variation across simulated nights.</p>
      </div></details>
    </header>
  );
}

/** Insights sections: recorded waste history and the similar-night search. */
export function InsightsTabs() {
  return (
    <nav className="insights-tabs no-print" aria-label="Insights sections">
      <NavLink to="/waste" end>Waste history</NavLink>
      <NavLink to="/similar-nights">Similar nights</NavLink>
      {/* Standalone page (web/public/tiger.html): reads Tiger's continuous aggregates, snapshot when offline. */}
      <a href="/tiger.html">Live data</a>
    </nav>
  );
}

type TopBarProps = {
  title: string;
  sub: string;
  scenario?: Scenario;
  scenarioPrefix?: string;
  dateControl?: ReactNode;
  showBadges?: boolean;
};

/** Global top bar (SPEC §2): date picker · meal · active scenario chip · synthetic badge. */
export function TopBar({ title, sub, scenario, scenarioPrefix = "Scenario", dateControl, showBadges = true }: TopBarProps) {
  const { date, setDate } = useApp();
  const m = useManifest();
  const dates = useAsync(loadDates, []);
  return (
    <header className={`topbar${title === "Simulation" ? " simulation-topbar" : ""}`}>
      <div className="title">
        <h1>{title}</h1>
        <div className="sub">{sub}</div>
      </div>
      {dateControl ?? (
        <label>
          <span className="sr-only" style={{ position: "absolute", left: -9999 }}>Date</span>
          <select className="btn" value={date} onChange={(e) => setDate(e.target.value)} aria-label="Date">
            {(m.data?.dates ?? [date]).map((d) => {
              const info = dates.data?.find((x) => x.date === d);
              const ev = info?.events_at_or_affecting_worcester?.[0]?.name;
              return (
                <option key={d} value={d}>
                  {dateLabel(d)}{ev ? ` · ${ev}` : ""}
                </option>
              );
            })}
          </select>
        </label>
      )}

      {showBadges && scenario && (
        <span className="chip maroon scenario-chip" style={{ padding: "6px 10px", borderRadius: 8 }}
          title={`${scenarioPrefix}: ${scenarioLabel(scenario.preset, scenario.lever, scenario.label)}`}>
          {scenarioPrefix} <b style={{ fontWeight: 600 }}>{scenarioLabel(scenario.preset, scenario.lever, scenario.label)}</b>
        </span>
      )}
      {showBadges && <span className="chip warn" style={{ padding: "6px 10px", borderRadius: 8 }}>{title === "Simulation" ? "Simulated replay" : "Synthetic waste data"}</span>}
    </header>
  );
}

export function Loading({ what = "data" }: { what?: string }) {
  return <div className="empty" role="status">Loading {what}…</div>;
}

export function ErrorBox({ error }: { error: Error }) {
  return (
    <div className="error" role="alert">
      Couldn't load: {error.message}. Run <code>make demo-bundle</code> to rebuild <code>web/public/cache</code>.
    </div>
  );
}
