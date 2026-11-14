import { useEffect, useMemo, useRef, useState } from "react";
import type { Pose } from "../lib/playback";
import { FOOTPRINT, STRUCTURES, TABLES } from "../lib/hallGeometry";
import { stationName } from "../lib/format";

export type SceneStation = { id: string; pos: [number, number]; line: number; empty: number };
type Props = {
  poses: Pose[];
  stations: SceneStation[];
  selectedStation: string | null;
  selectedStudent: number | null;
  onStation: (id: string) => void;
  onStudent: (id: number) => void;
  colorBy: "diet" | "wait" | "missed";
  missed: Set<number>;
};

const project = (x: number, y: number, z = 0): [number, number] => [44 + x * 8.8 + y * 3.2, 292 + x * 1.9 - y * 4.8 - z * 10];
const tables = TABLES;
const dietColors: Record<string, string> = { none: "#a5c4ed", vegetarian: "#a4d59c", vegan: "#6ad3c1", halal: "#eac28d" };

/** An illustrated isometric view of the same poses used by the measured top-down plan. */
export function HallScene({ poses, stations, selectedStation, selectedStudent, onStation, onStudent, colorBy, missed }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 800, height: 480 });
  useEffect(() => {
    const wrap = wrapRef.current!;
    const observer = new ResizeObserver(([entry]) => setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(wrap);
    return () => observer.disconnect();
  }, []);
  // Fit the illustrated floor's actual bounds, including room height and shadows.
  // This keeps the hall legible when landscape tablets have less vertical room.
  const scale = Math.min(size.width / 740, size.height / 360);
  const offsetX = (size.width - 740 * scale) / 2 - 20 * scale;
  const offsetY = (size.height - 360 * scale) / 2 - 86 * scale;

  // Distribute labels in screen pixels so all stations remain readable at laptop widths.
  const labels = useMemo(() => {
    const placed: { x: number; y: number; w: number; h: number }[] = [];
    return stations.filter((s) => s.id !== "breads").map((station) => {
      const [px, py] = project(...station.pos, 2.4);
      const label = station.id === "desserts" ? "Desserts · Breads" : stationName(station.id);
      const w = Math.min(190, label.length * 6.8 + 36), h = 32;
      const targetX = px * scale + offsetX - w / 2, targetY = py * scale + offsetY - h;
      let x = Math.max(6, Math.min(size.width - w - 6, targetX));
      let y = Math.max(4, Math.min(size.height - h - 4, targetY));
      const overlaps = (cx: number, cy: number) => placed.some((b) => cx < b.x + b.w + 5 && cx + w + 5 > b.x && cy < b.y + b.h + 5 && cy + h + 5 > b.y);
      if (overlaps(x, y)) {
        let best = Infinity;
        // Look both above and below; clamping every displaced label to the top
        // previously made labels pile on top of each other in a short viewport.
        for (let cy = 4; cy <= size.height - h - 4; cy += h + 6) {
          for (let cx = 6; cx <= size.width - w - 6; cx += 12) {
            const distance = (cx - targetX) ** 2 + (cy - targetY) ** 2;
            if (distance < best && !overlaps(cx, cy)) { best = distance; x = cx; y = cy; }
          }
        }
      }
      placed.push({ x, y, w, h });
      return { ...station, label, x, y, w, h, anchorX: px * scale + offsetX, anchorY: py * scale + offsetY };
    });
  }, [stations, size, scale, offsetX, offsetY]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || !size.width) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.round(size.width * dpr), h = Math.round(size.height * dpr);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.width, size.height);
    ctx.translate(offsetX, offsetY);
    ctx.scale(scale, scale);
    const polygon = (points: number[][], fill: string, stroke?: string) => {
      ctx.beginPath();
      points.forEach(([x, y, z = 0], i) => { const p = project(x, y, z); if (i) ctx.lineTo(...p); else ctx.moveTo(...p); });
      ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
      if (stroke) { ctx.lineWidth = .6; ctx.strokeStyle = stroke; ctx.stroke(); }
    };
    const box = (x: number, y: number, w: number, d: number, h: number, top = "#c9cdbf", front = "#829599", side = "#9aabaa") => {
      polygon([[x, y, 0], [x + w, y, 0], [x + w, y, h], [x, y, h]], front);
      polygon([[x + w, y, 0], [x + w, y + d, 0], [x + w, y + d, h], [x + w, y, h]], side);
      polygon([[x, y, h], [x + w, y, h], [x + w, y + d, h], [x, y + d, h]], top, "#ffffff16");
    };
    ctx.save(); ctx.shadowBlur = 28; ctx.shadowOffsetY = 16; ctx.shadowColor = "#030b12a8";
    polygon(FOOTPRINT.map((p) => [...p, -1]), "#08151f"); ctx.restore();
    FOOTPRINT.forEach((a, i) => { const b = FOOTPRINT[(i + 1) % FOOTPRINT.length]; polygon([[...a, 0], [...b, 0], [...b, -1], [...a, -1]], "#35464e"); });
    polygon(FOOTPRINT.map((p) => [...p, 0]), "#64757b", "#91a0a5");
    polygon([[1.7, 26], [61.8, 26], [61.8, 34.4], [1.7, 34.4]], "#77888d");
    polygon([[30.9, .2], [63.3, .2], [63.3, 6.4], [30.9, 6.4]], "#718181");
    const objects: { depth: number; draw: () => void }[] = [];
    const addBox = (x: number, y: number, w: number, d: number, h: number, top?: string, front?: string, side?: string) => objects.push({ depth: x * 1.9 - y * 4.8, draw: () => box(x, y, w, d, h, top, front, side) });
    for (const structure of STRUCTURES) {
      const { polygon: outline, height, kind } = structure;
      const depth = outline.reduce((sum, [x, y]) => sum + x * 1.9 - y * 4.8, 0) / outline.length;
      objects.push({ depth, draw: () => {
        for (let i = 0; i < outline.length; i++) {
          const a = outline[i], b = outline[(i + 1) % outline.length];
          polygon([[...a, 0], [...b, 0], [...b, height], [...a, height]], i % 2 ? "#83989c" : "#607782", "#ffffff10");
        }
        polygon(outline.map((p) => [...p, height]), kind === "room" ? "#627985" : kind === "stairs" ? "#a5b6b6" : kind === "wall" ? "#c0cdd0" : "#c6cbbd", "#d9e4e533");
      } });
    }
    for (const [x, y] of tables) {
      addBox(x - .65, y - .38, 1.3, .76, .72, "#bda889", "#7b7769", "#958c78");
      for (const dy of [-.72, .66]) addBox(x - .38, y + dy, .7, .32, .36, "#586e79", "#3f5560", "#496471");
    }
    for (const person of poses) {
      const [x, y] = person.xy;
      objects.push({ depth: x * 1.9 - y * 4.8 + .5, draw: () => {
        const color = colorBy === "diet" ? dietColors[person.diet] ?? "#e0e7e6"
          : colorBy === "missed" ? missed.has(person.id) ? "#f39e97" : "#c2d0d5"
          : person.phase === "line" ? (person.waitedMin ?? 0) > 10 ? "#f39e97" : (person.waitedMin ?? 0) > 5 ? "#eac28d" : "#a4d7b7"
          : person.phase === "seated" ? "#b1c4c9" : "#e0e7e6";
        const [cx, cy] = project(x, y);
        ctx.fillStyle = "#14273350"; ctx.beginPath(); ctx.ellipse(cx + 2, cy + 1, 3.1, 1.5, 0, 0, Math.PI * 2); ctx.fill();
        if (person.id === selectedStudent) { ctx.strokeStyle = "#f4c2cc"; ctx.lineWidth = 1.8; ctx.beginPath(); ctx.ellipse(cx, cy, 6, 3, 0, 0, Math.PI * 2); ctx.stroke(); }
        ctx.strokeStyle = color; ctx.lineWidth = 2.5; ctx.lineCap = "round"; ctx.beginPath(); ctx.moveTo(cx, cy - 2); ctx.lineTo(cx, cy - (person.phase === "seated" ? 5 : 8)); ctx.stroke();
        ctx.fillStyle = "#e3d4bc"; ctx.beginPath(); ctx.arc(cx, cy - (person.phase === "seated" ? 7 : 10), 1.65, 0, Math.PI * 2); ctx.fill();
        if (person.hands !== "empty") { ctx.fillStyle = person.hands === "plate" ? "#fff" : "#d6a875"; ctx.beginPath(); ctx.ellipse(cx + 2.3, cy - 5, 2.1, 1.1, 0, 0, Math.PI * 2); ctx.fill(); }
      } });
    }
    objects.sort((a, b) => a.depth - b.depth).forEach((o) => o.draw());
  }, [poses, size, scale, offsetX, offsetY, selectedStudent, colorBy, missed]);

  return (
    <div className="hall-canvas-wrap" ref={wrapRef}>
      <canvas ref={canvasRef} role="img" aria-label={`Isometric hall replay: ${poses.length} students inside. Select a station label for its menu.`}
        onClick={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect();
          const x = event.clientX - bounds.left, y = event.clientY - bounds.top;
          let nearest: Pose | undefined, distance = 12;
          for (const p of poses) {
            const [px, py] = project(...p.xy, .7);
            const d = Math.hypot(px * scale + offsetX - x, py * scale + offsetY - y);
            if (d < distance) { nearest = p; distance = d; }
          }
          if (nearest) onStudent(nearest.id);
        }} />
      <svg className="hall-label-leaders" width="100%" height="100%" aria-hidden>
        {labels.map((label) => <line key={label.id} x1={label.x + label.w / 2} y1={label.y + label.h} x2={label.anchorX} y2={label.anchorY + 16} />)}
      </svg>
      {labels.map((label) => (
        <button key={label.id} type="button" className={`hall-map-label${selectedStation === label.id ? " selected" : ""}`}
          style={{ left: label.x, top: label.y, width: label.w }} onClick={() => onStation(label.id)}
          aria-label={`${label.label}: ${label.line} in line${label.empty ? `, ${label.empty} empty pans` : ""}`}>
          <span>{label.label}</span><b>{label.line}</b>{label.empty > 0 && <i aria-hidden>!</i>}
        </button>
      ))}
    </div>
  );
}
