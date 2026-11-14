import { useEffect, useRef, useState } from "react";
import { clock } from "../lib/format";

export type LiveAlert = { id: string; station: string; title: string; message: string; kind: "empty" | "queue" };

/** One contextual notification at a time, only while the replay is running.
 * Dismissed conditions stay quiet until they clear; a cooldown avoids flooding. */
export function ReplayAlert({ alerts, playing, time, onInspect }: { alerts: LiveAlert[]; playing: boolean; time: number; onInspect: (station: string) => void }) {
  const [popup, setPopup] = useState<(LiveAlert & { time: number }) | null>(null);
  const announced = useRef(new Set<string>());
  const recent = useRef(new Map<string, number>());
  const previous = useRef(time);
  const lastPopup = useRef(-Infinity);
  useEffect(() => {
    // Scrubbing back is a new viewing pass; ordinary pause/resume is not.
    if (time < previous.current - .1) { announced.current.clear(); recent.current.clear(); lastPopup.current = -Infinity; setPopup(null); }
    previous.current = time;
    const active = new Set(alerts.map((a) => a.id));
    for (const id of announced.current) if (!active.has(id)) announced.current.delete(id);
    if (popup && !active.has(popup.id)) { setPopup(null); return; }
    if (!playing || popup) return;
    const next = alerts.find((a) => !announced.current.has(a.id) && time - (recent.current.get(a.id) ?? -Infinity) >= 15);
    if (next && (next.kind === "empty" || time - lastPopup.current >= 5)) {
      announced.current.add(next.id); recent.current.set(next.id, time); lastPopup.current = time;
      setPopup({ ...next, time });
    }
  }, [alerts, playing, time, popup]);
  useEffect(() => {
    if (!popup) return;
    const timeout = window.setTimeout(() => setPopup(null), 10000);
    return () => window.clearTimeout(timeout);
  }, [popup]);
  if (!popup) return null;
  return <aside className={`replay-alert ${popup.kind}`} aria-label="Simulation alert">
    <button className="replay-alert-close" aria-label="Dismiss simulation alert" onClick={() => setPopup(null)}>×</button>
    <div role="status"><small>In this replay · {clock(popup.time)}</small><h3>{popup.title}</h3><p>{popup.message}</p></div>
    <button className="link" onClick={() => { onInspect(popup.station); setPopup(null); }}>View station <span aria-hidden>→</span></button>
  </aside>;
}
