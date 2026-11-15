import { useEffect, useId, useRef, useState } from "react";
import { stationName } from "../lib/format";
import type { HallSceneController, SceneData } from "../lib/hall3d";
import type { LabelPosition } from "../lib/hallSceneInteraction";

export type { SceneStation } from "../lib/hall3d";
type Props = SceneData & { onStation: (id: string) => void; onStudent: (id: number) => void; onTopDown: () => void };

/** WebGL loads only when the 3D view is opened. Playback never rebuilds the scene. */
export function HallScene(props: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<HallSceneController | null>(null);
  const latest = useRef(props); latest.current = props;
  const labelsRef = useRef(new Map<string, HTMLButtonElement>());
  const linesRef = useRef(new Map<string, SVGLineElement>());
  const [status, setStatus] = useState<"loading" | "ready" | "unavailable">("loading");
  const [mode, setMode] = useState<"rotate" | "pan">("rotate");
  const [labels, setLabels] = useState(true);
  const [help, setHelp] = useState(false);
  const helpId = useId();

  useEffect(() => {
    let cancelled = false;
    function placeLabels(positions: LabelPosition[]) {
      for (const p of positions) {
        const button = labelsRef.current.get(p.id), line = linesRef.current.get(p.id);
        if (button) {
          button.hidden = !p.visible;
          button.style.transform = `translate(${p.left}px, ${p.top}px)`;
          button.style.width = `${p.width}px`;
        }
        if (line) {
          line.style.display = p.visible ? "" : "none";
          line.setAttribute("x1", String(p.left + p.width / 2)); line.setAttribute("y1", String(p.top + p.height));
          line.setAttribute("x2", String(p.x)); line.setAttribute("y2", String(p.y));
        }
      }
    }
    if (!window.WebGL2RenderingContext) { setStatus("unavailable"); return; }
    import("../lib/hall3d").then(({ createHallScene }) => {
      if (cancelled || !canvasRef.current) return;
      try {
        sceneRef.current = createHallScene(canvasRef.current, {
          onStation: (id) => latest.current.onStation(id), onStudent: (id) => latest.current.onStudent(id),
          onLabels: placeLabels, onUnavailable: () => { if (!cancelled) setStatus("unavailable"); },
        });
        sceneRef.current.update(latest.current);
        setStatus("ready");
      } catch { sceneRef.current?.dispose(); sceneRef.current = null; setStatus("unavailable"); }
    }).catch(() => { if (!cancelled) setStatus("unavailable"); });
    return () => { cancelled = true; sceneRef.current?.dispose(); sceneRef.current = null; };
  }, []);

  useEffect(() => { sceneRef.current?.update(props); }, [props.poses, props.stations, props.selectedStation, props.selectedStudent, props.colorBy, props.missed]);
  useEffect(() => { if (status === "ready") sceneRef.current?.refreshLabels(); }, [status]);

  return <div className="hall-canvas-wrap hall-3d" data-labels={labels} data-mode={mode}>
    <canvas ref={canvasRef} tabIndex={status === "ready" ? 0 : -1} role="img" aria-label="Interactive 3D hall. Drag to rotate; pinch or scroll to zoom. Arrow keys rotate, plus and minus zoom, Home resets." aria-describedby={help ? helpId : undefined} hidden={status === "unavailable"} />
    {status !== "ready" && <div className="hall-scene-placeholder" role="status">
      <span className="hall-scene-symbol" aria-hidden>◇</span>
      <strong>{status === "loading" ? "Opening the 3D hall…" : "3D isn’t available on this device"}</strong>
      <p>{status === "loading" ? "Getting your view ready." : "You can still explore every station and play the simulation in the top-down view."}</p>
      {status === "unavailable" && <button className="hall-scene-fallback" onClick={props.onTopDown}>Open top-down view</button>}
    </div>}
    {status === "ready" && <>
      <div className="hall-3d-labels">
        <svg className="hall-label-leaders" width="100%" height="100%" aria-hidden>
          {props.stations.filter((s) => s.id !== "breads").map((s) => <line key={s.id} ref={(node) => { if (node) linesRef.current.set(s.id, node); else linesRef.current.delete(s.id); }} />)}
        </svg>
        {props.stations.filter((s) => s.id !== "breads").map((s) => {
          const label = s.id === "desserts" ? "Desserts · Breads" : stationName(s.id);
          return <button key={s.id} type="button" className={`hall-map-label${props.selectedStation === s.id ? " selected" : ""}`} hidden
            ref={(node) => { if (node) labelsRef.current.set(s.id, node); else labelsRef.current.delete(s.id); }}
            onClick={() => props.onStation(s.id)} aria-label={`${label}: ${s.line} in line${s.empty ? `, ${s.empty} empty pans` : ""}`}>
            <span>{label}</span><b>{s.line}</b>{s.empty > 0 && <i aria-hidden>!</i>}
          </button>;
        })}
      </div>
      <div className="hall-3d-toolbar" aria-label="3D camera controls">
        <div className="hall-3d-modes" role="group" aria-label="Drag action">
          <button aria-pressed={mode === "rotate"} onClick={() => { setMode("rotate"); sceneRef.current?.setMode("rotate"); }}><CameraIcon kind="rotate" />Rotate</button>
          <button aria-pressed={mode === "pan"} onClick={() => { setMode("pan"); sceneRef.current?.setMode("pan"); }}><CameraIcon kind="move" />Move</button>
        </div>
        <div className="hall-3d-tools">
          <button className="camera-icon-button" aria-label="Zoom out" title="Zoom out" onClick={() => sceneRef.current?.zoom(1 / 1.25)}><CameraIcon kind="minus" /></button>
          <button className="camera-icon-button" aria-label="Zoom in" title="Zoom in" onClick={() => sceneRef.current?.zoom(1.25)}><CameraIcon kind="plus" /></button>
          <span className="camera-divider" />
          <button className="camera-reset" onClick={() => sceneRef.current?.reset()}><CameraIcon kind="reset" /><span>Reset</span></button>
          <button className="camera-label-toggle" aria-pressed={labels} aria-label="Show station labels" title="Show station labels" onClick={() => setLabels(!labels)}><CameraIcon kind="labels" /></button>
          <button className="camera-icon-button camera-help-toggle" aria-label="3D controls help" aria-expanded={help} aria-controls={helpId} onClick={() => setHelp(!help)}><CameraIcon kind="help" /></button>
        </div>
      </div>
      <p className="hall-3d-hint">{mode === "rotate" ? "Drag to explore" : "Drag to move"}<span> · Pinch or scroll to zoom</span></p>
      {help && <div className="hall-3d-help" id={helpId}>
        <div><strong>Make yourself at home</strong><button aria-label="Close 3D controls help" onClick={() => setHelp(false)}>×</button></div>
        <p><b>Touch</b> One finger rotates. Pinch to zoom. Use two fingers or Move to slide the hall.</p>
        <p><b>Mouse</b> Drag to rotate. Scroll to zoom. Right-drag or Move to slide.</p>
        <p><b>Keyboard</b> Focus the hall: arrows rotate, + / − zoom, Home resets.</p>
        <small>Tap a station or student to see their details.</small>
      </div>}
    </>}
  </div>;
}

function CameraIcon({ kind }: { kind: "rotate" | "move" | "minus" | "plus" | "reset" | "labels" | "help" }) {
  const paths = {
    rotate: <><path d="M19 8a8 8 0 1 0 1 8M19 3v5h-5" /><path d="m9 9 6 3-6 3Z" /></>,
    move: <path d="M12 3v18M3 12h18m-12-6 3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3m12-6 3 3-3 3" />,
    minus: <path d="M5 12h14" />, plus: <path d="M5 12h14M12 5v14" />,
    reset: <path d="M4 10a8 8 0 1 1 1 8M4 4v6h6" />,
    labels: <><path d="M4 4h16v12H9l-5 4V4Z" /><path d="M8 8h8M8 12h5" /></>,
    help: <><circle cx="12" cy="12" r="9" /><path d="M9.5 9a2.5 2.5 0 1 1 4.2 1.8c-1.1.8-1.7 1.2-1.7 2.7M12 17h.01" /></>,
  };
  return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>{paths[kind]}</svg>;
}
