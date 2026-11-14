import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { niceMax } from "../components/Charts";
import { Loading } from "../components/Shell";
import { dateLabel, downloadCSV, fmt1, fmtInt } from "../lib/format";
import { useApiUp, useAsync } from "../lib/hooks";
import { loadOutlook, resolveNights, totals, type NightPlan } from "../lib/outlook";
import { useApp } from "../lib/store";
import type { HistoryNight } from "../lib/types";

const CONTEXT_NIGHTS = 7; // recorded nights drawn before the divider, for comparison
const money = (x: number) => (x < 0 ? `−$${fmtInt(-x)}` : `$${fmtInt(x)}`);
const pct = (x: number) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${fmt1(Math.abs(x) * 100)}%`;

/** The coming nights: simulated waste with the plan adopted for each night (Lab, preset or Ask Sam), next to the recorded past. */
export default function WasteOutlook({ history }: { history: HistoryNight[] }) {
  const { adopted } = useApp();
  const apiOn = useApiUp();
  const out = useAsync(loadOutlook, [apiOn]);
  const nights = useAsync(async () => (out.data ? resolveNights(out.data.data, adopted) : []), [out.data, adopted, apiOn]);
  if (out.error) return <div className="alert warn" role="alert"><b>The coming nights aren't available yet</b>{out.error.message} The recorded past is under "Last 4 weeks".</div>;
  if (!out.data || !nights.data) return <Loading what="the waste outlook" />;
  const recent = history.slice(-28);
  const recordedAvg = recent.length ? recent.reduce((a, n) => a + n.waste_lb, 0) / recent.length : null;
  return (
    <>
      <OutlookKpis nights={nights.data} recordedAvg={recordedAvg} />
      <OutlookChart nights={nights.data} context={history.slice(-CONTEXT_NIGHTS)} source={out.data.source} />
      <OutlookTable nights={nights.data} />
    </>
  );
}

function OutlookKpis({ nights, recordedAvg }: { nights: NightPlan[]; recordedAvg: number | null }) {
  const t = totals(nights);
  const perNight = t.nights ? t.planLb / t.nights : 0;
  const change = t.baseLb ? -t.savedLb / t.baseLb : 0;
  return (
    <div className="kpis">
      <div className="card kpi">
        <div className="label">Expected waste · next {nights.length} nights</div>
        <div className="value">≈ {fmtInt(t.planLb)} lb</div>
        <div className="range">≈ {fmtInt(perNight)} lb a night{recordedAvg != null ? ` · recorded last 4 weeks: ${fmtInt(recordedAvg)} lb a night` : ""}</div>
        <span className="chip info">simulated</span>
      </div>
      <div className="card kpi">
        <div className="label">Saved vs. tonight's plans</div>
        <div className="value">{fmtInt(t.savedLb)} lb</div>
        <div className="range">{pct(change)} waste · ≈ {money(t.savedUsd)} over these nights</div>
        <span className={`chip ${t.savedLb > 0 ? "good" : ""}`}>{t.nights} of {nights.length} nights with numbers</span>
      </div>
      <div className="card kpi">
        <div className="label">Nights with an adopted plan</div>
        <div className="value">{t.adopted} of {nights.length}</div>
        <div className="range">{t.adopted < nights.length ? "the rest show the recommended plan, not yet adopted" : "every night has a plan"}</div>
        <span className="chip maroon">adopt in the Lab or Ask Sam</span>
      </div>
      <div className="card kpi">
        <div className="label">Stockout minutes</div>
        <div className="value">{t.stockoutChange >= 0 ? "+" : "−"}{fmt1(Math.abs(t.nights ? t.stockoutChange / t.nights : 0))}</div>
        <div className="range">minutes a night with empty pans, vs. tonight's plans</div>
        <span className={`chip ${t.stockoutChange > 3 * Math.max(1, t.nights) ? "warn" : ""}`}>{t.stockoutChange > 0 ? "trade-off: more empty pans" : "no more empty pans"}</span>
      </div>
    </div>
  );
}

function OutlookChart({ nights, context, source }: { nights: NightPlan[]; context: HistoryNight[]; source: "bundle" | "api" }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 1000, H = 240, padL = 40, padB = 26, padT = 18, gap = 0.6; // gap = slots between recorded and simulated
  const slots = context.length + gap + nights.length;
  const bw = (W - padL) / slots;
  const vals = [...context.map((n) => n.waste_lb), ...nights.flatMap((n) => [n.baseline?.lb.p90 ?? 0, n.plan?.lb.p90 ?? 0])];
  const max = niceMax(Math.max(1, ...vals));
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / max);
  const cxP = (i: number) => padL + i * bw + bw / 2;
  const cxF = (i: number) => padL + (context.length + gap + i) * bw + bw / 2;
  const divider = padL + (context.length + gap / 2) * bw;
  const pts = nights.map((n, i) => ({ n, i })).filter(({ n }) => n.plan);
  const hn = hover != null ? nights[hover] : null;
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Waste per night: recorded past and the next {nights.length} nights</h2>
          <div className="hint">Coming nights are simulated ({nights[0]?.plan?.n_nights ?? 30} nights each, range = 10th–90th percentile) with the plan adopted for that night, against tonight's plan as written. Same units as the record: expired + left at close.</div>
        </div>
        <button className="btn" onClick={() => exportOutlook(nights)}>Export</button>
      </div>
      <div style={{ position: "relative" }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Waste per night: recorded nights, then simulated coming nights with their plans">
          {[0, 0.25, 0.5, 0.75, 1].map((f) => (
            <g key={f}>
              <line x1={padL} x2={W} y1={y(max * f)} y2={y(max * f)} stroke="var(--border)" />
              <text x={padL - 6} y={y(max * f) + 4} fontSize="10" textAnchor="end" fill="var(--text-3)">{Math.round(max * f)}</text>
            </g>
          ))}
          {context.map((n, i) => (
            <g key={n.date}>
              <rect x={cxP(i) - bw * 0.36} y={y(n.waste_lb)} width={bw * 0.72} height={H - padB - y(n.waste_lb)} rx={2} fill="var(--recorded)" />
              <text x={cxP(i)} y={H - 8} fontSize="10" textAnchor="middle" fill="var(--text-3)">{dateLabel(n.date, { month: "short", day: "numeric" })}</text>
            </g>
          ))}
          <line x1={divider} x2={divider} y1={padT - 12} y2={H - padB} stroke="var(--text-3)" strokeDasharray="3 3" />
          <text x={divider - 6} y={padT - 4} fontSize="10" textAnchor="end" fill="var(--text-2)">Recorded</text>
          <text x={divider + 6} y={padT - 4} fontSize="10" fill="var(--text-2)">Simulated</text>
          {nights.map((n, i) => {
            const b = n.baseline?.lb;
            return (
              <g key={n.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <rect x={cxF(i) - bw / 2} y={padT} width={bw} height={H - padT - padB} fill="transparent" />
                {b && <>
                  <rect x={cxF(i) - bw * 0.36} y={y(b.p50)} width={bw * 0.72} height={H - padB - y(b.p50)} rx={2}
                    fill="var(--bg-muted)" stroke="var(--border-strong)" strokeDasharray="3 2" opacity={hover == null || hover === i ? 1 : 0.7} />
                  <line x1={cxF(i)} x2={cxF(i)} y1={y(b.p90)} y2={y(b.p10)} stroke="var(--text-3)" />
                </>}
                <text x={cxF(i)} y={H - 8} fontSize="10" textAnchor="middle" fill="var(--text-2)">{dateLabel(n.date, { month: "short", day: "numeric" })}</text>
                {n.status !== "ok" && b && <text x={cxF(i)} y={y(b.p50) - 8} fontSize="12" textAnchor="middle" fill="var(--warn)">?</text>}
              </g>
            );
          })}
          {pts.length > 1 && (
            <path d={`M${pts.map(({ n, i }) => `${cxF(i)},${y(n.plan!.lb.p50)}`).join("L")}`} fill="none" stroke="var(--good)" strokeWidth={1.5} strokeDasharray="5 4" pointerEvents="none" />
          )}
          {pts.map(({ n, i }) => {
            const c = n.source === "adopted" ? "var(--maroon)" : "var(--good)";
            return (
              <g key={n.date} pointerEvents="none">
                <line x1={cxF(i)} x2={cxF(i)} y1={y(n.plan!.lb.p90)} y2={y(n.plan!.lb.p10)} stroke={c} strokeWidth={3} strokeLinecap="round" opacity={0.45} />
                <circle cx={cxF(i)} cy={y(n.plan!.lb.p50)} r={5} fill={c} stroke="var(--bg-surface)" strokeWidth={1.5} />
              </g>
            );
          })}
        </svg>
        {hn && (
          <div className="card" style={{ position: "absolute", top: 0, left: `${Math.min(70, (cxF(hover!) / W) * 100)}%`, padding: "8px 10px", pointerEvents: "none", fontSize: 12, maxWidth: 300 }}>
            <b>{dateLabel(hn.date)}</b> · {hn.source === "adopted" ? "adopted" : "recommended, not adopted"}<br />
            <span className="muted">{hn.label}</span><br />
            {hn.baseline && <>Tonight's plan {fmt1(hn.baseline.lb.p50)} lb ({fmt1(hn.baseline.lb.p10)}–{fmt1(hn.baseline.lb.p90)})<br /></>}
            {hn.plan ? <>With the plan {fmt1(hn.plan.lb.p50)} lb ({fmt1(hn.plan.lb.p10)}–{fmt1(hn.plan.lb.p90)})</> : <>{statusText(hn)}</>}
          </div>
        )}
      </div>
      <div className="legend">
        <span><i className="dot" style={{ background: "var(--recorded)", borderRadius: 2, margin: 0 }} />Recorded (synthetic)</span>
        <span><i className="dot" style={{ background: "var(--bg-muted)", border: "1px dashed var(--border-strong)", borderRadius: 2, margin: 0 }} />Tonight's plan as written (simulated)</span>
        <span><i className="dot" style={{ background: "var(--maroon)", margin: 0 }} />Adopted plan</span>
        <span><i className="dot" style={{ background: "var(--good)", margin: 0 }} />Recommended plan (not adopted)</span>
        <span className="faint">{source === "api" ? "from the live API" : "from the demo bundle"}</span>
      </div>
    </section>
  );
}

function statusText(n: NightPlan) {
  return n.status === "needs-api" ? "This plan ran on the API: start it (make serve) to load its numbers."
    : n.status === "missing" ? "No simulation of this plan for this night yet: open it in the Lab and run it." : "";
}

function OutlookTable({ nights }: { nights: NightPlan[] }) {
  const nav = useNavigate();
  const { setDate } = useApp();
  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2>Plan per night</h2>
          <div className="hint">Adopting a plan in the Lab or from Ask Sam updates its night here. Waste is the median of the simulated nights, in lb.</div>
        </div>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="t">
          <thead><tr><th>Night</th><th>Plan</th><th className="n">Tonight's plan</th><th className="n">With the plan</th><th className="n">Change</th><th className="n">$ saved</th><th /></tr></thead>
          <tbody>
            {nights.map((n) => {
              const ch = n.plan && n.baseline ? n.plan.lb.p50 / n.baseline.lb.p50 - 1 : null;
              return (
                <tr key={n.date}>
                  <td>{dateLabel(n.date)}</td>
                  <td>
                    <span className={`chip ${n.source === "adopted" ? "maroon" : "good"}`}>{n.source === "adopted" ? "Adopted" : "Recommended"}</span>{" "}
                    <span title={n.label}>{n.label.length > 60 ? `${n.label.slice(0, 57)}…` : n.label}</span>
                    {n.status !== "ok" && <div className="hint">{statusText(n)}</div>}
                  </td>
                  <td className="n">{n.baseline ? fmtInt(n.baseline.lb.p50) : "—"}</td>
                  <td className="n">{n.plan ? fmtInt(n.plan.lb.p50) : "—"}</td>
                  <td className="n">{ch == null ? "—" : <span className={ch < 0 ? "good-t" : ch > 0 ? "bad-t" : ""}>{pct(ch)}</span>}</td>
                  <td className="n">{n.plan && n.baseline ? money(n.baseline.usd.p50 - n.plan.usd.p50) : "—"}</td>
                  <td><button className="btn ghost" onClick={() => { setDate(n.date); nav("/lab"); }}>Change in the Lab</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function exportOutlook(nights: NightPlan[]) {
  downloadCSV("waste-outlook.csv", [
    ["night", "plan", "source", "baseline_lb_p50", "baseline_lb_p10", "baseline_lb_p90", "plan_lb_p50", "plan_lb_p10", "plan_lb_p90", "saved_usd_p50"],
    ...nights.map((n) => [n.date, n.label, n.source,
      n.baseline?.lb.p50 ?? "", n.baseline?.lb.p10 ?? "", n.baseline?.lb.p90 ?? "",
      n.plan?.lb.p50 ?? "", n.plan?.lb.p10 ?? "", n.plan?.lb.p90 ?? "",
      n.plan && n.baseline ? Math.round(n.baseline.usd.p50 - n.plan.usd.p50) : ""]),
  ]);
}
