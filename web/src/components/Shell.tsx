import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
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
  const links = [
    ["/hall", "Hall", "hall"],
    ["/dashboard", "Dashboard", "dashboard"],
    ["/waste", "Waste Record", "waste"],
    ["/lab", "What-If Lab", "lab"],
  ];
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-logo">DC</div>
        <div>
          <b>DC What-If</b>
          <span>Worcester Commons</span>
        </div>
      </div>
      <div className="overline">Pages</div>
      <nav className="nav" aria-label="Pages">
        {links.map(([to, label, icon]) => (
          <NavLink key={to} to={to} title={label}>
            <Icon name={icon} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="sources">
        <div className="overline">Data sources</div>
        <ul>
          <li><i className="dot" style={{ background: "var(--good)" }} />Menu · umassdining.com snapshot</li>
          <li><i className="dot" style={{ background: "var(--warn)" }} />Arrivals · model, no swipes yet</li>
          <li><i className="dot" style={{ background: "var(--warn)" }} />Waste · synthetic (MetaFoodX-shaped)</li>
          <li><i className="dot" style={{ background: "var(--warn)" }} />Personas · uncalibrated</li>
        </ul>
      </div>
    </aside>
  );
}

type TopBarProps = {
  title: string;
  sub: string;
  scenario?: Scenario;
  scenarioPrefix?: string;
  dateControl?: ReactNode;
};

/** Global top bar (SPEC §2): date picker · meal · active scenario chip · synthetic badge. */
export function TopBar({ title, sub, scenario, scenarioPrefix = "Scenario", dateControl }: TopBarProps) {
  const { date, setDate } = useApp();
  const m = useManifest();
  const dates = useAsync(loadDates, []);
  return (
    <header className="topbar">
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
      <div className="seg" role="group" aria-label="Meal">
        <button disabled title="Breakfast is not simulated yet (SPEC §10: stretch)">Breakfast</button>
        <button disabled title="Lunch is not simulated yet (SPEC §10: stretch)">Lunch</button>
        <button aria-pressed="true">Dinner</button>
      </div>
      {scenario && (
        <span className="chip maroon scenario-chip" style={{ padding: "6px 10px", borderRadius: 8 }}
          title={`${scenarioPrefix}: ${scenarioLabel(scenario.preset, scenario.lever, scenario.label)}`}>
          {scenarioPrefix} <b style={{ fontWeight: 600 }}>{scenarioLabel(scenario.preset, scenario.lever, scenario.label)}</b>
        </span>
      )}
      <span className="chip warn" style={{ padding: "6px 10px", borderRadius: 8 }}>Synthetic waste data</span>
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
