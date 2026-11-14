"""Sam with the setting functions (staffing, menu) and what-ifs in the Ask Sam box. Never calls Google."""

from __future__ import annotations

from types import SimpleNamespace as NS

from dcsim.ai import LLM
from dcsim.ai.agent import (AgentRun, Goal, PLAN_LEVERS, TryPlan, history_entry, is_question, is_whatif,
                            protected_ids, run_agent)
from dcsim.levers import build_config, ScenarioRequest
from dcsim.service import Service
from dcsim.store import Store

DATE = "2026-11-13"


def _goal(**kw) -> Goal:
    return Goal(**({"waste_cut_pct": 10, "protect_diets": ["vegetarian"], "protect_dishes": [], "keep_missed_meals": True,
                    "keep_wait": True, "summary": "", "unsupported": []} | kw))


def _run(tmp_path, llm=None) -> AgentRun:
    r = AgentRun(Service(Store(tmp_path)), llm or LLM(provider="mock"), "x", DATE, n_nights=3)
    r.goal = _goal()
    r.protected = protected_ids(r.goal, r.base_cfg)
    r.baseline()
    return r


def test_plan_levers_are_decisions_only(tmp_path):
    r = _run(tmp_path)
    assert {"set_crowd", "shift_arrivals", "set_popularity"}.isdisjoint(PLAN_LEVERS)
    assert "describes the night" in r.queue_lever("set_crowd", {"multiplier": 0.8})["error"]
    veg = sorted(r.protected)[0]
    assert "protected" in r.queue_lever("remove_dish", {"dish_id": veg})["error"]
    assert "unknown station" in r.queue_lever("set_servers", {"station_id": "nowhere", "servers": 3})["error"]
    assert "queued" in r.queue_lever("set_servers", {"station_id": "grill", "servers": 3})
    assert "queued" in r.queue_lever("set_cooks", {"period": "dinner", "cooks": 13})
    r.run_try(TryPlan(note="staff + smaller batches", scale=[{"target": "unprotected", "batch_size": 0.85}]))
    t = r.tries[-1]
    cfg = build_config(t.request)
    assert next(s for s in cfg.stations if s.id == "grill").servers == 3 and cfg.kitchen_resources.cooks["dinner"] == 13
    assert "Grill Station servers: 2 -> 3" in t.moves and "Cooks (dinner): 12 -> 13" in t.moves
    assert t.moves[0] == "Unprotected dishes: batch size ×0.85"
    assert r.pending == []  # setting calls apply to one run only
    r.run_try(TryPlan(note="batches only", scale=[{"target": "unprotected", "batch_size": 0.85}]))
    assert next(s for s in build_config(r.tries[-1].request).stations if s.id == "grill").servers == 2


def test_explicit_dish_values_win_over_group_scaling(tmp_path):
    r = _run(tmp_path)
    dish = sorted(set(r.base_cfg.kitchen) - r.protected)[0]
    r.queue_lever("set_batch", {"dish_id": dish, "batch_size": 7})
    r.run_try(TryPlan(note="x", scale=[{"target": "unprotected", "batch_size": 0.5}]))
    assert build_config(r.tries[-1].request).kitchen[dish].batch_size == 7


class _Fake:
    """Gemini that answers each tool turn with a scripted list of function calls (several per turn)."""

    def __init__(self, turns, goal_json):
        from google.genai import types

        self.calls, self.turns, outer = [], list(turns), self

        def generate_content(model, contents, config):
            outer.calls.append(config)
            if getattr(config, "response_json_schema", None):
                return NS(text=goal_json, candidates=[NS(finish_reason="STOP")])
            parts = [types.Part(function_call=types.FunctionCall(name=n, args=a, id=f"c{i}"))
                     for i, (n, a) in enumerate(outer.turns.pop(0))]
            return NS(candidates=[NS(content=types.Content(role="model", parts=parts), finish_reason="STOP")])

        self.models = NS(generate_content=generate_content)


def test_sam_calls_setting_functions_with_run_simulation(tmp_path, monkeypatch):
    monkeypatch.setenv("DCSIM_AGENT_MODEL", "gemini-test")
    goal = ('{"kind": "plan", "waste_cut_pct": 5, "protect_diets": [], "protect_dishes": [], "keep_missed_meals": true,'
            ' "keep_wait": true, "summary": "Cut waste 5%", "unsupported": []}')
    fake = _Fake([[("set_servers", {"station_id": "grill", "servers": 3}),
                   ("run_simulation", {"note": "one more Grill server, smaller batches",
                                       "scale": [{"target": "all", "batch_size": 0.8}]})],
                  [("finish", {"try_id": 1, "reasoning": "Try 1."})]], goal)
    llm = LLM(Store(tmp_path), provider="gemini", model="gemini-main", client=fake)
    r = run_agent(Service(Store(tmp_path)), llm, "cut waste 5%, you may add a Grill server", DATE, n_nights=3, max_tries=2)
    assert r["kind"] == "plan" and r["chosen"] == 1
    assert "Grill Station servers: 2 -> 3" in r["moves"]
    decl = {d.name for d in fake.calls[1].tools[0].function_declarations}
    assert {"run_simulation", "finish", "set_servers", "set_cooks", "remove_dish"} <= decl
    assert "set_crowd" not in decl and "set_popularity" not in decl


def test_whatif_in_the_sam_box(tmp_path):
    assert is_whatif("What if we add a Grill server and it's 10% busier?") and is_whatif("halve the hot pot batches")
    assert not is_whatif("cut waste 20% without any vegetarian dish running out")
    assert not is_whatif("Which dishes ran out on rainy Fridays?") and is_question("Which dishes ran out on rainy Fridays?")
    events = []
    r = run_agent(Service(Store(tmp_path)), LLM(provider="mock"), "what if we add one more server at Grill Station and 10% busier",
                  DATE, n_nights=3, emit=lambda k, d: events.append(k))
    assert r["kind"] == "whatif" and r["ok"] and r["method"] == "keywords"
    assert "Grill Station servers: 2 -> 3" in r["settings"] and any(s.startswith("Crowd: x1.10") for s in r["settings"])
    assert set(r["metrics"]) == {"waste_total", "missed_meal", "wait_p90", "stockout_min_total"}
    assert r["summary"].startswith("Against tonight's plan: waste") and r["config_hash"] and r["request"]["levers"]
    assert "whatif" in events and "try_start" in events and "validate_start" not in events
    h = history_entry(r)
    assert h["kind"] == "whatif" and h["settings"] == r["settings"] and h["headline"].startswith("waste")


def test_whatif_with_nothing_to_set(tmp_path):
    r = run_agent(Service(Store(tmp_path)), LLM(provider="mock"), "what if we paint the walls blue", DATE, n_nights=3)
    assert r["kind"] == "whatif" and not r["settings"] and "nothing to simulate" in r["summary"].lower() or not r["ok"]
    assert "config_hash" not in r
