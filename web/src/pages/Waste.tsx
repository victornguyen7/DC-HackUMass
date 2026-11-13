import { useMemo, useState } from "react";
import { niceMax } from "../components/Charts";
import { ErrorBox, Loading, TopBar } from "../components/Shell";
import { loadBacktest, loadFacts, loadHistory } from "../lib/data";
import { dateLabel, downloadCSV, fmt1, fmtInt, PRESET_LABELS, stationName } from "../lib/format";
import { useAsync } from "../lib/hooks";
import type { Backtest, HistoryNight, WasteFacts, WasteHistory } from "../lib/types";

type Win = 28 | 56 | 0;

export default function Waste() {
  const data = useAsync(async () => {
    const [h, b, f] = await Promise.all([loadHistory(), loadBacktest(), loadFacts()]);
    return { h, b, f };
  }, []);
  const [win, setWin] = useState<Win>(28);
  const [station, setStation] = useState<string | null>(null);
  const d = data.data;
  const nights = useMemo(() => (d ? (win ? d.h.nights.slice(-win) : d.h.nights) : []), [d, win]);
  const first = nights[0]?.date, last = nights[nights.length - 1]?.date;
  return (
    <>
      <TopBar title="Waste Record" sub="Recorded waste vs. what the recommended plan would have done"
        scenario={d ? { preset: d.b.plan, lever: "" } : undefined} scenarioPrefix="Plan"
        dateControl={first ? <span className="chip" style={{ padding: "6px 10px", borderRadius: 8 }}>{dateLabel(first, { month: "short", day: "numeric" })} – {dateLabel(last!, { month: "short", day: "numeric" })}</span> : undefined} />
      <div className="content">
        <div className="banner"><span className="chip warn">Synthetic</span>Recorded numbers are synthetic, shaped like MetaFoodX / LeanPath exports, until UMass Dining shares real data. Simulated numbers always come with a range.</div>
        {data.error && <ErrorBox error={data.error} />}
        {data.loading && <Loading />}
        {d && (
          <>
            <div className="row">
              {([[28, "Last 4 weeks"], [56, "Last 8 weeks"], [0, "All recorded"]] as const).map(([w, l]) => (
                <button key={l} className="pill" aria-pressed={win === w} onClick={() => setWin(w)}
                  disabled={w === 56 && d.h.nights.length < 56} title={w === 56 && d.h.nights.length < 56 ? `Only ${d.h.nights.length} nights recorded` : undefined}>{l}</button>
              ))}
              <span className="faint">·</span>
              <button className="pill" aria-pressed={station === null} onClick={() => setStation(null)}>All stations</button>
              {stationsOf(d.b, d.h).map((s) => (
                <button key={s} className="pill" aria-pressed={station === s} onClick={() => setStation(s)}>{stationName(s)}</button>
              ))}
              <span className="spacer" />
              <button className="btn" onClick={() => exportSummary(nights, d.b)}>Export summary</button>
            </div>
            <Kpis nights={nights} b={d.b} />
            <WasteChart nights={nights} b={d.b} station={d.b.nights[0]?.by_station ? station : null} />
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 2fr)", gap: 16 }} className="waste-row">
              <Causes nights={nights} f={d.f} />
              {d.b.dishes ? <TopDishesPlan b={d.b} station={station} /> : <TopDishes h={d.h} station={station} />}
            </div>
            <Tracker b={d.b} />
          </>
        )}
      </div>
    </>
  );
}

const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const money = (x: number) => (x < 0 ? `−$${fmtInt(-x)}` : `$${fmtInt(x)}`);

/** Stations with recorded waste, most waste first. */
function stationsOf(b: Backtest, h: WasteHistory): string[] {
  if (b.dishes) {
    const tot = new Map<string, number>();
    for (const d of b.dishes) tot.set(d.station, (tot.get(d.station) ?? 0) + d.recorded_lb_per_night * d.nights);
    return [...tot.entries()].filter(([, v]) => v > 0).sort((a, c) => c[1] - a[1]).map(([s]) => s);
  }
  return [...new Set(h.top_dishes.map((x) => x.station))].slice(0, 6);
}

function Kpis({ nights, b }: { nights: HistoryNight[]; b: Backtest }) {
  const lb = sum(nights.map((n) => n.waste_lb));
  const portions = sum(nights.map((n) => n.waste_portions));
  const cooked = sum(nights.map((n) => n.cooked_portions));
  const h = b.headline;
  const so = h.stockout_min_per_night;
  // the selected window's back-tested nights (adopted nights are forecasts, not back-tests)
  const inWin = new Set(nights.map((n) => n.date));
  const bt = b.nights.filter((n) => inWin.has(n.date));
  const all = bt.length === b.nights.length;
  const winRed = bt.length ? 1 - sum(bt.map((n) => n.plan_waste_lb.p50)) / sum(bt.map((n) => n.recorded_waste_lb)) : 0;
  const winWins = bt.filter((n) => n.plan_wins).length;
  const soChange = so.plan.p50 / so.baseline_plan.p50 - 1;
  return (
    <div className="kpis">
      <div className="card kpi">
        <div className="label">Recorded waste · {nights.length} dinners</div>
        <div className="value">{fmtInt(lb)} lb</div>
        <div className="range">≈ {fmtInt(portions)} portions · synthetic</div>
        <span className="chip warn">{Math.round((portions / cooked) * 100)}% of cooked</span>
      </div>
      <div className="card kpi">
        <div className="label">With recommended plan</div>
        <div className="value">{all ? `−${fmt1(h.reduction_pct.p50)}` : `${winRed >= 0 ? "−" : "+"}${fmt1(Math.abs(winRed) * 100)}`}%</div>
        <div className="range">{all ? "" : "these nights · "}all recorded: −{fmt1(h.reduction_pct.p50)}% (−{fmt1(h.reduction_pct.p10)} to −{fmt1(h.reduction_pct.p90)}%), simulated</div>
        <span className={`chip ${winWins / Math.max(1, bt.length) > 0.6 ? "good" : ""}`}>wins {winWins} of {bt.length} back-tested nights</span>
      </div>
      <div className="card kpi">
        <div className="label">Saved per week</div>
        <div className="value">≈ {fmtInt(h.lb_per_week.p50)} lb</div>
        <div className="range">≈ {money(h.usd_per_week.p50)} · range {money(h.usd_per_week.p10)} to {money(h.usd_per_week.p90)}</div>
        <span className="chip info">estimate</span>
      </div>
      <div className="card kpi">
        <div className="label">Stockout minutes</div>
        <div className="value">{soChange >= 0 ? "+" : "−"}{fmt1(Math.abs(soChange) * 100)}%</div>
        <div className="range">{fmt1(so.baseline_plan.p50)} → {fmt1(so.plan.p50)} min / night · same nights, same students</div>
        <span className={`chip ${soChange > 0.03 ? "warn" : soChange < -0.01 ? "good" : ""}`}>{soChange > 0.03 ? "trade-off: more empty pans" : soChange < -0.01 ? "fewer empty pans too" : "about the same"}</span>
      </div>
    </div>
  );
}

function WasteChart({ nights: all, b, station }: { nights: HistoryNight[]; b: Backtest; station: string | null }) {
  // A station view uses the back-test's per-station split: pre-consumer lb, plan = mean of seeds (no band).
  const simAll = new Map(b.nights.map((n) => [n.date, n]));
  const nights = station
    ? all.filter((n) => simAll.get(n.date)?.by_station).map((n) => ({ ...n, waste_lb: simAll.get(n.date)!.by_station![station]?.recorded_lb ?? 0 }))
    : all;
  const sim = station
    ? new Map(b.nights.map((n) => {
      const v = n.by_station?.[station]?.plan_lb ?? 0;
      return [n.date, { ...n, plan_waste_lb: { p10: v, p50: v, p90: v } }];
    }))
    : simAll;
  const W = 1000, H = 220, padL = 40, padB = 24, padT = 10;
  const max = niceMax(Math.max(...nights.map((n) => Math.max(n.waste_lb, sim.get(n.date)?.plan_waste_lb.p90 ?? 0))));
  const bw = (W - padL) / nights.length;
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / max);
  const cx = (i: number) => padL + i * bw + bw / 2;
  const pts = nights.map((n, i) => ({ i, s: sim.get(n.date) })).filter((p) => p.s);
  const band = pts.length
    ? `M${pts.map((p) => `${cx(p.i)},${y(p.s!.plan_waste_lb.p90)}`).join("L")}L${[...pts].reverse().map((p) => `${cx(p.i)},${y(p.s!.plan_waste_lb.p10)}`).join("L")}Z`
    : "";
  const [hover, setHover] = useState<number | null>(null);
  const hn = hover != null ? nights[hover] : null;
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Waste per dinner{station ? ` at ${stationName(station)}` : ""}: recorded vs. simulated with the plan</h2>
          <div className="hint">
            Each night re-simulated with its own menu and crowd · {station ? "expired + left at close only; plan line = average of 10 simulated nights (adopted nights left out)" : "band = 10th–90th percentile"} · plan: {PRESET_LABELS[b.plan] ?? b.plan}
          </div>
        </div>
        <span className="chip">lb</span>
      </div>
      <div style={{ position: "relative" }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Waste per dinner, recorded bars and simulated line">
          <defs>
            <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="6" height="6" fill="var(--good-soft)" /><line x1="0" y1="0" x2="0" y2="6" stroke="#bcd9c4" strokeWidth="2" />
            </pattern>
          </defs>
          {[0, 0.25, 0.5, 0.75, 1].map((f) => (
            <g key={f}>
              <line x1={padL} x2={W} y1={y(max * f)} y2={y(max * f)} stroke="var(--border)" />
              <text x={padL - 6} y={y(max * f) + 4} fontSize="10" textAnchor="end" fill="var(--text-3)">{Math.round(max * f)}</text>
            </g>
          ))}
          {nights.map((n, i) => (
            <g key={n.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={padL + i * bw} y={padT} width={bw} height={H - padT - padB} fill="transparent" />
              <rect x={padL + i * bw + bw * 0.14} y={y(n.waste_lb)} width={bw * 0.72} height={H - padB - y(n.waste_lb)} rx={2}
                fill={n.adopted ? "var(--maroon)" : "var(--recorded)"} opacity={hover == null || hover === i ? 1 : 0.75} />
              {(i % Math.ceil(nights.length / 6) === 0 || i === nights.length - 1) && (
                <text x={cx(i)} y={H - 6} fontSize="10" textAnchor="middle" fill="var(--text-3)">{dateLabel(n.date, { month: "short", day: "numeric" })}</text>
              )}
            </g>
          ))}
          {band && <path d={band} fill="url(#hatch)" opacity={0.9} pointerEvents="none" />}
          {pts.length > 1 && (
            <path d={`M${pts.map((p) => `${cx(p.i)},${y(p.s!.plan_waste_lb.p50)}`).join("L")}`} fill="none" stroke="var(--good)" strokeWidth={2} strokeDasharray="6 4" pointerEvents="none" />
          )}
        </svg>
        {hn && (
          <div className="card" style={{ position: "absolute", top: 0, left: `${Math.min(75, (cx(hover!) / W) * 100)}%`, padding: "8px 10px", pointerEvents: "none", fontSize: 12 }}>
            <b>{dateLabel(hn.date)}</b>{hn.adopted ? " · plan adopted" : ""}<br />
            Recorded {fmt1(hn.waste_lb)} lb · {fmtInt(hn.students)} students<br />
            {sim.get(hn.date) && <>With plan {fmt1(sim.get(hn.date)!.plan_waste_lb.p50)} lb ({fmt1(sim.get(hn.date)!.plan_waste_lb.p10)}–{fmt1(sim.get(hn.date)!.plan_waste_lb.p90)})</>}
          </div>
        )}
      </div>
      <div className="legend">
        <span><i className="dot" style={{ background: "var(--recorded)", borderRadius: 2, margin: 0 }} />Recorded (synthetic)</span>
        <span><i className="dot" style={{ background: "var(--maroon)", borderRadius: 2, margin: 0 }} />Recorded, plan was adopted</span>
        <span><svg width="18" height="6"><line x1="0" y1="3" x2="18" y2="3" stroke="var(--good)" strokeWidth="2" strokeDasharray="5 3" /></svg>Simulated with recommended plan</span>
        <span><i className="dot" style={{ background: "url(#hatch) var(--good-soft)", borderRadius: 2, margin: 0 }} />Simulated range</span>
      </div>
    </section>
  );
}

function Causes({ nights, f }: { nights: HistoryNight[]; f: WasteFacts }) {
  const exp = sum(nights.map((n) => n.expired_lb)), close = sum(nights.map((n) => n.close_lb)), plate = sum(nights.map((n) => n.plate_lb));
  const tot = exp + close + plate || 1;
  const parts = [
    ["Expired on the line", "past hold time · planning can fix", exp, "var(--bad)"],
    ["Left in pans at close", "over-production · planning can fix", close, "var(--warn)"],
    ["Plate waste", "students' plates · not affected by the plan", plate, "var(--recorded)"],
  ] as const;
  const dest = f.destinations_and_targets.destination_distribution_fy2026;
  return (
    <section className="card">
      <h2>Where it comes from</h2>
      <div className="hint" style={{ marginBottom: 12 }}>Share of recorded waste, {nights.length} dinners</div>
      <div style={{ display: "flex", height: 10, borderRadius: 5, overflow: "hidden", gap: 2, marginBottom: 12 }}>
        {parts.map(([l, , v, c]) => <div key={l} style={{ width: `${(v / tot) * 100}%`, background: c }} />)}
      </div>
      {parts.map(([l, s, v, c]) => (
        <div key={l} className="row" style={{ flexWrap: "nowrap", alignItems: "flex-start", marginBottom: 10 }}>
          <i className="dot" style={{ background: c, borderRadius: 2, marginTop: 5 }} />
          <div style={{ flex: 1 }}><div style={{ fontWeight: 500 }}>{l}</div><div className="hint">{s}</div></div>
          <b style={{ fontSize: 17 }} className="tnum">{Math.round((v / tot) * 100)}%</b>
        </div>
      ))}
      <div className="alert muted" style={{ fontSize: 12 }}>
        Tracked overproduction, FY2026 (published): <b style={{ display: "inline" }}>{dest.donation_pct}% donated</b> · {dest.reuse_pct}% reused · {dest.anaerobic_digestion_pct}% anaerobic digestion
      </div>
    </section>
  );
}

function TopDishes({ h, station }: { h: WasteHistory; station: string | null }) {
  const rows = h.top_dishes.filter((d) => !station || d.station === station).slice(0, 8);
  const n = h.nights.length;
  return (
    <section className="card">
      <h2>Top waste dishes</h2>
      <div className="hint" style={{ marginBottom: 8 }}>Recorded pre-consumer waste per dinner served (all {n} recorded nights)</div>
      <div className="scroll-x">
        <table className="t">
          <thead><tr><th>Dish</th><th>Station</th><th className="n">lb / dinner</th><th className="n">Expired</th><th className="n">Left at close</th><th className="n">Plate</th></tr></thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.dish_id}>
                <td>{d.dish}</td>
                <td className="muted">{stationName(d.station)}</td>
                <td className="n">{fmt1(d.pre_consumer_lb / n)}</td>
                <td className="n">{fmtInt(d.expired_lb)}</td>
                <td className="n">{fmtInt(d.close_lb)}</td>
                <td className="n">{fmtInt(d.plate_lb)}</td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={6} className="muted">No top-waste dishes at this station.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="hint" style={{ marginTop: 6 }}>Per-dish "with plan" numbers aren't in the back-test export yet.</div>
    </section>
  );
}

/** Back-test per dish: recorded vs. with the plan, $ / week (designed table). */
function TopDishesPlan({ b, station }: { b: Backtest; station: string | null }) {
  const rows = b.dishes!.filter((d) => !station || d.station === station)
    .sort((a, c) => c.recorded_lb_per_night * c.nights - a.recorded_lb_per_night * a.nights).slice(0, 8);
  return (
    <section className="card">
      <h2>Top waste dishes</h2>
      <div className="hint" style={{ marginBottom: 8 }}>Expired + left at close, per night the dish was served · {b.headline.nights} back-tested nights · plan = average of 10 simulated nights</div>
      <div className="scroll-x">
        <table className="t">
          <thead><tr><th>Dish</th><th>Station</th><th className="n">Recorded lb</th><th className="n">With plan</th><th className="n">Change</th><th className="n">$ / week</th></tr></thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.dish_id}>
                <td title={`served on ${d.nights} of ${b.headline.nights} nights`}>{d.dish}</td>
                <td className="muted">{stationName(d.station)}</td>
                <td className="n">{fmt1(d.recorded_lb_per_night)}</td>
                <td className="n">{fmt1(d.plan_lb_per_night)}</td>
                <td className="n">{d.change_pct == null ? "—" : <span className={d.change_pct < -1 ? "good-text" : d.change_pct > 1 ? "bad-text" : ""}>{d.change_pct > 0 ? "+" : d.change_pct < 0 ? "−" : "±"}{fmt1(Math.abs(d.change_pct))}%</span>}</td>
                <td className="n">{d.usd_saved_per_week >= 0 ? `$${fmtInt(d.usd_saved_per_week)} saved` : `$${fmtInt(-d.usd_saved_per_week)} more`}</td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={6} className="muted">No recorded waste at this station.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="hint" style={{ marginTop: 6 }}>Recorded numbers are synthetic. Some dishes waste more under the plan; the total still drops.</div>
    </section>
  );
}

function Tracker({ b }: { b: Backtest }) {
  const rows = b.adopted_nights;
  const inside = rows.filter((r) => r.within_range).length;
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Adopted-plan tracker</h2>
          <div className="hint">When a What-If plan is adopted for a date, its prediction is checked against what was recorded</div>
        </div>
        <span className="chip info">{inside} of {rows.length} inside the predicted range</span>
      </div>
      <div className="scroll-x">
        <table className="t">
          <thead><tr><th>Date</th><th>Adopted plan</th><th className="n">Predicted waste (lb)</th><th className="n">Recorded</th><th className="n">Stockout min (pred → rec)</th><th>Result</th></tr></thead>
          <tbody>
            {rows.map((r) => {
              const above = r.recorded_waste_lb > r.predicted_waste_lb.p90;
              return (
                <tr key={r.date}>
                  <td>{dateLabel(r.date)}</td>
                  <td>{PRESET_LABELS[r.plan] ?? r.plan}</td>
                  <td className="n">{fmtInt(r.predicted_waste_lb.p50)} ({fmtInt(r.predicted_waste_lb.p10)}–{fmtInt(r.predicted_waste_lb.p90)})</td>
                  <td className="n">{fmtInt(r.recorded_waste_lb)}</td>
                  <td className="n">{fmtInt(r.predicted_stockout_min.p50)} → {fmtInt(r.recorded_stockout_min)}</td>
                  <td>{r.within_range ? <span className="chip good">✓ inside range</span> : <span className="chip bad">✕ {above ? "above" : "below"} range</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function exportSummary(nights: HistoryNight[], b: Backtest) {
  const sim = new Map(b.nights.map((n) => [n.date, n]));
  downloadCSV("waste_summary.csv", [
    ["date", "weekday", "students", "adopted", "recorded_waste_lb", "expired_lb", "close_lb", "plate_lb", "stockout_min", "plan_waste_lb_p10", "plan_waste_lb_p50", "plan_waste_lb_p90", "kind"],
    ...nights.map((n) => {
      const s = sim.get(n.date);
      return [n.date, n.weekday, n.students, n.adopted ? 1 : 0, n.waste_lb, n.expired_lb, n.close_lb, n.plate_lb, n.stockout_min,
        s?.plan_waste_lb.p10 ?? "", s?.plan_waste_lb.p50 ?? "", s?.plan_waste_lb.p90 ?? "", "recorded=SYNTHETIC; plan=simulated"];
    }),
  ]);
}
