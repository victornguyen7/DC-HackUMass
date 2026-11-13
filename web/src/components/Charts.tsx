// Small hand-rolled SVG charts sized to their container (viewBox + width 100%).
import { useState } from "react";

export type Bar = { label: string; value: number; color?: string; title?: string };

/** Vertical bars with x tick labels every `tickEvery` bars. */
export function Bars({
  bars, height = 160, tickEvery = 3, onPick, marker, yLabel,
}: {
  bars: Bar[];
  height?: number;
  tickEvery?: number;
  onPick?: (i: number) => void;
  marker?: number; // fractional bar index for a vertical line
  yLabel?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 800, H = height, padL = 34, padB = 22, padT = 8;
  const max = Math.max(1, ...bars.map((b) => b.value));
  const nice = niceMax(max);
  const bw = (W - padL) / Math.max(1, bars.length);
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / nice);
  return (
    <div style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={yLabel ?? "bar chart"}>
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={padL} x2={W} y1={y(nice * f)} y2={y(nice * f)} stroke="var(--border)" />
            <text x={padL - 6} y={y(nice * f) + 4} textAnchor="end" fontSize="10" fill="var(--text-3)">{Math.round(nice * f)}</text>
          </g>
        ))}
        {bars.map((b, i) => (
          <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onClick={() => onPick?.(i)} style={{ cursor: onPick ? "pointer" : undefined }}>
            <rect x={padL + i * bw} y={padT} width={bw} height={H - padT - padB} fill="transparent" />
            <rect x={padL + i * bw + bw * 0.12} y={y(b.value)} width={bw * 0.76} height={Math.max(0, H - padB - y(b.value))} rx={2} fill={b.color ?? "var(--data-1)"} opacity={hover === null || hover === i ? 1 : 0.7} />
            {i % tickEvery === 0 && (
              <text x={padL + i * bw + bw / 2} y={H - 6} textAnchor="middle" fontSize="10" fill="var(--text-3)">{b.label}</text>
            )}
          </g>
        ))}
        {marker != null && <line x1={padL + marker * bw} x2={padL + marker * bw} y1={padT} y2={H - padB} stroke="var(--maroon)" strokeWidth={2} />}
      </svg>
      {hover !== null && (
        <div className="chip dark" style={{ position: "absolute", top: 0, left: `${((padL + (hover + 0.5) * bw) / W) * 100}%`, transform: "translateX(-50%)", pointerEvents: "none" }}>
          {bars[hover].title ?? `${bars[hover].label}: ${Math.round(bars[hover].value)}`}
        </div>
      )}
    </div>
  );
}

export function niceMax(v: number) {
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

/** Tiny bar sparkline (station detail: queue length over time). */
export function Spark({ values, highlight, height = 40 }: { values: number[]; highlight?: number; height?: number }) {
  const max = Math.max(1, ...values);
  const W = 200, bw = W / Math.max(1, values.length);
  return (
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} aria-hidden>
      {values.map((v, i) => (
        <rect key={i} x={i * bw + 1} y={height - (v / max) * height} width={bw - 2} height={(v / max) * height} rx={1}
          fill={highlight != null && Math.abs(i - highlight) <= 2 ? "var(--warn)" : "var(--data-1)"} />
      ))}
    </svg>
  );
}

/** Horizontal meter. */
export function Meter({ frac, color = "var(--good)", width = 60 }: { frac: number; color?: string; width?: number }) {
  return (
    <div className="bar-track" style={{ width }}>
      <div className="bar-fill" style={{ width: `${Math.max(0, Math.min(1, frac)) * 100}%`, background: color }} />
    </div>
  );
}
