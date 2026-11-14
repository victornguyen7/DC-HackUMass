# Shortcuts from ENGINE.md. Run from the repo root.
ENGINE := cd engine && uv run

.PHONY: web web-test web-test-api web-build prepare test serve waste backtest tsdb-load calibrate tsdb-compress tsdb-bench demo-bundle demo-bundle-quick cache-clean personas personas-adopt narrate

prepare:            ## real menus -> per-date menus + baseline kitchen plans
	$(ENGINE) dcsim prepare

test:
	$(ENGINE) pytest -q

serve:              ## FastAPI on :8000 (docs at /docs)
	$(ENGINE) dcsim serve

waste:              ## SYNTHETIC recorded waste history -> data/synthetic/worcester
	$(ENGINE) dcsim waste-gen

backtest: waste     ## "if we had used the plan" on every recorded night
	$(ENGINE) dcsim backtest

# Tiger Data (docs/TIGER.md). Needs DCSIM_TSDB_URL in engine/.env, or a saved password: tiger db save-password <id>
tsdb-load: waste    ## schema + observed (SYNTHETIC) history -> Tiger
	$(ENGINE) dcsim tsdb load

calibrate: tsdb-load    ## fit on weeks 1-4, forecast + score held-out weeks 5-6; sims and scores -> Tiger (~40 s)
	$(ENGINE) dcsim calibrate

tsdb-compress:      ## convert older chunks to the Tiger columnstore and print sizes
	$(ENGINE) dcsim tsdb compress

tsdb-bench:         ## time the same questions on the raw hypertables and on their continuous aggregates
	$(ENGINE) dcsim tsdb bench

demo-bundle: backtest   ## static bundle for an offline demo (~4 min, ~15 MB) -> web/public/cache
	$(ENGINE) dcsim bundle --grid

demo-bundle-quick:  ## presets only, no lever grid
	$(ENGINE) dcsim bundle

cache-clean:        ## drop results from older engine versions
	$(ENGINE) dcsim cache prune

# LLM layer (ENGINE.md §7). LLM_PROVIDER=groq (default when GROQ_API_KEY is in engine/.env) | mock
personas:           ## PAUSED (LLM.md): needs DCSIM_PERSONAS_LLM=1
	$(ENGINE) dcsim personas generate

personas-adopt:     ## reviewed personas_raw.json -> personas.json (invalidates cached results)
	$(ENGINE) dcsim personas adopt

narrate:            ## narrate the highlight students on Nov 13's median night
	$(ENGINE) dcsim narrate

# Web UI (web/README.md). Reads web/public/cache; proxies /api to `make serve` when it's running.
web:                ## dev server on :5173
	cd web && npm run dev

web-test:           ## unit tests against the demo bundle
	cd web && npm test

web-test-api:       ## browser test of the API-backed UI (needs `make serve` and `make web` running)
	cd web && node scripts/smoke-api.mjs http://localhost:5173 smoke-api-out

web-build:
	cd web && npm run build
