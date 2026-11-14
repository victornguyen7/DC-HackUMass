import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Bars } from "../components/Charts";
import { ErrorBox, Loading, TopBar } from "../components/Shell";
import { DetailDialog } from "../components/DetailDialog";
import { clock, dateLabel, downloadCSV, fmt1, fmtInt, groupFlags, rangeText, scenarioLabel, stationName } from "../lib/format";
import { useScenarioResult } from "../lib/hooks";
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
  const [params, setParams] = useSearchParams();
  const menuTab = params.get("view") === "menu";
  const [detail, setDetail] = useState<"production" | "staffing" | "leftovers" | "alerts" | null>(null);
  const watch = (t: number) => { setScenario(sc); setHallTime(t); nav("/hall"); };
  const r = res.data?.result;

  return (
    <>
      <TopBar title="Kitchen plan" sub={`Plan for tonight · ${dateLabel(date, { weekday: "short", month: "short", day: "numeric" })}`}
        scenario={sc} showBadges={false} />
      <div className="content kitchen-content">
        {res.error && <ErrorBox error={res.error} />}
        {res.data?.fallback && <div className="banner">{res.data.reason}</div>}
        {res.loading && !r && <Loading />}
        {r && (
          <>
            <div className="kitchen-context">
              <span className="service-time">{clock(r.open)} – {clock(r.close)}<small>Service hours</small></span>
              <Link to="/hall" className="link" onClick={() => setScenario(sc)}>Watch simulation →</Link>
            </div>
            <nav className="kitchen-tabs" aria-label="Kitchen plan sections">
              <button aria-current={!menuTab ? "page" : undefined} onClick={() => setParams({})}>Overview</button>
              <button aria-current={menuTab ? "page" : undefined} onClick={() => setParams({ view: "menu" })}>Menu <span>{r.cook_sheet.length}</span></button>
            </nav>
            {menuTab ? <MenuByStation r={r} /> : <>
              <Kpis r={r} />
              <ArrivalsCard r={r} onPick={watch} onAlerts={() => setDetail("alerts")} />
              <div className="plan-detail-cards">
                <button className="card plan-detail-card" onClick={() => setDetail("production")}>
                  <span className="plan-detail-icon" aria-hidden>≡</span><h2>Production plan</h2>
                  <p>Batch sizes, refill points, and last cook times.</p>
                  <strong>{r.cook_sheet.filter((d) => d.plan).length}<small> batch-cooked dishes</small></strong>
                  <span className="detail-open">View production plan <span aria-hidden>↗</span></span>
                </button>
                <button className="card plan-detail-card" onClick={() => setDetail("staffing")}>
                  <span className="plan-detail-icon" aria-hidden>⌘</span><h2>Staffing</h2>
                  <p>Servers by station and expected peak queues.</p>
                  <strong>{r.stations.filter((s) => !s.self_serve).reduce((sum, s) => sum + s.servers, 0)}<small> servers planned</small></strong>
                  <span className="detail-open">View staffing <span aria-hidden>↗</span></span>
                </button>
                <button className="card plan-detail-card" onClick={() => setDetail("leftovers")}>
                  <span className="plan-detail-icon" aria-hidden>◷</span><h2>Left at close</h2>
                  <p>Expected leftovers and a draft pickup note.</p>
                  <strong>{fmtInt(r.cook_sheet.reduce((sum, d) => sum + d.leftover.p50, 0))}<small> portions estimated</small></strong>
                  <span className="detail-open">View leftovers <span aria-hidden>↗</span></span>
                </button>
              </div>
              <p className="kitchen-model-note">{adopted[date] ? "Adopted plan" : "Current plan"}: {scenarioLabel(sc.preset, sc.lever, sc.label)} · Forecast across {r.n_nights} simulated nights. Assumptions are available in Model information.</p>
            </>}
            {detail && <DetailDialog title={detail === "alerts" ? "Arrival alerts" : detail === "production" ? "Production plan" : detail === "staffing" ? "Staffing" : "Expected left at close"} onClose={() => setDetail(null)}>
              {detail === "production" ? <ProductionPlan r={r} date={date} /> : detail === "staffing" ? <Staffing r={r} /> : detail === "leftovers" ? <LeftAtClose r={r} /> : <Alerts r={r} onWatch={watch} />}
            </DetailDialog>}
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
      <Kpi label="Expected students" value={fmtInt(h.students.p50)} range={`range ${rangeText(h.students)}`} chip={<span className="chip">{r.late_close != null ? "dinner + late night" : "dinner"}</span>} />
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

function ArrivalsCard({ r, onPick, onAlerts }: { r: Result; onPick: (t: number) => void; onAlerts: () => void }) {
  const peak = peakWindow(r);
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Expected arrivals</h2>
          <div className="hint">Students per 10 minutes. Select a bar to watch that moment in the simulation.</div>
        </div>
        <button className="btn arrivals-alert-button" onClick={onAlerts}>View alerts <span>{groupFlags(r.flags).length}</span></button>
      </div>
      <Bars
        bars={r.arrivals.map((a) => ({ label: a.start.replace(" pm", ""), value: a.expected, color: a === peak ? "var(--maroon)" : undefined, title: `${a.start}: ~${Math.round(a.expected)} students` }))}
        tickEvery={3} onPick={(i) => onPick(r.arrivals[i].start_min + 5)} yLabel="students per 10 minutes" />
      <p className="arrivals-caption">Busiest around <b>{clock(peak.start_min)}</b> · {Math.round(peak.expected)} expected arrivals in 10 minutes.</p>
    </section>
  );
}

function Alerts({ r, onWatch }: { r: Result; onWatch: (t: number) => void }) {
  const cls = (s: string) => (s === "bad" ? "bad" : s === "warn" ? "warn" : s === "good" ? "good" : "info");
  return (
    <section className="card">
      <h2>Arrival alerts</h2>
      <div className="hint" style={{ marginBottom: 10 }}>Potential issues across {r.n_nights} simulated nights. Open a moment in the replay to investigate.</div>
      <div className="stack" style={{ gap: 8 }}>
        {r.flags.length === 0 && <div className="alert good"><b>No issues flagged</b></div>}
        {groupFlags(r.flags).map((f, i) => (
          <div key={i} className={`alert ${cls(f.severity)}`}>
            <b>{f.kind.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())}</b><p>{f.text}</p>
            <button className="link" onClick={() => onWatch(r.cook_sheet.find((d) => d.dish_id === f.dish_id)?.runout_window?.p50_min ?? peakWindow(r).start_min + 5)}>View in simulation →</button>
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
      <div className="station-menu-grid">
        {stations.map(({ s, dishes }) => (
          <div key={s.id} style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 12 }}>
            <div className="row" style={{ marginBottom: 6 }}>
              <h3>{s.name}</h3>
              <span className="spacer" />
              {s.to_order && <span className="chip info">to order</span>}
              {s.self_serve && <span className="chip">self-serve</span>}
            </div>
            {[...dishes].sort((a, b) => Number(a.period === "late_night") - Number(b.period === "late_night") || b.served.p50 - a.served.p50).slice(0, showAll ? 99 : 7).map((d) => <DishLine key={d.dish_id} d={d} />)}
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
    <div className="row menu-dish-line">
      <span style={{ flex: 1, minWidth: 0 }} title={d.name}>
        {d.period === "late_night" && <span className="chip info" style={{ padding: "0 5px", marginRight: 4 }} title="Late night, 9 pm–12 am">late</span>}{d.name}
      </span>
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
      <div className="scroll-x production-table" role="region" aria-label="Production plan table" tabIndex={0}>
        <table className="t">
          <thead>
            <tr><th>Dish</th><th>Station</th><th className="n">Start</th><th className="n">Reorder</th><th className="n">+Batch</th><th className="n">Last</th><th className="n">Prepare</th><th>Risk</th></tr>
          </thead>
          <tbody>
            {rows.slice(0, all ? rows.length : 10).map((d) => (
              <tr key={d.dish_id}>
                <td className="production-dish">{d.name}{d.period === "late_night" ? " (late night)" : ""}</td>
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

function hourLabel(h: number) {
  const h12 = ((h + 11) % 12) + 1;
  return `${h12}${h % 24 >= 12 ? "pm" : "am"}`;
}

/** Servers needed per station and hour for servers ≤85% busy (engine 0.5.0 `result.staffing`, UI_ISSUES U5). */
export function staffingNeed(r: Result, sid: string): { peak: number; hour: number | null; byHour: [number, number][] } {
  const byHour = Object.entries(r.staffing?.[sid] ?? {}).map(([h, n]) => [Number(h), n] as [number, number]).sort((a, b) => a[0] - b[0]);
  const top = byHour.reduce<[number, number] | null>((best, x) => (!best || x[1] > best[1] ? x : best), null);
  return { peak: top ? top[1] : 0, hour: top ? top[0] : null, byHour };
}

function Staffing({ r }: { r: Result }) {
  const rows = r.stations.filter((s) => !s.self_serve).sort((a, b) => b.max_queue.p50 - a.max_queue.p50);
  const has = !!r.staffing && Object.keys(r.staffing).length > 0;
  const short = has ? rows.filter((s) => staffingNeed(r, s.id).peak > s.servers) : [];
  return (
    <section className="card">
      <h2>Staffing</h2>
      <div className="hint" style={{ marginBottom: 8 }}>Servers planned per station, the longest line they face (median night){has ? ", and the servers needed in the busiest hour to keep them at most 85% busy" : ""}</div>
      <table className="t">
        <thead><tr><th>Station</th><th className="n">Servers</th><th className="n">Peak line</th>{has && <th className="n">Needed at peak</th>}</tr></thead>
        <tbody>
          {rows.map((s) => {
            const need = staffingNeed(r, s.id);
            return (
              <tr key={s.id}>
                <td>{s.name}</td>
                <td className="n">{s.servers}</td>
                <td className="n"><span className={`chip ${s.max_queue.p50 >= 25 ? "bad" : s.max_queue.p50 >= 12 ? "warn" : ""}`}>{Math.round(s.max_queue.p50)}</span></td>
                {has && (
                  <td className="n">
                    <span className={`chip ${need.peak > s.servers ? "warn" : "good"}`} title={need.byHour.map(([h, n]) => `${hourLabel(h)}: ${n}`).join(" · ")}>
                      {need.peak}{need.hour !== null ? ` · ${hourLabel(need.hour)}` : ""}
                    </span>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
      {has ? (
        short.length > 0 && (
          <div className="stack" style={{ marginTop: 8, gap: 4 }}>
            {short.map((s) => (
              <div key={s.id} className="hint">
                <b>{s.name}</b> by hour: {staffingNeed(r, s.id).byHour.map(([h, n]) => `${hourLabel(h)} ${n}`).join(" · ")} (has {s.servers})
              </div>
            ))}
          </div>
        )
      ) : (
        <div className="hint" style={{ marginTop: 6 }}>"Servers needed" by hour comes with engine 0.5.0+ results; this demo bundle is older (rebuild with <code>make demo-bundle</code>, or run the API).</div>
      )}
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
