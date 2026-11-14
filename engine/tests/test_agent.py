"""Sam the Minuteman, the planning agent (ai/agent.py). Tests never call Google: a fake client plays Gemini."""

from __future__ import annotations

import time
from types import SimpleNamespace as NS

import pytest

from dcsim.ai import LLM
from dcsim.ai.agent import (Goal, Scale, DishSet, TryPlan, check_goal, parse_goal, plan_to_request, protected_ids,
                            run_agent)
from dcsim.data import load_baseline
from dcsim.levers import build_config
from dcsim.service import Service
from dcsim.store import Store

DATE = "2026-11-13"
GOAL = "cut waste 20% without any vegetarian dish running out"


def _goal(**kw) -> Goal:
    return Goal(**({"waste_cut_pct": 20, "protect_diets": ["vegetarian"], "protect_dishes": [], "keep_missed_meals": True,
                    "keep_wait": True, "summary": "", "unsupported": []} | kw))


def _row(dish_id, share, empty, waste=10):
    return {"dish_id": dish_id, "name": dish_id, "station": "grill", "runout_share": share,
            "stockout_min": {"p50": empty}, "waste": {"p50": waste}}


def _metric(diff, verdict):
    return {"diff": {"p50": diff}, "verdict": verdict}


def test_check_goal_is_code_not_the_llm():
    base = {"headline": {"waste_total": {"p50": 1000}}, "cook_sheet": [_row("a", 0.1, 0.0), _row("b", 0.0, 0.0)]}
    res = {"cook_sheet": [_row("a", 0.15, 0.5), _row("b", 0.3, 0.0)],
           "vs_baseline": {"metrics": {"waste_total": _metric(-250, "better"), "missed_meal": _metric(1, "same"),
                                       "wait_p90": _metric(0.2, "same")}}}
    c = check_goal(_goal(), {"a", "b"}, base, res)
    assert c["waste_pct"] == -25.0
    assert {x["name"]: x["ok"] for x in c["checks"]} == {"waste": True, "protected": False, "missed_meal": True,
                                                          "wait_p90": True}
    assert [w["dish_id"] for w in c["protected_worse"]] == ["b"]  # a stays inside the noise allowance
    assert not c["met"]
    res["cook_sheet"][1] = _row("b", 0.05, 0.0)
    res["vs_baseline"]["metrics"]["missed_meal"] = _metric(20, "worse")
    c = check_goal(_goal(), {"a", "b"}, base, res)
    assert not c["met"] and [x["name"] for x in c["checks"] if not x["ok"]] == ["missed_meal"]
    assert check_goal(_goal(keep_missed_meals=False), {"a", "b"}, base, res)["met"]


def test_goal_keyword_parser():
    g, src = parse_goal(LLM(provider="mock"), "Cut waste by 15 percent; no vegan or halal dish may run out", DATE)
    assert src == "mock" and g.waste_cut_pct == 15 and g.protect_diets == ["vegan", "halal"]
    cfg = load_baseline(DATE)
    prot, menu = protected_ids(g, cfg), {d.id: d for d in cfg.menu}
    assert prot and all({"vegan", "halal"} & set(menu[d].diet_tags) and d in cfg.kitchen for d in prot)


def test_plan_to_request_scales_then_sets():
    cfg = load_baseline(DATE)
    prot = protected_ids(_goal(), cfg)
    some = sorted(set(cfg.kitchen) - prot)[0]
    veg = sorted(prot)[0]
    req = plan_to_request(DATE, TryPlan(scale=[Scale(target="unprotected", batch_size=0.5)],
                                        dishes=[DishSet(dish_id=veg, reorder_point=99)]), prot)
    k = req.levers.kitchen
    assert k[some].batch_size == max(1, round(cfg.kitchen[some].batch_size * 0.5))
    assert veg not in k or k[veg].batch_size is None  # protected dishes are not in 'unprotected'
    assert k[veg].reorder_point == 99
    out = build_config(req)
    assert out.kitchen[veg].reorder_point == 99
    with pytest.raises(ValueError, match="unknown scale target"):
        plan_to_request(DATE, TryPlan(scale=[Scale(target="nope", batch_size=0.5)]), prot)
    with pytest.raises(ValueError, match="outside"):
        plan_to_request(DATE, TryPlan(scale=[Scale(target="all", batch_size=9)]), prot)
    assert plan_to_request(DATE, TryPlan(), prot).levers is None


def test_rule_based_agent_end_to_end(tmp_path):
    svc = Service(Store(tmp_path))
    events = []
    r = run_agent(svc, LLM(provider="mock"), GOAL, DATE, n_nights=3, max_tries=2,
                  emit=lambda k, d: events.append(k))
    kinds = [e for e in events if e != "progress"]
    assert kinds[:2] == ["start", "goal"] and "try" in kinds and kinds[-1] == "validate_start"
    assert 1 <= len(r["tries"]) <= 2 and r["source"] == "mock" and r["chosen_by"] in ("rules", "goal check")
    assert r["holdout"]["seeds"] == [4, 6]
    assert r["request"]["date"] == DATE and r["config_hash"] == r["tries"][r["chosen"] - 1]["config_hash"]
    assert r["changed_dishes"] and all(c["before"] != c["after"] for c in r["changed_dishes"])


class _FakeToolGemini:
    """Plays Gemini function calling: a scripted list of function calls, one per turn."""

    def __init__(self, script):
        from google.genai import types

        self.calls, self.script, self.types = [], list(script), types
        outer = self

        def generate_content(model, contents, config):
            outer.calls.append({"model": model, "contents": list(contents), "config": config})
            name, args = outer.script.pop(0)
            part = types.Part(function_call=types.FunctionCall(name=name, args=args, id=f"c{len(outer.calls)}"))
            return NS(candidates=[NS(content=types.Content(role="model", parts=[part]), finish_reason="STOP")])

        self.models = NS(generate_content=generate_content)


def test_gemini_agent_loop_and_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("DCSIM_AGENT_MODEL", "gemini-test")
    store = Store(tmp_path)
    svc = Service(store)
    goal_json = ('{"waste_cut_pct": 10, "protect_diets": ["vegetarian"], "protect_dishes": [], "keep_missed_meals": true,'
                 ' "keep_wait": true, "summary": "Cut waste 10%", "unsupported": []}')
    script = [("run_simulation", {"note": "nonsense target", "scale": [{"target": "nowhere", "batch_size": 0.8}]}),
              ("run_simulation", {"note": "cut unprotected", "scale": [{"target": "unprotected", "batch_size": 0.8,
                                                                         "reorder_point": 0.8}]}),
              ("finish", {"try_id": 7, "reasoning": "x"}),
              ("finish", {"try_id": 1, "reasoning": "Try 1 cut waste.", "caveats": "watch fries"})]
    fake = _FakeToolGemini(script)
    goal_reply = NS(text=goal_json, candidates=[NS(finish_reason="STOP")])
    tool_gc = fake.models.generate_content

    def gc(model, contents, config):  # the goal parse is a JSON call; the rest are tool turns
        return goal_reply if getattr(config, "response_json_schema", None) else tool_gc(model, contents, config)

    fake.models = NS(generate_content=gc)
    llm = LLM(store, provider="gemini", model="gemini-main", client=fake)
    events = []
    r = run_agent(svc, llm, GOAL, DATE, n_nights=3, max_tries=3, emit=lambda k, d: events.append((k, d)))
    assert r["source"] == "llm" and r["chosen"] == 1 and r["chosen_by"] == "agent" and r["model"] == "gemini-test"
    assert r["reasoning"] == "Try 1 cut waste." and r["goal"]["waste_cut_pct"] == 10
    assert len(fake.calls) == 4 and {c["model"] for c in fake.calls} == {"gemini-test"}
    # the bad target came back to the model as an error, and the finish for a missing try too
    first_reply = fake.calls[1]["contents"][-1]["parts"][0]["function_response"]
    assert "unknown scale target" in first_reply["response"]["result"]["error"] and first_reply["id"] == "c1"
    assert "no try 7" in str(fake.calls[3]["contents"][-1])
    assert [k for k, _ in events if k == "try"] == ["try"]

    # same goal again: every turn comes from the cache, the fake is not called
    fake.script = []
    r2 = run_agent(svc, llm, GOAL, DATE, n_nights=3, max_tries=3)
    assert r2["source"] == "cache" and r2["config_hash"] == r["config_hash"] and len(fake.calls) == 4


def test_gemini_failure_falls_back_to_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("DCSIM_AGENT_MODEL", "gemini-main")  # main model: no switch, straight to the rules

    def boom(model, contents, config):
        raise RuntimeError("network down")

    llm = LLM(Store(tmp_path), provider="gemini", model="gemini-main", client=NS(models=NS(generate_content=boom)))
    events = []
    r = run_agent(Service(Store(tmp_path)), llm, GOAL, DATE, n_nights=3, max_tries=2,
                  emit=lambda k, d: events.append((k, d)))
    assert r["source"] == "mock" and r["tries"] and any("rule-based" in d["text"] for k, d in events if k == "notice")


def test_agent_api(tmp_path_factory):
    from fastapi.testclient import TestClient

    mp = pytest.MonkeyPatch()
    mp.setenv("DCSIM_CACHE", str(tmp_path_factory.mktemp("cache")))
    mp.setenv("DCSIM_WORKERS", "1")
    from dcsim.api import app

    try:
        with TestClient(app) as c:
            assert c.post("/api/agent", json={"goal": "x"}).status_code == 422
            assert c.post("/api/agent", json={"goal": GOAL, "date": "1999-01-01"}).status_code == 404
            assert c.post("/api/agent", json={"goal": GOAL, "date": DATE, "max_tries": 6}).status_code == 422  # cap 5
            j = c.post("/api/agent", json={"goal": GOAL, "date": DATE, "max_tries": 1, "n_nights": 3}).json()
            end = time.time() + 120
            while (s := c.get(f"/api/agent/{j['job_id']}").json())["status"] == "running" and time.time() < end:
                time.sleep(0.1)
            assert s["status"] == "done", s
            assert s["result"]["tries"] and s["result"]["agent"] == "Sam the Minuteman"
            body = c.get(j["stream"]).text  # finished: the stream replays everything and closes
            assert body.index("event: start") < body.index("event: try") < body.index("event: final")
            assert c.get("/api/agent/nope").status_code == 404
            hist = c.get("/api/agent/history").json()
            assert hist[0]["run_id"] == j["job_id"] and hist[0]["prompt"] == GOAL and hist[0]["for_date"] == DATE
            item = c.get(f"/api/agent/history/{j['job_id']}").json()
            assert "tries" not in item["final"] and item["final"]["tries_run"] == 1 and item["final"]["reasoning"]
            assert s["result"]["history_id"] == j["job_id"]
            assert c.delete(f"/api/agent/history/{j['job_id']}").status_code == 204
            assert c.get(f"/api/agent/history/{j['job_id']}").status_code == 404
    finally:
        mp.undo()


def test_never_more_than_five_tries(tmp_path):
    r = run_agent(Service(Store(tmp_path)), LLM(provider="mock"), "cut waste 60% without any vegetarian dish running out",
                  DATE, n_nights=3, max_tries=9)
    assert len(r["tries"]) <= 5


def test_guard_sql():
    from dcsim.ai.tiger_mcp import TigerError, guard_sql

    assert guard_sql("select 1;") == "select 1"
    assert guard_sql("WITH x AS (SELECT 1) SELECT * FROM x") .startswith("WITH")
    assert guard_sql("select 'drop table x' as s -- delete") .startswith("select")  # keywords in strings/comments are fine
    for bad in ["delete from dcsim.forecasts", "select 1; drop table x", "select * into t from dcsim.nights",
                "update dcsim.nights set waste_lb = 0", "select pg_sleep(100)", "select set_config('a','b',false)",
                "/* hi */ insert into x values (1)", ""]:
        with pytest.raises(TigerError):
            guard_sql(bad)


def test_question_without_ai_says_what_it_needs(tmp_path):
    from dcsim.ai.agent import is_question

    assert is_question("Which dishes ran out on rainy Fridays?")
    assert not is_question("Cut waste 20% without any vegetarian dish running out")
    events = []
    r = run_agent(Service(Store(tmp_path)), LLM(provider="mock"), "Which dishes ran out on rainy Fridays?", DATE,
                  emit=lambda k, d: events.append(k))
    assert r["kind"] == "question" and "Gemini" in r["answer"] and "try_start" not in events


class _FakeTiger:
    def __init__(self):
        self.sql = []

    def query(self, sql):
        from dcsim.ai.tiger_mcp import guard_sql

        self.sql.append(guard_sql(sql))
        return {"columns": ["dish", "nights"], "rows": [["Cumin Basmati Rice", "1"]], "row_count": 1, "truncated": False}

    def schema(self):
        return "TABLE nights ..."

    def close(self):
        pass


def test_question_answered_from_tiger(tmp_path, monkeypatch):
    import dcsim.ai.agent as A
    import dcsim.ai.tiger_mcp as T

    monkeypatch.setenv("DCSIM_AGENT_MODEL", "gemini-test")
    monkeypatch.setattr(T, "available", lambda: True)
    fake_tiger = _FakeTiger()
    monkeypatch.setattr(T, "TigerMCP", lambda: fake_tiger)
    goal_json = ('{"kind": "question", "waste_cut_pct": 0, "protect_diets": [], "protect_dishes": [], '
                 '"keep_missed_meals": true, "keep_wait": true, "summary": "rainy Fridays", "unsupported": []}')
    script = [("answer", {"answer": "too early"}),  # refused: no query yet
              ("query_tiger", {"sql": "delete from dcsim.nights", "why": "oops"}),  # blocked by the guard
              ("query_tiger", {"sql": "select dish, count(*) from dcsim.pan_windows group by 1", "why": "count"}),
              ("answer", {"answer": "Cumin Basmati Rice ran out once.", "caveats": "synthetic"})]
    fake = _FakeToolGemini(script)
    tool_gc = fake.models.generate_content
    goal_reply = NS(text=goal_json, candidates=[NS(finish_reason="STOP")])
    fake.models = NS(generate_content=lambda model, contents, config: goal_reply
                     if getattr(config, "response_json_schema", None) else tool_gc(model, contents, config))
    llm = LLM(Store(tmp_path), provider="gemini", model="gemini-main", client=fake)
    events = []
    r = A.run_agent(Service(Store(tmp_path)), llm, "Which dishes ran out on rainy Fridays?", DATE,
                    emit=lambda k, d: events.append((k, d)))
    assert r["kind"] == "question" and r["answer"] == "Cumin Basmati Rice ran out once."
    assert len(fake_tiger.sql) == 1  # the DELETE never reached Tiger
    assert [x.get("error") is None for x in r["tiger_lookups"]] == [False, True]
    assert [k for k, _ in events].count("tiger") == 2 and "try_start" not in [k for k, _ in events]
    h = A.history_entry(r)
    assert h["headline"].startswith("Cumin") and h["tiger_lookups"][1]["row_count"] == 1
