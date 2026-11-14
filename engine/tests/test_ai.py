"""LLM layer: mock provider, checks, fallbacks, and the real SDK path against a fake HTTP server (no network)."""

from __future__ import annotations

import asyncio
import json

import groq
import httpx
import pytest

from dcsim.ai import LLM
from dcsim.ai import personas as P
from dcsim.ai.ask import ask
from dcsim.ai.dish_prior import estimate
from dcsim.ai.explain import check as explain_check
from dcsim.ai.narrate import TRACES, Narrator, card, check, highlights, template
from dcsim.config import Persona, config_hash
from dcsim.data import load_baseline
from dcsim.levers import Levers, NewDish, ScenarioRequest, build_config
from dcsim.runner import run_night
from dcsim.service import Service
from dcsim.store import Store

DATE = "2026-11-13"


@pytest.fixture(scope="module")
def night(tmp_path_factory):
    svc = Service(Store(tmp_path_factory.mktemp("cache")))
    cfg = load_baseline(DATE)
    h = svc.store.put_config(cfg)
    trace = TRACES.get(svc, h, 1)
    return svc, cfg, h, trace


# ---------------------------------------------------------------- fake Groq API


def _completion(text: str, finish: str = "stop") -> dict:
    return {"id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "qwen/qwen3.8-27b",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}


def _sse(chunks: list[str]) -> bytes:
    def chunk(delta: dict, finish=None) -> str:
        body = {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 0, "model": "qwen/qwen3.8-27b",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return f"data: {json.dumps(body)}\n\n"

    out = chunk({"role": "assistant", "content": ""}) + "".join(chunk({"content": c}) for c in chunks)
    return (out + chunk({}, "stop") + "data: [DONE]\n\n").encode()


class FakeAPI:
    """Records request bodies; answers with queued JSON texts or SSE text chunks."""

    def __init__(self, texts: list[str] | None = None, stream_chunks: list[str] | None = None):
        self.texts, self.chunks, self.requests = list(texts or []), stream_chunks, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"body": body, "headers": dict(request.headers)})
        if body.get("stream"):
            return httpx.Response(200, content=_sse(self.chunks), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=_completion(self.texts.pop(0)))

    def llm(self, store=None) -> LLM:
        t = httpx.MockTransport(self.handler)
        return LLM(store, provider="groq",
                   client=groq.Groq(api_key="test", max_retries=0, http_client=groq.DefaultHttpxClient(transport=t)),
                   async_client=groq.AsyncGroq(api_key="test", max_retries=0,
                                               http_client=groq.DefaultAsyncHttpxClient(transport=t)))


# ---------------------------------------------------------------- narration


def test_template_never_names_unseen_dishes(night):
    svc, cfg, h, trace = night
    picks = highlights(cfg, trace, 20)
    reasons = {p["reason"] for p in picks}
    assert "switched" in reasons
    if any(not any(e["type"] == "served" for e in s["events"]) for s in trace["students"]):
        assert "missed_meal" in reasons  # only when someone on this night left with nothing
    for p in picks:
        c = card(cfg, trace["_by_id"][p["student_id"]])
        text = template(c)
        assert check(text, c, cfg) == [], text
        assert 1 <= text.count(".") <= 4


def test_check_flags_dishes_outside_the_events(night):
    _, cfg, _, trace = night
    c = card(cfg, next(s for s in trace["students"] if any(e["type"] == "served" for e in s["events"])))
    unseen = next(d.name for d in cfg.menu if d.name not in c["got"] and all(d.name not in e["text"] for e in c["events"]))
    assert check(f"I got the {c['got'][0]}.", c, cfg) == []
    assert check(f"I got the {unseen}.", c, cfg) == [unseen]


def test_mock_narration_is_the_template(night):
    svc, cfg, h, trace = night
    sid = highlights(cfg, trace, 1)[0]["student_id"]
    out = Narrator(svc, LLM(svc.store, provider="mock")).narrate_sync(h, 1, sid)
    assert out["source"] == "template" and out["text"] == template(card(cfg, trace["_by_id"][sid]))


def test_llm_narration_streams_checks_and_caches(night):
    svc, cfg, h, trace = night
    s = next(s for s in trace["students"] if any(e["type"] == "served" for e in s["events"]))
    c = card(cfg, s)
    fake = FakeAPI(stream_chunks=["I grabbed the ", f"{c['got'][0]} pretty fast. ", "Then I sat down", " and ate."])
    narrator = Narrator(svc, fake.llm(svc.store))
    out = narrator.narrate_sync(h, 1, s["id"])
    assert out["source"] == "llm" and out["text"].startswith("I grabbed the")
    req = fake.requests[0]
    assert req["body"]["model"] == "qwen/qwen3.8-27b" and req["body"]["reasoning_effort"] == "none"
    assert c["events"][0]["clock"] in req["body"]["messages"][1]["content"]  # prompt = recorded events
    assert narrator.narrate_sync(h, 1, s["id"])["source"] == "cache" and len(fake.requests) == 1


def test_llm_narration_that_invents_a_dish_falls_back(night):
    svc, cfg, h, trace = night
    s = [s for s in trace["students"] if any(e["type"] == "served" for e in s["events"])][5]
    c = card(cfg, s)
    unseen = next(d.name for d in cfg.menu if d.name not in c["got"] and all(d.name not in e["text"] for e in c["events"]))
    fake = FakeAPI(stream_chunks=[f"I loved the {unseen}. ", "Great night."])
    out = Narrator(svc, fake.llm(svc.store)).narrate_sync(h, 1, s["id"])
    assert out["source"] == "template" and out["text"] == template(c) and unseen in out["reason"]


# ---------------------------------------------------------------- structured calls


def test_structured_reasks_once_then_succeeds():
    q = "Add one more server at Grill Station"
    blank = {"preset": None, "crowd_multiplier": None, "crowd_shift_min": None, "stations": [], "kitchen": [],
             "popularity": [], "remove_dishes": [], "unsupported": []}
    st = {"servers": 3, "service_median_min": None}
    bad = blank | {"stations": [{"station_id": "grill_station"} | st], "explanation": "x"}
    good = blank | {"stations": [{"station_id": "grill"} | st], "explanation": "One more Grill server."}
    fake = FakeAPI(texts=[json.dumps(bad), json.dumps(good)])
    r = ask(fake.llm(), q, DATE)
    assert r["ok"] and r["settings"] == ["Grill Station servers: 2 -> 3"] and r["source"] == "llm"
    assert len(fake.requests) == 2
    second = fake.requests[1]["body"]
    assert "unknown station" in second["messages"][-1]["content"]
    assert second["response_format"]["type"] == "json_schema" and second["temperature"] == 0


def test_described_but_unset_plan_is_reasked():
    blank = {"preset": None, "crowd_multiplier": None, "crowd_shift_min": None, "stations": [], "kitchen": [],
             "popularity": [], "remove_dishes": [], "unsupported": []}
    fake = FakeAPI(texts=[json.dumps(blank | {"explanation": "Made it 15% busier."}),
                          json.dumps(blank | {"crowd_multiplier": 1.15, "explanation": "Made it 15% busier."})])
    r = ask(fake.llm(), "15% busier", DATE)
    assert r["ok"] and r["settings"][0].startswith("Crowd: x1.15") and len(fake.requests) == 2


def test_api_failure_falls_back_to_the_mock():
    def boom(_):
        return httpx.Response(500, json={"error": {"message": "down", "type": "internal_server_error"}})

    llm = LLM(None, provider="groq", client=groq.Groq(
        api_key="test", max_retries=0, http_client=groq.DefaultHttpxClient(transport=httpx.MockTransport(boom))))
    r = ask(llm, "What if it's 10% busier?", DATE)
    assert r["source"] == "fallback" and r["request"]["levers"]["crowd_multiplier"] == 1.1 and r["error"]


def test_mock_ask_builds_a_valid_request():
    r = ask(LLM(provider="mock"), "smaller batches, 2 servers at Sushi, and 20% fewer students", DATE)
    assert r["ok"] and r["request"]["preset"] == "smaller_batches"
    assert r["request"]["levers"]["stations"]["sushi"]["servers"] == 2
    assert r["request"]["levers"]["crowd_multiplier"] == 0.8
    assert r["config_hash"] == config_hash(build_config(ScenarioRequest(**r["request"])))


def test_explain_check_rejects_numbers_not_in_facts():
    facts = {"nights": 30, "metrics": [{"change_median": -46.0, "better_in_share_of_nights": 0.733}]}
    assert explain_check("Waste fell by 46 portions, better in 73% of 30 nights.", facts) == []
    assert explain_check("Waste fell by 52 portions.", facts)
    assert explain_check("This saved 46 portions.", facts)


# ---------------------------------------------------------------- personas and new dishes


def test_mock_persona_set_is_valid_and_matches_known_diets(tmp_path):
    data = P.generate(LLM(provider="mock"))
    assert data["source"] == "mock" and not data["review"]["dropped"]
    shares = {}
    for p in data["personas"]:
        Persona.model_validate({k: v for k, v in p.items() if not k.startswith("_")})
        shares[p["diet"]] = shares.get(p["diet"], 0) + p["share"]
    assert shares["vegetarian"] == pytest.approx(0.083, abs=1e-3) and shares["vegan"] == pytest.approx(0.012, abs=1e-3)
    raw = P.write_raw(data, tmp_path / "raw.json")
    target = P.adopt(raw, tmp_path / "personas.json")
    assert json.loads(target.read_text())["status"] == "uncalibrated"


def test_persona_limits():
    d = P._mock_set().personas[0].model_copy(update={"seconds_prob": 0.95, "id": "Bad Id"})
    errs = P.problems(d, {"grill"})
    assert any("seconds_prob" in e for e in errs) and any("snake_case" in e for e in errs)
    assert any("station_affinity key" in e for e in P.problems(P._mock_set().personas[1], set()))


def test_new_dish_prior_and_lever_run():
    cfg = load_baseline(DATE)
    burger = next(d for d in cfg.menu if d.station == "grill" and d.role == "entree")
    nd = NewDish(name="Smash Burger", station="grill", role="entree", similar_to=[burger.id])
    prior = estimate(LLM(provider="mock"), DATE, nd)
    assert prior["popularity"]["range"][0] < prior["popularity"]["value"] < prior["popularity"]["range"][1]
    lever = NewDish(**prior["lever"])
    new_cfg = build_config(ScenarioRequest(date=DATE, levers=Levers(add_dishes=[lever])))
    assert any(d.id == nd.id and d.estimate for d in new_cfg.menu) and nd.id in new_cfg.kitchen
    m = run_night(new_cfg, 1).metrics
    assert m.dishes[nd.id].cooked == m.dishes[nd.id].served + m.dishes[nd.id].expired + m.dishes[nd.id].leftover


# ---------------------------------------------------------------- API


def test_api_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DCSIM_CACHE", str(tmp_path))
    monkeypatch.setenv("DCSIM_WORKERS", "1")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    from dcsim.api import app

    with TestClient(app) as c:
        assert c.get("/api/ai/status").json()["provider"] == "mock"
        r = c.post("/api/simulate", json={"date": DATE, "preset": "smaller_batches", "n_nights": 2})
        h = r.json()["config_hash"]
        if r.status_code == 202:
            with c.stream("GET", r.json()["stream"]) as s:
                for _ in s.iter_lines():
                    pass
        hl = c.get(f"/api/traces/{h}/highlights").json()
        sid, seed = hl["students"][0]["student_id"], hl["seed"]
        events = []
        with c.stream("GET", f"/api/narrate/{h}/{seed}/{sid}") as s:
            events = [line[7:] for line in s.iter_lines() if line.startswith("event: ")]
        assert events[0] == "card" and events[-1] == "final" and "sentence" in events
        j = c.get(f"/api/narrate/{h}/{seed}/{sid}", params={"stream": False}).json()
        assert j["card"]["title"].startswith(f"Student #{sid}") and j["source"] == "template"
        assert c.get(f"/api/narrate/{h}/{seed}/999999").status_code == 404
        ex = c.post("/api/explain", json={"scenario_hash": h, "n_nights": 2}).json()
        assert ex["source"] == "template" and ex["facts"]["changes"]
        a = c.post("/api/ask", json={"question": "10% busier", "date": DATE}).json()
        assert a["ok"] and a["settings"][0].startswith("Crowd")
        burger = next(d for d in load_baseline(DATE).menu if d.station == "grill")
        dp = c.post("/api/ai/dish-prior", json={"date": DATE, "dish": {
            "name": "Smash Burger", "station": "grill", "role": "entree", "similar_to": [burger.id]}})
        assert dp.status_code == 200 and dp.json()["label"].startswith("similar-dish")


def test_adopt_refuses_an_incomplete_set(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({"status": "incomplete", "review": {"missing_diets": ["vegetarian"]}, "personas": []}))
    target = tmp_path / "personas.json"
    with pytest.raises(ValueError, match="incomplete"):
        P.adopt(raw, target)
    assert not target.exists()


# ---------------------------------------------------------------- fake Gemini client

class _FakeGemini:
    """Duck-types google.genai.Client: models.generate_content and aio.models.generate_content_stream."""

    def __init__(self, replies=(), error=None, chunks=()):
        from types import SimpleNamespace as NS

        self.calls, self._replies, self._error = [], list(replies), error
        outer = self

        def generate_content(model, contents, config):
            outer.calls.append({"model": model, "contents": contents, "config": config})
            if outer._error:
                raise outer._error
            text = outer._replies.pop(0)
            return NS(text=text, candidates=[NS(finish_reason="STOP")])

        async def generate_content_stream(model, contents, config):
            outer.calls.append({"model": model, "contents": contents, "config": config})

            async def gen():
                for i, t in enumerate(chunks):
                    yield NS(text=t, candidates=[NS(finish_reason="STOP" if i == len(chunks) - 1 else None)])
            return gen()

        self.models = NS(generate_content=generate_content)
        self.aio = NS(models=NS(generate_content_stream=generate_content_stream))


def test_gemini_structured_reask_and_cache(tmp_path):
    from pydantic import BaseModel

    from dcsim.ai.llm import LLM
    from dcsim.store import Store

    class Out(BaseModel):
        n: int

    fake = _FakeGemini(replies=['{"n": -1}', '{"n": 3}'])
    llm = LLM(Store(tmp_path / "r.sqlite"), provider="gemini", model="gemini-3.8-flash", client=fake)
    kw = dict(system="sys", user="u", schema=Out, mock=lambda: Out(n=0),
              check=lambda v: [] if v.n > 0 else ["n must be positive"], effort="low", max_tokens=100)
    r = llm.structured("t", "v1", **kw)
    assert (r.source, r.value.n, r.problems) == ("llm", 3, [])
    second = fake.calls[1]
    assert [c["role"] for c in second["contents"]] == ["user", "model", "user"]
    cfg = second["config"]
    assert cfg.system_instruction == "sys" and cfg.response_mime_type == "application/json"
    assert cfg.temperature == 0 and cfg.max_output_tokens == 1600
    assert str(cfg.thinking_config.thinking_level).endswith("LOW")
    assert llm.structured("t", "v1", **kw).source == "cache" and len(fake.calls) == 2


def test_gemini_error_falls_back():
    from google.genai import errors
    from pydantic import BaseModel

    from dcsim.ai.llm import LLM

    class Out(BaseModel):
        n: int

    err = errors.ClientError(402, {"error": {"code": 402, "status": "RESOURCE_EXHAUSTED", "message": "credits depleted"}})
    r = LLM(None, provider="gemini", client=_FakeGemini(error=err)).structured(
        "t", "v1", system="s", user="u", schema=Out, mock=lambda: Out(n=7))
    assert r.source == "fallback" and r.value.n == 7 and "402" in r.error


def test_gemini_stream_text():
    from dcsim.ai.llm import LLM

    fake = _FakeGemini(chunks=["Hello ", "there."])
    llm = LLM(None, provider="gemini", model="gemini-3.8-flash", client=fake)

    async def collect():
        return [t async for t in llm.stream_text(system="s", user="u")]
    assert asyncio.run(collect()) == ["Hello ", "there."]
    assert fake.calls[0]["config"].temperature == 0.3


def test_provider_default_prefers_gemini(monkeypatch):
    from dcsim.ai import llm as m

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(m, "load_env_file", lambda *a, **k: None)
    monkeypatch.setenv("GEMINI_API_KEY", "x"); monkeypatch.setenv("GROQ_API_KEY", "y")
    assert m.provider_from_env() == "gemini"
    monkeypatch.delenv("GEMINI_API_KEY")
    assert m.provider_from_env() == "groq"


# ---------------------------------------------------------------- Gemini: backups, load gate, embeddings

class _ChainFake(_FakeGemini):
    """Fails with `busy` for the listed models, answers for the others; embeds by counting letters."""

    def __init__(self, busy_models=(), replies=(), chunks=()):
        from google.genai import errors
        from types import SimpleNamespace as NS

        super().__init__(replies=replies, chunks=chunks)
        inner_gc, inner_st = self.models.generate_content, self.aio.models.generate_content_stream
        busy = errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}})

        def gc(model, contents, config):
            if model in busy_models:
                self.calls.append({"model": model}); raise busy
            return inner_gc(model, contents, config)

        async def st(model, contents, config):
            if model in busy_models:
                self.calls.append({"model": model}); raise busy
            return await inner_st(model, contents, config)

        def embed_content(model, contents, config):
            vec = lambda t: [t.lower().count(ch) + 0.01 for ch in "abcdefghijklmnopqrstuvwxyz"]
            return NS(embeddings=[NS(values=vec(t)) for t in contents])

        self.models = NS(generate_content=gc, embed_content=embed_content)
        self.aio = NS(models=NS(generate_content_stream=st))


def test_gemini_busy_model_falls_back_to_backup(tmp_path):
    from pydantic import BaseModel

    class Out(BaseModel):
        n: int

    fake = _ChainFake(busy_models={"gemini-3.5-flash-lite"}, replies=['{"n": 2}'])
    llm = LLM(Store(tmp_path / "r.sqlite"), provider="gemini", client=fake)
    assert llm.fallbacks == ["gemini-3.1-flash-lite"]
    r = llm.structured("t", "v1", system="s", user="u", schema=Out, mock=lambda: Out(n=0))
    assert (r.source, r.value.n, r.model, r.fallback_model) == ("llm", 2, "gemini-3.1-flash-lite", True)
    assert [c["model"] for c in fake.calls] == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    again = llm.structured("t", "v1", system="s", user="u", schema=Out, mock=lambda: Out(n=0))
    assert again.source == "cache" and again.model == "gemini-3.1-flash-lite"


def test_gemini_stream_uses_backup_before_first_word():
    fake = _ChainFake(busy_models={"gemini-3.5-flash-lite"}, chunks=["Hi."])
    llm = LLM(None, provider="gemini", client=fake)

    async def collect():
        return [t async for t in llm.stream_text(system="s", user="u")]
    assert asyncio.run(collect()) == ["Hi."] and llm.last_model == "gemini-3.1-flash-lite" and llm.live == 0


def test_explicit_model_has_no_backup():
    from pydantic import BaseModel

    class Out(BaseModel):
        n: int

    fake = _ChainFake(busy_models={"gemini-4-hydrogen"})
    r = LLM(None, provider="gemini", client=fake).structured(
        "t", "v1", system="s", user="u", schema=Out, mock=lambda: Out(n=9), model="gemini-4-hydrogen")
    assert r.source == "fallback" and r.value.n == 9 and "503" in r.error


def test_busy_llm_serves_the_template_at_once(night):
    svc, cfg, h, trace = night
    s = next(s for s in trace["students"] if any(e["type"] == "served" for e in s["events"]))
    fake = _ChainFake(chunks=["never used."])
    llm = LLM(svc.store, provider="gemini", client=fake)
    llm.max_live = 0
    out = Narrator(svc, llm).narrate_sync(h, 1, s["id"])
    assert out["source"] == "template" and "busy" in out["reason"] and fake.calls == []


def test_narration_check_times_and_numbers(night):
    from dcsim.ai.narrate import check_facts

    _, cfg, _, trace = night
    s = next(s for s in trace["students"] if any(e["type"] == "balk" for e in s["events"]))
    c = card(cfg, s)
    e = next(e for e in c["events"] if e["type"] == "balk")
    n = next(x for x in e["text"].split() if x.isdigit())
    t = e["clock"].split()[0]
    assert check_facts(f"At {t} I saw {n} people in line and left.", c) == []
    assert check_facts("At 3:17 I saw 987 people and left.", c) == ["time 3:17", "number 987"]


def test_similar_dishes_embeddings_and_keyword_fallback():
    from dcsim.ai.similar import near_duplicates, similar_dishes

    cfg = load_baseline(DATE)
    target = next(d for d in cfg.menu if d.role == "entree")
    emb = similar_dishes(LLM(None, provider="gemini", client=_ChainFake()), DATE, target.name, role="entree", k=3)
    assert emb["source"] == "embedding" and emb["similar_to"][0] == target.id and len(emb["similar_to"]) == 3
    kw = similar_dishes(LLM(provider="mock"), DATE, target.name, role="entree")
    assert kw["source"] == "keywords" and kw["similar_to"][0] == target.id
    NewDish(name="X", station=target.station, role="entree", similar_to=kw["similar_to"])  # valid lever input
    dup = near_duplicates(LLM(None, provider="gemini", client=_ChainFake()),
                          {"a": "Late Hearty Eater", "b": "Late Hearty Eater v2", "c": "Vegan quick snacker"}, 0.95)
    assert [(d["a"], d["b"]) for d in dup] == [("a", "b")]


def test_dish_prior_reasons_are_short_and_use_the_strong_model():
    cfg = load_baseline(DATE)
    burger = next(d for d in cfg.menu if d.station == "grill" and d.role == "entree")
    nd = NewDish(name="Smash Burger", station="grill", role="entree", similar_to=[burger.id])
    from dcsim.population import eligible

    ids = [p.id for p in cfg.personas if eligible(p.diet, burger)]
    long = json.dumps({"affinities": [{"persona_id": i, "affinity": 1.2, "reason": "one two three four five six seven "
                                       "eight nine ten"} for i in ids]})
    short = json.dumps({"affinities": [{"persona_id": i, "affinity": 1.2, "reason": "likes burgers"} for i in ids]})
    fake = _ChainFake(replies=[long, short])
    out = estimate(LLM(None, provider="gemini", client=fake), DATE, nd)
    assert fake.calls[0]["model"] == "gemini-4-hydrogen" and len(fake.calls) == 2  # re-asked for short reasons
    assert out["source"] == "llm" and all(len(r.split()) <= 8 for r in out["reasons"].values())


def test_describe_and_ask_cover_engine_06_settings():
    """Kitchen staff, shifts and forecast reordering are described, and /ask can set cooks and shifts."""
    from dcsim import load_baseline
    from dcsim.ai.ask import AskPlan, CookChange, ShiftChange, _empty, to_request
    from dcsim.ai.describe import config_changes
    from dcsim.levers import build_config
    from dcsim.presets import PRESETS

    base = load_baseline("2026-11-13")
    plan: AskPlan = _empty(cooks=[CookChange(period="dinner", cooks=13)],
                           shifts=[ShiftChange(station_id="grill", from_time="18:30", servers=3),
                                   ShiftChange(station_id="grill", from_time="20:00", servers=2)])
    cfg = build_config(to_request("2026-11-13", plan))
    assert cfg.kitchen_resources.cooks["dinner"] == 13
    assert cfg.station("grill").shifts == ((1110, 3), (1200, 2))
    text = " | ".join(config_changes(base, cfg))
    assert "Cooks (dinner): 12 -> 13" in text and "servers by hour" in text
    assert any("Forecast reordering" in c for c in config_changes(base, PRESETS["forecast_reorder"](base)))


def test_mock_ask_station_server_is_not_the_preset():
    """ISSUES R6: "an extra server at grill station" is the Grill lever, not also the extra_server preset."""
    from dcsim.ai.ask import _mock

    plan = _mock("what if we add an extra server at grill station?", "2026-11-13")
    assert plan.preset is None and [c.station_id for c in plan.stations] == ["grill"]
    assert _mock("try the extra server preset", "2026-11-13").preset == "extra_server"
