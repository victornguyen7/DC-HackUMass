"""Plain English -> settings through Gemini function calling (ai/ask.py). A fake client plays Gemini."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

from dcsim.ai import LLM
from dcsim.ai.ask import ask, ask_tools, calls_to_plan
from dcsim.data import load_baseline
from dcsim.store import Store

DATE = "2026-11-13"


class _FakeGemini:
    """Tool turns return the scripted function calls; JSON-schema calls return `json_reply`."""

    def __init__(self, turns, json_reply=None):
        from google.genai import types

        self.calls, self.turns, outer = [], list(turns), self

        def generate_content(model, contents, config):
            outer.calls.append({"contents": list(contents), "config": config})
            if getattr(config, "response_json_schema", None):
                return NS(text=json_reply, candidates=[NS(finish_reason="STOP")])
            parts = [types.Part(function_call=types.FunctionCall(name=n, args=a, id=f"c{i}"))
                     for i, (n, a) in enumerate(outer.turns.pop(0))]
            return NS(candidates=[NS(content=types.Content(role="model", parts=parts), finish_reason="STOP")])

        self.models = NS(generate_content=generate_content)


def _llm(tmp_path, fake):
    return LLM(Store(tmp_path), provider="gemini", model="gemini-test", client=fake)


def test_function_calls_become_settings(tmp_path):
    fake = _FakeGemini([[("set_servers", {"station_id": "grill", "servers": 3}), ("set_crowd", {"multiplier": 1.1}),
                         ("explain", {"text": "One more Grill server, 10% busier."})]])
    r = ask(_llm(tmp_path, fake), "one more server at Grill and 10% busier", DATE)
    assert r["ok"] and r["method"] == "function_calling" and r["source"] == "llm"
    assert r["settings"] == ["Crowd: x1.10 (about 2,970 dinner students)", "Grill Station servers: 2 -> 3"]
    assert r["explanation"] == "One more Grill server, 10% busier."
    cfg = fake.calls[0]["config"]
    decl = {d.name: d for d in cfg.tools[0].function_declarations}
    assert {"set_servers", "scale_batch", "set_crowd", "remove_dish", "note_unsupported"} <= set(decl)
    assert "grill" in decl["set_servers"].parameters_json_schema["properties"]["station_id"]["enum"]
    assert cfg.tool_config.function_calling_config.mode == "ANY"
    # cached: the same question doesn't call Gemini again
    ask(_llm(tmp_path, fake), "one more server at Grill and 10% busier", DATE)
    assert len(fake.calls) == 1


def test_bad_call_is_reasked_with_its_error(tmp_path):
    sushi = next(d.id for d in load_baseline(DATE).menu if d.id not in load_baseline(DATE).kitchen)  # made to order
    fake = _FakeGemini([[("scale_batch", {"dish_id": sushi, "multiplier": 0.5})],
                        [("note_unsupported", {"request_part": "made-to-order dishes have no batches"})]])
    r = ask(_llm(tmp_path, fake), "halve the sushi batches", DATE)
    assert r["ok"] and r["method"] == "function_calling" and r["settings"] == []
    assert r["unsupported"] == ["made-to-order dishes have no batches"]
    assert r["explanation"] == "Nothing in the question maps to a setting."
    second = fake.calls[1]["contents"][-1]["parts"]
    assert "not a batch-cooked dish" in second[0]["function_response"]["response"]["error"]
    assert second[0]["function_response"]["id"] == "c0"


def test_still_invalid_falls_back_to_json_schema(tmp_path):
    bad = [("set_servers", {"station_id": "nowhere", "servers": 3})]
    blank = {"preset": None, "crowd_multiplier": 1.15, "crowd_shift_min": None, "stations": [], "kitchen": [],
             "popularity": [], "remove_dishes": [], "unsupported": [], "explanation": "15% busier."}
    fake = _FakeGemini([bad, bad], json_reply=json.dumps(blank))
    r = ask(_llm(tmp_path, fake), "15% busier", DATE)
    assert r["ok"] and r["method"] == "json_schema" and r["settings"][0].startswith("Crowd: x1.15")
    assert "still invalid" in r["function_calling_error"]


def test_scale_and_set_batch_math():
    cfg = load_baseline(DATE)
    dish = next(iter(cfg.kitchen))
    plan, problems = calls_to_plan([
        {"name": "scale_batch", "args": {"dish_id": dish, "multiplier": 0.5}},
        {"name": "set_batch", "args": {"dish_id": dish, "reorder_point": 9, "last_batch_time": "20:15"}},
        {"name": "set_shift", "args": {"station_id": "grill", "from_time": "18:30", "servers": 3}},
        {"name": "fly", "args": {}},
    ], DATE)
    k = plan.kitchen[0]
    assert k.batch_size == max(1, round(cfg.kitchen[dish].batch_size * 0.5)) and k.reorder_point == 9
    assert k.last_batch_time == "20:15" and plan.shifts[0].servers == 3
    assert problems[:3] == [None, None, None] and "unknown function" in problems[3]
    assert len(ask_tools(DATE)) == 13
