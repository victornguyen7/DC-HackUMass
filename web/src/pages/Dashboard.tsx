import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bars } from "../components/Charts";
import { ErrorBox, Loading, TopBar } from "../components/Shell";
import { loadDates, loadMenu } from "../lib/data";
import { clock, dateLabel, downloadCSV, fmt1, fmtInt, groupFlags, rangeText, stationName } from "../lib/format";
import { useAsync, useScenarioResult } from "../lib/hooks";
import { BASELINE, useApp } from "../lib/store";
import type { CookRow, Result } from "../lib/types";

const DIET_BADGE: Record<string, [string, string]> = { halal: ["H", "warn"], vegan: ["VG", "good"], vegetarian: ["V", "info"] };

export function DietTags({ tags }: { tags: string[] }) {
  // vegan implies vegetarian; show the strongest tag only
  const shown = tags.filter((t) => DIET_BADGE[t] && !(t === "vegetarian" && tags.includes("vegan")));
  return (
    <>
      {shown.map((t) => (
        <span key={t} className={`chip ${DIET_BADGE[t][1]}`} style={{ padding: "0 5px", fontSize: 10 }} title={t}>{DIET_BADGE[t][0]}</span>
      ))}
    </>
  );
}

export default function Dashboard() {
  const { date, adopted, setHallTime, setScenario } = useApp();
  const sc = adopted[date] ?? BASELINE;
  const res = useScenarioResult(date, sc);
  const nav = useNavigate();
  const dates = useAsync(loadDates, []);
  const menu = useAsync(() => loadMenu(date), [date]);
  const info = dates.data?.find((d) => d.date === date);
  const r = res.data?.result;

  return (
    <>
      <TopBar title="Dashboard" sub={`Plan for tonight · ${dateLabel(date, { weekday: "short", month: "short", day: "numeric" })}`}
        scenario={sc} scenarioPrefix={adopted[date] ? "Adopted" : "Scenario"} />
      <div className="content">
        {res.error && <ErrorBox error={res.error} />}
        {res.data?.fallback && <div className="banner">{res.data.reason}</div>}
        {res.loading && !r && <Loading />}
        {r && (
          <>
            <div className="row" style={{ fontSize: 12 }}>
              <span className="chip">Dinner {clock(r.open)}–{clock(r.close)}</span>
              <span className="chip">{info?.weekday ?? ""}{info?.weekend ? " · weekend" : ""}</span>
              {info?.events_at_or_affecting_worcester.map((e) => <span key={e.name} className="chip info">{e.name}</span>)}
              <span className="chip good">Menu: snapshot ({r.cook_sheet.length} dishes{menu.data?.addons ? `, ${menu.data.addons.length} add-ons` : ""})</span>
              <span className="chip warn">Arrivals: modeled, no swipes yet</span>
              <span className="chip">Weather: not connected</span>
              {r.population_status !== "calibrated" && <span className="chip warn">Personas {r.population_status}</span>}
              <span className="spacer" />
              <Link to="/hall" className="btn ghost" onClick={() => setScenario(sc)}>View in Hall →</Link>
            </div>
            <Kpis r={r} />
            <div className="grid-2">
              <ArrivalsCard r={r} onPick={(t) => { setScenario(sc); setHallTime(t); nav("/hall"); }} />
              <Alerts r={r} />
            </div>
            <MenuByStation r={r} />
            <div className="grid-2">
              <ProductionPlan r={r} date={date} />
              <div className="stack">
                <Staffing r={r} />
                <LeftAtClose r={r} />
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );
}

export function peakWindow(r: Result) {
  return r.arrivals.reduce((a, b) => (b.expected > a.expected ? b : a), r.arrivals[0]);
}

function Kpis({ r }: { r: Result }) {
  const h = r.headline;
  const peak = peakWindow(r);
  const batch = r.cook_sheet.filter((d) => !d.to_order);
  const prep = r.cook_sheet.reduce((s, d) => s + d.portions_to_prepare.p50, 0);
  const cooked = batch.reduce((s, d) => s + d.portions_to_prepare.p50, 0);
  const atRisk = r.cook_sheet.filter((d) => d.runout_share >= 0.5);
  const busiest = [...r.stations].sort((a, b) => b.max_queue.p50 - a.max_queue.p50)[0];
  const wastePct = cooked ? h.waste_total.p50 / cooked : 0;
  return (
    <div className="kpis">
      <Kpi label="Expected students" value={fmtInt(h.students.p50)} range={`range ${rangeText(h.students)}`} chip={<span className="chip">dinner</span>} />
      <Kpi label="Peak" value={clock(peak.start_min)} range={`${Math.round(peak.expected)} students / 10 min`} chip={<span className="chip warn">rush</span>} />
      <Kpi label="Portions to prepare" value={fmtInt(prep)} range={`${batch.length} batch-cooked · ${r.cook_sheet.length - batch.length} to order`} />
      <Kpi label="Forecast waste" value={fmtInt(h.waste_total.p50)} range={`portions · range ${rangeText(h.waste_total)}`} chip={<span className={`chip ${wastePct > 0.2 ? "warn" : "good"}`}>{Math.round(wastePct * 100)}% of cooked</span>} />
      <Kpi label="Dishes at risk" value={String(atRisk.length)} range="run out in ≥50% of nights" chip={atRisk[0] && <span className="chip bad">{atRisk.sort((a, b) => b.runout_share - a.runout_share)[0].name.slice(0, 22)}</span>} />
      <Kpi label="Peak wait (p90)" value={`${fmt1(h.wait_p90.p50)} min`} range={`range ${rangeText(h.wait_p90, fmt1)}`} chip={busiest && <span className="chip warn">{stationName(busiest.id)}</span>} />
    </div>
  );
}

function Kpi({ label, value, range, chip }: { label: string; value: string; range: string; chip?: React.ReactNode }) {
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      <div className="range">{range}</div>
      {chip}
    </div>
  );
}

function ArrivalsCard({ r, onPick }: { r: Result; onPick: (t: number) => void }) {
  const peak = peakWindow(r);
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Expected arrivals</h2>
          <div className="hint">Per 10 minutes, dinner · from the arrival model ({r.n_nights} simulated nights) · click a bar to watch that moment in the Hall</div>
        </div>
        <span className="chip info">Dinner only</span>
      </div>
      <Bars
        bars={r.arrivals.map((a) => ({ label: a.start.replace(" pm", ""), value: a.expected, color: a === peak ? "var(--maroon)" : undefined, title: `${a.start}: ~${Math.round(a.expected)} students` }))}
        tickEvery={3} onPick={(i) => onPick(r.arrivals[i].start_min + 5)} yLabel="students per 10 minutes" />
      <div className="hint">Breakfast and lunch aren't simulated yet, so the full-day view in the design isn't shown.</div>
    </section>
  );
}

function Alerts({ r }: { r: Result }) {
  const cls = (s: string) => (s === "bad" ? "bad" : s === "warn" ? "warn" : s === "good" ? "good" : "info");
  return (
    <section className="card">
      <h2>Alerts</h2>
      <div className="hint" style={{ marginBottom: 10 }}>Flagged automatically from {r.n_nights} simulated nights</div>
      <div className="stack" style={{ gap: 8 }}>
        {r.flags.length === 0 && <div className="alert good"><b>No issues flagged</b></div>}
        {groupFlags(r.flags).map((f, i) => (
          <div key={i} className={`alert ${cls(f.severity)}`}>
            <b>{f.kind.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())}</b>{f.text}
          </div>
        ))}
      </div>
    </section>
  );
}

function MenuByStation({ r }: { r: Result }) {
  const [showAll, setShowAll] = useState(false);
  const stations = r.stations.map((s) => ({ s, dishes: r.cook_sheet.filter((d) => d.station === s.id) })).filter((x) => x.dishes.length);
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Tonight's menu by station</h2>
          <div className="hint">Real menu (umassdining.com) · number = expected portions served (median of {r.n_nights} nights) · clock = likely run-out</div>
        </div>
        <button className="btn" onClick={() => setShowAll(!showAll)}>{showAll ? "Top 7 per station" : "Show all dishes"}</button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 12 }}>
        {stations.map(({ s, dishes }) => (
          <div key={s.id} style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12 }}>
            <div className="row" style={{ marginBottom: 6 }}>
              <h3>{s.name}</h3>
              <span className="spacer" />
              {s.to_order && <span className="chip info">to order</span>}
              {s.self_serve && <span className="chip">self-serve</span>}
            </div>
            {[...dishes].sort((a, b) => b.served.p50 - a.served.p50).slice(0, showAll ? 99 : 7).map((d) => <DishLine key={d.dish_id} d={d} />)}
          </div>
        ))}
      </div>
      <div className="legend">
        <span><DietTags tags={["halal"]} /> Halal</span>
        <span><DietTags tags={["vegan"]} /> Plant based (vegan)</span>
        <span><DietTags tags={["vegetarian"]} /> Vegetarian</span>
      </div>
    </section>
  );
}

function DishLine({ d }: { d: CookRow }) {
  const risk = d.runout_window && d.runout_share >= 0.3;
  return (
    <div className="row" style={{ flexWrap: "nowrap", fontSize: 12, padding: "3px 0" }}>
      <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={d.name}>{d.name}</span>
      <DietTags tags={d.diet_tags} />
      <span className="num" style={{ width: 34, textAlign: "right" }}>{Math.round(d.served.p50)}</span>
      <span style={{ width: 56, textAlign: "right" }}>
        {risk && <span className={`chip ${d.runout_share >= 0.5 ? "bad" : "warn"}`} style={{ padding: "0 5px" }} title={`runs out in ${Math.round(d.runout_share * 100)}% of nights, ${d.runout_window!.from}–${d.runout_window!.to}`}>{clock(d.runout_window!.p50_min, false)}</span>}
      </span>
    </div>
  );
}

function ProductionPlan({ r, date }: { r: Result; date: string }) {
  const rows = r.cook_sheet.filter((d) => d.plan).sort((a, b) => b.portions_to_prepare.p50 - a.portions_to_prepare.p50);
  const [all, setAll] = useState(false);
  const exportCsv = () =>
    downloadCSV(`production_plan_${date}.csv`, [
      ["dish_id", "dish", "station", "starting_batches", "batch_size", "reorder_point", "max_batches", "cook_min", "hold_min", "last_batch", "portions_to_prepare_p50", "served_p50", "runout_share", "runout_from", "runout_to"],
      ...rows.map((d) => [d.dish_id, d.name, d.station, d.plan!.starting_batches, d.plan!.batch_size, d.plan!.reorder_point, d.plan!.max_batches,
        d.plan!.cook_min, d.plan!.hold_min, d.plan!.last_batch_clock, d.portions_to_prepare.p50, d.served.p50, d.runout_share,
        d.runout_window?.from ?? "", d.runout_window?.to ?? ""]),
    ]);
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Production plan · batch-cooked dishes</h2>
          <div className="hint">What the kitchen cooks tonight · sorted by portions</div>
        </div>
        <div className="row no-print">
          <button className="btn" onClick={() => window.print()}>Print</button>
          <button className="btn primary" onClick={exportCsv}>Export CSV</button>
        </div>
      </div>
      <div className="scroll-x">
        <table className="t">
          <thead>
            <tr><th>Dish</th><th>Station</th><th className="n">Start</th><th className="n">Reorder</th><th className="n">+Batch</th><th className="n">Last</th><th className="n">Prepare</th><th>Risk</th></tr>
          </thead>
          <tbody>
            {rows.slice(0, all ? rows.length : 10).map((d) => (
              <tr key={d.dish_id}>
                <td style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={d.name}>{d.name}</td>
                <td className="muted">{stationName(d.station)}</td>
                <td className="n">{d.plan!.starting_batches}×{d.plan!.batch_size}</td>
                <td className="n">{d.plan!.reorder_point}</td>
                <td className="n">{d.plan!.batch_size}</td>
                <td className="n">{d.plan!.last_batch_clock.replace(" pm", "")}</td>
                <td className="n">{Math.round(d.portions_to_prepare.p50)}</td>
                <td>
                  {d.runout_window && d.runout_share >= 0.3 ? (
                    <span className={`chip ${d.runout_share >= 0.5 ? "bad" : "warn"}`}>out {d.runout_window.from.replace(" pm", "")} · {Math.round(d.runout_share * 100)}%</span>
                  ) : d.waste.p50 >= 30 ? <span className="chip warn">ok · {Math.round(d.waste.p50)} wasted</span> : <span className="chip good">ok</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row hint" style={{ marginTop: 8 }}>
        Showing {all ? rows.length : Math.min(10, rows.length)} of {rows.length} batch-cooked dishes · made-to-order stations aren't planned
        <span className="spacer" />
        {rows.length > 10 && <button className="btn ghost no-print" onClick={() => setAll(!all)}>{all ? "Show fewer" : "Show all"}</button>}
      </div>
    </section>
  );
}

function Staffing({ r }: { r: Result }) {
  const rows = r.stations.filter((s) => !s.self_serve).sort((a, b) => b.max_queue.p50 - a.max_queue.p50);
  return (
    <section className="card">
      <h2>Staffing</h2>
      <div className="hint" style={{ marginBottom: 8 }}>Servers planned per station and the longest line they face (median night)</div>
      <table className="t">
        <thead><tr><th>Station</th><th className="n">Servers</th><th className="n">Peak line</th></tr></thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id}>
              <td>{s.name}</td>
              <td className="n">{s.servers}</td>
              <td className="n"><span className={`chip ${s.max_queue.p50 >= 25 ? "bad" : s.max_queue.p50 >= 12 ? "warn" : ""}`}>{Math.round(s.max_queue.p50)}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="hint" style={{ marginTop: 6 }}>By-hour "servers needed for p90 wait &lt; 8 min" isn't computed by the engine yet.</div>
    </section>
  );
}

function LeftAtClose({ r }: { r: Result }) {
  const rows = r.cook_sheet.filter((d) => d.leftover.p50 > 0).sort((a, b) => b.leftover.p50 - a.leftover.p50).slice(0, 6);
  const max = Math.max(1, ...rows.map((d) => d.leftover.p50));
  const total = r.cook_sheet.reduce((s, d) => s + d.leftover.p50, 0);
  return (
    <section className="card">
      <h2>Expected left at close</h2>
      <div className="hint" style={{ marginBottom: 10 }}>For donation planning · portions, median</div>
      {rows.length === 0 && <div className="hint">Nothing expected to be left over.</div>}
      {rows.map((d) => (
        <div key={d.dish_id} className="row" style={{ flexWrap: "nowrap", fontSize: 12, padding: "3px 0" }}>
          <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.name}</span>
          <div className="bar-track" style={{ width: 70 }}><div className="bar-fill" style={{ width: `${(d.leftover.p50 / max) * 100}%`, background: "var(--data-green)" }} /></div>
          <span className="num" style={{ width: 28, textAlign: "right" }}>{Math.round(d.leftover.p50)}</span>
        </div>
      ))}
      <div className="alert good" style={{ marginTop: 10 }}>
        <b>Pickup note · Food Recovery Network (template)</b>
        ~{fmtInt(total)} portions expected at {clock(r.close)} · suggest pickup {clock(r.close + 15)}
      </div>
    </section>
  );
}
