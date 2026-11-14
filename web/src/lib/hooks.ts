import { useEffect, useState } from "react";
import { apiUp, findEntry, loadApiResult, loadManifest, loadResult } from "./data";
import type { Manifest, ManifestEntry, Result } from "./types";
import type { Scenario } from "./store";

export type Async<T> = { data: T | undefined; error: Error | undefined; loading: boolean };

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): Async<T> {
  const [s, set] = useState<Async<T>>({ data: undefined, error: undefined, loading: true });
  useEffect(() => {
    let live = true;
    set((p) => ({ data: p.data, error: undefined, loading: true }));
    fn().then(
      (data) => live && set({ data, error: undefined, loading: false }),
      (error) => live && set({ data: undefined, error, loading: false }),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return s;
}

export const useManifest = () => useAsync<Manifest>(loadManifest, []);

/** True when the engine API answers (re-checked every 15 s). */
export const useApiUp = () => useAsync<boolean>(apiUp, []).data ?? false;

export type ScenarioData = {
  entry: ManifestEntry;
  result: Result;
  /** The requested scenario couldn't be loaded; this is the date's baseline. */
  fallback: boolean;
  /** Why it fell back, for the banner. */
  reason?: string;
  source: "bundle" | "api";
};

/** Bundle entry + result for a date and scenario. An API scenario (sc.hash) is
 * read from the API, or from the copy kept when it ran. Falls back to the date's baseline. */
export function useScenarioResult(date: string, sc: Scenario) {
  return useAsync<ScenarioData | null>(async () => {
    const m = await loadManifest();
    const base = findEntry(m, date, null, "");
    if (sc.hash) {
      try {
        const result = await loadApiResult(sc.hash);
        if (result.date === date) {
          const entry: ManifestEntry = { key: `api|${sc.hash}`, date, preset: sc.preset, lever: "api", label: result.label,
            config_hash: sc.hash, file: "", trace: null, narration: null };
          return { entry, result, fallback: false, source: "api" };
        }
      } catch (e) {
        if (!base) return null;
        return { entry: base, result: await loadResult(base), fallback: true, source: "bundle",
          reason: `"${sc.label ?? "This scenario"}" was simulated on the API, which isn't answering (${(e as Error).message}). Start it with \`make server\`.` };
      }
    }
    let entry = sc.hash ? undefined : findEntry(m, date, sc.preset, sc.lever);
    let fallback = false;
    if (!entry) {
      entry = base;
      fallback = !!(sc.preset || sc.lever);
    }
    if (!entry) return null;
    return { entry, result: await loadResult(entry), fallback, source: "bundle",
      reason: fallback ? `This scenario isn't in the offline bundle for this date; showing the baseline.` : undefined };
  }, [date, sc.preset, sc.lever, sc.hash]);
}
