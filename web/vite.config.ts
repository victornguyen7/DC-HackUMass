import { defineConfig } from "vitest/config";
import type { ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

// The app reads the static demo bundle in public/cache (no API needed).
// If `make server` is running, /api is proxied to it for runs that aren't cached
// (DCSIM_API_URL picks another API, e.g. the ledger-safe test API from `make server-test`).
// When the API is down, /api/health answers 200 {ok:false} instead of a proxy 500,
// so the pages can check for it without logging an error (UI_ISSUES U13).
const apiProxy: Record<string, ProxyOptions> = {
  "/api": {
    target: process.env.DCSIM_API_URL ?? "http://127.0.0.1:8000", // the test UI (make web-test-ui) points at :8001
    configure: (proxy) => {
      proxy.on("error", (_err, req, res) => {
        const r = res as import("node:http").ServerResponse;
        if (!("writeHead" in r) || r.headersSent) return;
        const isHealth = req.url?.startsWith("/api/health");
        r.writeHead(isHealth ? 200 : 503, { "Content-Type": "application/json" });
        r.end(JSON.stringify(isHealth ? { ok: false, reason: "API not running" } : { detail: "The simulation API isn't running. Start it with `make server`." }));
      });
    },
  },
};

export default defineConfig({
  plugins: [react()],
  server: { proxy: apiProxy },
  preview: { proxy: apiProxy },
  test: { environment: "jsdom", globals: true },
});
