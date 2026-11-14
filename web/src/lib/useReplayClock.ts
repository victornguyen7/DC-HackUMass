import { useCallback, useEffect, useRef, useState } from "react";

/** The replay clock advances through the trace, independently of the selected panel. */
export function useReplayClock(start: number, end: number, initial: number) {
  const clamp = (value: number) => Math.max(start, Math.min(end, value));
  const [time, setTime] = useState(() => clamp(initial));
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(30);
  const timeRef = useRef(time);
  timeRef.current = time;

  const seek = useCallback((value: number) => {
    setPlaying(false);
    const next = Math.max(start, Math.min(end, value));
    timeRef.current = next;
    setTime(next);
  }, [start, end]);

  const toggle = useCallback(() => {
    if (timeRef.current >= end) {
      timeRef.current = start;
      setTime(start);
    }
    setPlaying((value) => !value);
  }, [start, end]);

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    let previous = performance.now();
    let elapsed = 0;
    const tick = (now: number) => {
      elapsed += Math.max(0, Math.min(now - previous, 250));
      previous = now;
      if (elapsed >= 1000 / 30) {
        const next = Math.min(end, timeRef.current + elapsed / 1000 * speed / 60);
        elapsed = 0;
        timeRef.current = next;
        setTime(next);
        if (next >= end) { setPlaying(false); return; }
      }
      frame = requestAnimationFrame(tick);
    };
    const onVisibility = () => { if (document.hidden) setPlaying(false); };
    document.addEventListener("visibilitychange", onVisibility);
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [playing, speed, end]);

  return { time, timeRef, playing, setPlaying, speed, setSpeed, seek, toggle };
}
