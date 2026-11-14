"""FastAPI backend (SPEC §9, ENGINE.md §8). Run with `uv run dcsim serve`.

    POST /api/simulate            -> 200 {status: done, result} if cached, else 202 {status: running, job_id}
    GET  /api/jobs/{id}/stream    -> Server-Sent Events: progress, provisional, final, error
    GET  /api/jobs/{id}           -> job status + latest result
    GET  /api/results/{hash}      -> result from cached nights (?include=series: per-minute bands)
    POST /api/compare             -> paired comparison of two cached configs
    GET  /api/traces/{hash}       -> median-night (or ?seed=) trace for 3D, gzipped JSON
    GET  /api/dates | /api/menu/{date} | /api/arrivals/{date} | /api/layout | /api/presets | /api/facts/waste
    GET  /api/waste/history | /api/waste/backtest      (SYNTHETIC recorded history + "if we had used the plan")
    GET  /api/waste/calibration                         held-out forecast check (`dcsim calibrate`, Tiger Data)
    GET/POST/DELETE /api/scenarios, POST /api/scenarios/{id}/adopt   (adopting logs a forecast to the Tiger ledger)
    GET  /api/forecasts?date=                           forecast ledger, scored once the night is observed
    LLM layer (ENGINE.md §7; LLM_PROVIDER=gemini|groq|mock, template fallbacks):
    GET  /api/ai/status                              provider, models, backups, load limits
    POST /api/ai/similar-dishes                     menu dishes like a new one (Gemini embeddings, keyword fallback)
    GET  /api/traces/{hash}/highlights?seed=        students worth clicking (missed meal, came back, ...)
    GET  /api/narrate/{hash}/{seed}/{student}       SSE: card (timeline, instant), sentence..., final
    POST /api/ask                                   plain English -> lever settings (not run)
    POST /api/ai/dish-prior                         persona affinities for a new dish ("AI estimate")
    POST /api/explain                               why a scenario won or lost (from cached nights)

The browser never simulates; it plays back results. Identical in-flight
requests join one job (ENGINE.md §5.5).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .ai import LLM, LLMError
from .ai.narrate import TRACES, Narrator
from .ai.narrate import highlights as night_highlights
from .config import ENGINE_VERSION, ScenarioConfig
from .config import config_hash as hash_of
from .data import available_dates, default_data_dir, load_baseline, repo_root
from .levers import Levers, NewDish, ScenarioRequest, build_config
from .presets import PRESETS
from .runner import median_night
from .reports import arrivals as arrivals_report
from .reports import clock
from .service import Service, baseline_of
from .store import Store

# ---------------------------------------------------------------- jobs


class Job:
    def __init__(self, job_id: str, cfg: ScenarioConfig, n: int):
        self.id, self.cfg, self.n = job_id, cfg, n
        self.hash = hash_of(cfg)
        self.status: Literal["running", "done", "error"] = "running"
        self.events: list[dict] = []
        self.result: dict | None = None
        self.error: str | None = None
        self._changed = asyncio.Condition()

    async def emit(self, event: str, data: dict) -> None:
        async with self._changed:
            self.events.append({"event": event, "data": data})
            self._changed.notify_all()

    async def follow(self):
        """Replay every event so far, then wait for new ones until the job ends."""
        i = 0
        while True:
            async with self._changed:
                while i >= len(self.events) and self.status == "running":
                    await self._changed.wait()
                pending, i = self.events[i:], len(self.events)
                finished = self.status != "running"
            for e in pending:
                yield e
            if finished and i >= len(self.events):
                return

    def public(self) -> dict:
        return {"job_id": self.id, "status": self.status, "config_hash": self.hash, "n_nights": self.n,
                "result": self.result, "error": self.error}


class Jobs:
    def __init__(self, svc: Service):
        self.svc = svc
        self.by_id: dict[str, Job] = {}
        self.running: dict[tuple[str, int], Job] = {}

    def start(self, cfg: ScenarioConfig, n: int) -> Job:
        key = (hash_of(cfg), n)
        if key in self.running:  # in-flight dedupe
            return self.running[key]
        job = Job(uuid.uuid4().hex[:12], cfg, n)
        self.by_id[job.id] = job
        self.running[key] = job
        asyncio.create_task(self._run(job, key))
        return job

    async def _run(self, job: Job, key: tuple[str, int]) -> None:
        svc, cfg, n = self.svc, job.cfg, job.n
        try:
            base = baseline_of(cfg)
            is_base = hash_of(base) == job.hash
            plans = [] if is_base else [(base, svc.batches(base, n))]
            plans.append((cfg, svc.batches(cfg, n)))
            total = sum(len(b) for _, bs in plans for b in bs)
            done = 0
            await job.emit("progress", {"done": 0, "total": total})
            for c, bs in plans:
                for b in bs:
                    await asyncio.to_thread(svc.run_batch, c, b)
                    done += len(b)
                    await job.emit("progress", {"done": done, "total": total})
                    if c is cfg and done < total:
                        nights = svc.cached_nights(cfg, n)
                        base_n = None if is_base else svc.cached_nights(base, len(nights))
                        await job.emit("provisional", svc.result(cfg, nights, base_n, provisional=True))
            nights = svc.cached_nights(cfg, n)
            job.result = svc.result(cfg, nights, None if is_base else svc.cached_nights(base, n))
            job.status = "done"
            await job.emit("final", job.result)
            # Warm the 3D trace for the median night in the background.
            asyncio.create_task(asyncio.to_thread(svc.trace_bytes, job.hash, job.result["median_seed"]))
        except Exception as e:  # surfaced to the client as an SSE `error`
            job.status, job.error = "error", f"{type(e).__name__}: {e}"
            await job.emit("error", {"message": job.error})
        finally:
            self.running.pop(key, None)


# ---------------------------------------------------------------- app


def _workers() -> int:
    return int(os.environ.get("DCSIM_WORKERS", os.cpu_count() or 1))


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = Store()
    pool = ProcessPoolExecutor(max_workers=_workers()) if _workers() > 1 else None
    svc = Service(store, pool)
    app.state.svc, app.state.jobs = svc, Jobs(svc)
    app.state.llm = LLM(store)
    app.state.narrator = Narrator(svc, app.state.llm)
    warm = None
    if os.environ.get("DCSIM_WARM") == "1":  # precompute baseline + presets for every date (ENGINE.md §6.1)
        warm = asyncio.create_task(_warm(svc))
    yield
    if warm:
        warm.cancel()
    if pool:
        pool.shutdown(cancel_futures=True)


async def _warm(svc: Service) -> None:
    for date in available_dates():
        for preset in [None, *PRESETS]:
            cfg = build_config(ScenarioRequest(date=date, preset=preset))
            for b in svc.batches(cfg, 30):
                await asyncio.to_thread(svc.run_batch, cfg, b)


app = FastAPI(title="DC What-If API", version=ENGINE_VERSION, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("DCSIM_CORS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


def _svc(request: Request) -> Service:
    return request.app.state.svc


def _bad(e: Exception) -> HTTPException:
    return HTTPException(422, str(e))


def _cfg_or_404(svc: Service, h: str) -> ScenarioConfig:
    cfg = svc.store.get_config(h)
    if cfg is None:
        raise HTTPException(404, f"unknown config {h}")
    return cfg


# ------------------------------------------------------------ reference data


@app.get("/api/health")
def health(request: Request) -> dict:
    return {"ok": True, "engine_version": ENGINE_VERSION, "cache": _svc(request).store.stats()}


def _calendar() -> dict:
    p = default_data_dir() / "calendar.json"
    return {r["date"]: r for r in json.loads(p.read_text())["dates"]} if p.exists() else {}


@app.get("/api/dates")
def dates() -> list[dict]:
    cal = _calendar()
    return [{"date": d} | {k: v for k, v in cal.get(d, {}).items() if k != "date"} for d in available_dates()]


@app.get("/api/menu/{date}")
def menu(date: str) -> dict:
    try:
        cfg = load_baseline(date)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    raw = json.loads((default_data_dir() / "menus" / f"{date}_dinner.json").read_text())
    return {
        "date": date, "meal": cfg.meal, "open": clock(cfg.open_min), "close": clock(cfg.service_end),
        "dinner_close": clock(cfg.close_min),
        "stations": [{"id": s.id, "name": s.name, "to_order": s.to_order, "self_serve": s.self_serve, "servers": s.servers}
                     for s in cfg.stations],
        "dishes": [d.model_dump() | {"plan": cfg.kitchen[d.id].model_dump() if d.id in cfg.kitchen else None}
                   for d in cfg.menu],
        "addons": raw.get("addons", []),
        "calendar": _calendar().get(date),
    }


@app.get("/api/arrivals/{date}")
def arrivals(date: str, crowd_multiplier: float = Query(1.0, gt=0, le=3), window_min: int = Query(10, ge=5, le=60)) -> dict:
    try:
        cfg = build_config(ScenarioRequest(date=date, levers=Levers(crowd_multiplier=crowd_multiplier)))
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, str(e)) from e
    rows = arrivals_report(cfg, window_min)
    return {"date": date, "expected_students": round(sum(r["expected"] for r in rows)), "windows": rows,
            "note": "Dinner only. Crowd size and wave shape are estimates; see data/worcester/ASSUMPTIONS.md"}


@app.get("/api/layout")
def layout() -> dict:
    return json.loads((default_data_dir() / "layout" / "floor_plan.json").read_text())


@app.get("/api/presets")
def presets() -> list[dict]:
    return [{"id": k, "description": (f.__doc__ or "").strip().splitlines()[0] if f.__doc__ else k} for k, f in PRESETS.items()]


@app.get("/api/facts/waste")
def waste_facts() -> dict:
    """Published campus waste figures (recorded, not simulated); never mix with simulated numbers."""
    src = repo_root() / "data" / "sources" / "waste"
    return {
        "kind": "published",
        "oz_per_swipe": json.loads((src / "oz_per_swipe.json").read_text()),
        "destinations_and_targets": json.loads((src / "destinations_and_targets.json").read_text()),
    }


@app.get("/api/waste/history")
def waste_history_endpoint() -> dict:
    """SYNTHETIC recorded history (MetaFoodX/LeanPath shape). Hidden generator parameters are never served."""
    from .backtest import waste_history

    h = waste_history()
    if not h["nights"]:
        raise HTTPException(404, "no synthetic history; run `dcsim waste-gen`")
    return h


@app.get("/api/waste/backtest")
def waste_backtest() -> dict:
    """Cached back-test of the recommended plan on every recorded night (`dcsim backtest` refreshes it)."""
    from .backtest import backtest_path

    p = backtest_path()
    if not p.exists():
        raise HTTPException(404, "no back-test yet; run `dcsim backtest`")
    return json.loads(p.read_text())


@app.get("/api/waste/calibration")
def waste_calibration() -> dict:
    """Fit on early recorded weeks, scored on held-out weeks (`dcsim calibrate` refreshes it)."""
    from .calibration import public_report

    r = public_report()
    if r is None:
        raise HTTPException(404, "no calibration yet; run `dcsim calibrate`")
    return r


# ------------------------------------------------------------ simulation


@app.post("/api/simulate")
async def simulate(req: ScenarioRequest, request: Request):
    svc: Service = _svc(request)
    try:
        cfg = build_config(req)
    except (FileNotFoundError, ValueError) as e:
        raise _bad(e) from e
    h = svc.store.put_config(cfg)
    base = baseline_of(cfg)
    if svc.is_cached(cfg, req.n_nights) and (hash_of(base) == h or svc.is_cached(base, req.n_nights)):
        svc.store.touch(h)
        base_n = None if hash_of(base) == h else svc.cached_nights(base, req.n_nights)
        return {"status": "done", "config_hash": h, "result": svc.result(cfg, svc.cached_nights(cfg, req.n_nights), base_n)}
    job = request.app.state.jobs.start(cfg, req.n_nights)
    return JSONResponse(status_code=202, content={"status": "running", "job_id": job.id, "config_hash": h,
                                                  "stream": f"/api/jobs/{job.id}/stream"})


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, request: Request) -> dict:
    job = request.app.state.jobs.by_id.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    return job.public()


@app.get("/api/jobs/{job_id}/stream")
async def job_stream(job_id: str, request: Request):
    job = request.app.state.jobs.by_id.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")

    async def sse():
        async for e in job.follow():
            yield f"event: {e['event']}\ndata: {json.dumps(e['data'], separators=(',', ':'))}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/results/{config_hash}")
def result(
    config_hash: str, request: Request, n_nights: int = Query(30, ge=1, le=200),
    include: str | None = Query(None, pattern="^series$"),
) -> dict:
    """`?include=series` adds per-minute p10/p50/p90 lines, pan levels and students inside
    (IMPROVEMENTS 1.4). It re-runs the nights, so it takes a second or two."""
    svc = _svc(request)
    cfg = _cfg_or_404(svc, config_hash)
    nights = svc.cached_nights(cfg, n_nights)
    if len(nights) < n_nights:
        raise HTTPException(404, f"only {len(nights)} of {n_nights} nights cached; POST /api/simulate first")
    base = baseline_of(cfg)
    base_n = svc.cached_nights(base, n_nights) if config_hash != hash_of(base) else None
    out = svc.result(cfg, nights, base_n or None)
    if include == "series":
        from .runner import series_summary

        out["series"] = series_summary(cfg, [m.seed for m in nights])
    return out


class CompareRequest(BaseModel):
    baseline_hash: str
    scenario_hash: str
    n_nights: int = Field(30, ge=1, le=200)


@app.post("/api/compare")
def compare(req: CompareRequest, request: Request) -> dict:
    try:
        return _svc(request).compare_hashes(req.baseline_hash, req.scenario_hash, req.n_nights)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/traces/{config_hash}")
async def trace(config_hash: str, request: Request, seed: int | None = Query(None, ge=1)):
    svc = _svc(request)
    cfg = _cfg_or_404(svc, config_hash)
    if seed is None:
        nights = svc.store.get_nights(config_hash)
        if not nights:
            raise HTTPException(404, "no nights cached; POST /api/simulate first")
        seed = median_night(nights)
    data = await asyncio.to_thread(svc.trace_bytes, config_hash, seed)
    if data is None:
        raise HTTPException(404, "trace unavailable")
    return Response(data, media_type="application/json", headers={
        "Content-Encoding": "gzip", "X-Night-Seed": str(seed), "X-Config-Label": cfg.label})


# ------------------------------------------------------------ saved / adopted scenarios


class ScenarioSave(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    config_hash: str
    status: Literal["saved", "adopted"] = "saved"


@app.get("/api/scenarios")
def list_scenarios(request: Request, date: str | None = None) -> list[dict]:
    return _svc(request).store.list_scenarios(date)


def _log_forecast(svc: Service, cfg: ScenarioConfig, name: str, scenario_id: str) -> None:
    """Adopting a plan writes its forecast to the append-only Tiger ledger before the night (docs/TIGER.md)."""
    import logging

    from . import tsdb
    from .calibration import issue_forecasts

    try:
        with tsdb.connect() as conn:
            issue_forecasts(svc, conn, cfg, label=name, scenario_id=scenario_id)
    except Exception as e:  # never break adoption because the ledger is unreachable
        logging.getLogger("dcsim").warning("forecast ledger: %s", e)


def _queue_forecast(s: dict, svc: Service, tasks: BackgroundTasks) -> dict:
    from . import tsdb

    if not tsdb.configured():
        return s | {"forecast": "not logged (Tiger not configured)"}
    cfg = svc.store.get_config(s["config_hash"])
    if cfg is None:
        return s | {"forecast": "not logged (unknown config)"}
    tasks.add_task(_log_forecast, svc, cfg, s["name"], s["scenario_id"])
    return s | {"forecast": "logging to Tiger ledger"}


@app.post("/api/scenarios", status_code=201)
def save_scenario(body: ScenarioSave, request: Request, tasks: BackgroundTasks) -> dict:
    svc = _svc(request)
    cfg = _cfg_or_404(svc, body.config_hash)
    s = svc.store.save_scenario(body.name, body.config_hash, body.status, cfg.date)
    return _queue_forecast(s, svc, tasks) if body.status == "adopted" else s


@app.post("/api/scenarios/{scenario_id}/adopt")
def adopt_scenario(scenario_id: str, request: Request, tasks: BackgroundTasks) -> dict:
    svc = _svc(request)
    s = svc.store.adopt_scenario(scenario_id)
    if not s:
        raise HTTPException(404, "unknown scenario")
    return _queue_forecast(s, svc, tasks)


@app.get("/api/forecasts")
def forecasts(date: str | None = None) -> list[dict]:
    """The forecast ledger: every forecast logged before its night, scored once the night is observed."""
    from . import tsdb

    if not tsdb.configured():
        raise HTTPException(503, "Tiger not configured (DCSIM_TSDB_URL or TIGER_SERVICE_ID in engine/.env)")
    try:
        with tsdb.connect() as conn:
            return tsdb.scorecard(conn, date)
    except Exception as e:
        raise HTTPException(503, f"Tiger unreachable: {str(e)[:200]}") from e


@app.delete("/api/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: str, request: Request) -> Response:
    if not _svc(request).store.delete_scenario(scenario_id):
        raise HTTPException(404, "unknown scenario")
    return Response(status_code=204)


# ------------------------------------------------------------ LLM layer (ENGINE.md §7)


def _llm(request: Request) -> LLM:
    return request.app.state.llm


@app.get("/api/ai/status")
def ai_status(request: Request) -> dict:
    return _llm(request).status() | {"note": "LLM output never changes simulated numbers except through inputs you can see "
                                           "(personas, new-dish affinities, lever settings)."}


def _seed_or_median(svc: Service, h: str, seed: int | None) -> int:
    if seed is not None:
        return seed
    nights = svc.store.get_nights(h)
    if not nights:
        raise HTTPException(404, "no nights cached; POST /api/simulate first")
    return median_night(nights)


@app.get("/api/traces/{config_hash}/highlights")
async def trace_highlights(config_hash: str, request: Request, seed: int | None = Query(None, ge=1),
                           k: int = Query(12, ge=1, le=50)) -> dict:
    svc = _svc(request)
    cfg = _cfg_or_404(svc, config_hash)
    seed = _seed_or_median(svc, config_hash, seed)
    trace = await asyncio.to_thread(TRACES.get, svc, config_hash, seed)
    if trace is None:
        raise HTTPException(404, "trace unavailable")
    return {"config_hash": config_hash, "seed": seed, "students": night_highlights(cfg, trace, k)}


@app.get("/api/narrate/{config_hash}/{seed}/{student_id}")
async def narrate(config_hash: str, seed: int, student_id: int, request: Request, stream: bool = True):
    narrator: Narrator = request.app.state.narrator
    try:
        await asyncio.to_thread(narrator.load, config_hash, seed, student_id)  # 404 before the stream opens
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    if not stream:
        out: dict = {}
        async for kind, data in narrator.narrate(config_hash, seed, student_id):
            if kind == "card":
                out["card"] = data
            elif kind == "final":
                out |= data
        return out

    async def sse():
        async for kind, data in narrator.narrate(config_hash, seed, student_id):
            yield f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    date: str | None = None


@app.post("/api/ask")
def ask_endpoint(body: AskRequest, request: Request) -> dict:
    from .ai.ask import ask

    date = body.date or available_dates()[0]
    if date not in available_dates():
        raise HTTPException(404, f"no menu for {date}")
    try:
        return ask(_llm(request), body.question, date)
    except LLMError as e:
        raise HTTPException(503, f"LLM unavailable: {e}") from e


class DishPriorRequest(BaseModel):
    date: str | None = None
    dish: NewDish


@app.post("/api/ai/dish-prior")
def dish_prior_endpoint(body: DishPriorRequest, request: Request) -> dict:
    from .ai.dish_prior import estimate

    try:
        return estimate(_llm(request), body.date or available_dates()[0], body.dish)
    except (ValueError, FileNotFoundError) as e:
        raise _bad(e) from e
    except LLMError as e:
        raise HTTPException(503, f"LLM unavailable: {e}") from e


class SimilarRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    date: str | None = None
    station: str | None = None
    role: str | None = None
    k: int = Field(3, ge=1, le=10)


@app.post("/api/ai/similar-dishes")
def similar_dishes_endpoint(body: SimilarRequest, request: Request) -> dict:
    from .ai.similar import similar_dishes

    try:
        return similar_dishes(_llm(request), body.date or available_dates()[0], body.name, body.station, body.role, body.k)
    except (ValueError, FileNotFoundError) as e:
        raise _bad(e) from e


class ExplainRequest(BaseModel):
    scenario_hash: str
    baseline_hash: str | None = None  # default: the scenario's own baseline
    n_nights: int = Field(30, ge=1, le=200)


@app.post("/api/explain")
def explain_endpoint(body: ExplainRequest, request: Request) -> dict:
    from .ai.explain import default_baseline, explain

    svc = _svc(request)
    try:
        bh = body.baseline_hash or default_baseline(svc, body.scenario_hash)
        if bh == body.scenario_hash:
            raise HTTPException(422, "scenario is the baseline; nothing to explain")
        return explain(_llm(request), svc, bh, body.scenario_hash, body.n_nights)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'")) from e
    except LLMError as e:
        raise HTTPException(503, f"LLM unavailable: {e}") from e
