"""Plain English -> lever settings (SPEC §6.1 stretch, ENGINE.md §7.1).

"What if we add a server at Grill and cut the meatloaf batches in half?" becomes
a ScenarioRequest. Nothing runs here: the manager sees the settings it chose
(and what it couldn't express), can edit them, then runs. The request is
validated by building the config; one re-ask with the error if it fails.

Three ways in, tried in order:
    1. Gemini function calling (provider gemini): one function per lever (set_servers, scale_batch,
       set_crowd, ...), with the real station and dish ids as enums; the calls become an AskPlan.
    2. JSON-schema output (any LLM provider): the model fills AskPlan directly.
    3. Keyword matching (no key, or both failed): `_mock`.
The reply's `method` says which one answered.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field # pyright: ignore[reportMissingImports]

from ..config import config_hash
from ..data import load_baseline
from ..levers import KitchenLever, KitchenStaffLever, Levers, ScenarioRequest, StationLever, build_config
from ..presets import PRESETS
from ..reports import clock
from .describe import config_changes
from .llm import LLM, LLMError

PROMPT_VERSION = "ask-v3"  # v3 (engine 0.6.0): cooks per period and server shifts
FC_VERSION = "ask-fc-v1"  # function calling


class StationChange(BaseModel):
    station_id: str
    servers: int | None = Field(description="new total servers, or null to keep")
    service_median_min: float | None = Field(description="minutes per item, or null to keep")


class ShiftChange(BaseModel):
    station_id: str
    from_time: str = Field(description="24-hour HH:MM when this staffing starts")
    servers: int = Field(description="servers from that time on (until the next shift for this station)")


class CookChange(BaseModel):
    period: str = Field(description="dinner or late_night")
    cooks: int = Field(description="new total cooks for that period")


class KitchenChange(BaseModel):
    dish_id: str
    batch_size: int | None = Field(description="null to keep")
    starting_batches: int | None = Field(description="null to keep")
    reorder_point: int | None = Field(description="null to keep")
    max_batches: int | None = Field(description="null to keep")
    last_batch_time: str | None = Field(description="24-hour HH:MM, or null to keep")


class PopularityChange(BaseModel):
    dish_id: str
    multiplier: float = Field(description="x current popularity, 0.1-3")


class AskPlan(BaseModel):
    """Every field is required (null / [] = no change), so the model has to fill the settings, not just describe them."""

    preset: str | None = Field(description="one preset id, or null")
    crowd_multiplier: float | None = Field(description="x expected crowd, e.g. 1.15 for 15% busier; null = no change")
    crowd_shift_min: float | None = Field(description="minutes to move every arrival wave, negative = earlier; null = no change")
    stations: list[StationChange]
    kitchen: list[KitchenChange]
    popularity: list[PopularityChange]
    remove_dishes: list[str]
    shifts: list[ShiftChange] = Field(default_factory=list, description="servers by time of night, e.g. one more Grill server 18:30-20:00 "
                                      "= two shifts: 18:30 -> current+1, 20:00 -> current")
    cooks: list[CookChange] = Field(default_factory=list, description="cooks in the kitchen per period")
    explanation: str = Field(description="one or two sentences: which settings you chose and why")
    unsupported: list[str] = Field(description="parts of the question no setting can express")


def _empty(**kw) -> AskPlan:
    return AskPlan(**({"preset": None, "crowd_multiplier": None, "crowd_shift_min": None, "stations": [], "kitchen": [],
                       "popularity": [], "remove_dishes": [], "shifts": [], "cooks": [], "explanation": "",
                       "unsupported": []} | kw))


SYSTEM = """You turn a dining manager's what-if question into settings for a dinner simulation.
Use only the ids in the tables. Change as little as possible: only what the question asks for.
Put every change in the setting fields; the explanation only describes them.
When the question gives a relative change ("half", "one more", "10% busier"), compute it from the current values shown.
If something can't be expressed with these settings (new staff roles, prices, breakfast, other dining halls), put it
in `unsupported` instead of guessing. Never invent a dish or station."""


def _hhmm_to_min(s: str) -> int:
    h, m = s.strip().split(":")
    return int(h) * 60 + int(m)


def to_request(date: str, plan: AskPlan) -> ScenarioRequest:
    kitchen = {}
    for k in plan.kitchen:
        f = k.model_dump(exclude={"dish_id", "last_batch_time"}, exclude_none=True)
        if k.last_batch_time:
            f["last_batch_min"] = _hhmm_to_min(k.last_batch_time)
        kitchen[k.dish_id] = KitchenLever(**f)
    stations = {s.station_id: s.model_dump(exclude={"station_id"}, exclude_none=True) for s in plan.stations}
    for sh in plan.shifts:
        stations.setdefault(sh.station_id, {}).setdefault("shifts", []).append((_hhmm_to_min(sh.from_time), sh.servers))
    lv = Levers(
        crowd_multiplier=plan.crowd_multiplier, crowd_shift_min=plan.crowd_shift_min,
        stations={k: StationLever(**v) for k, v in stations.items()},
        kitchen=kitchen, popularity={p.dish_id: p.multiplier for p in plan.popularity},
        remove_dishes=plan.remove_dishes,
        kitchen_staff=KitchenStaffLever(cooks={c.period: c.cooks for c in plan.cooks}) if plan.cooks else None,
    )
    empty = lv == Levers()
    return ScenarioRequest(date=date, preset=plan.preset or None, levers=None if empty else lv)


def _tables(date: str) -> str:
    cfg = load_baseline(date)
    st = "\n".join(f"{s.id} | {s.name} | servers {s.servers}{' (self-serve spots)' if s.self_serve else ''} | "
                   f"{s.service_median_min:g} min/item" for s in cfg.stations)
    dishes = []
    for d in cfg.menu:
        p = cfg.kitchen.get(d.id)
        plan = (f"batch {p.batch_size}, opening pans {p.starting_batches}, reorder at {p.reorder_point}, "
                f"max extra batches {p.max_batches}, last batch {clock(p.last_batch_min or cfg.window(d.period)[1])}") if p else "made to order"
        dishes.append(f"{d.id} | {d.name} | {d.station} | {plan}")
    presets = "\n".join(f"{k} | {(f.__doc__ or k).strip().splitlines()[0]}" for k, f in PRESETS.items())
    kr = cfg.kitchen_resources
    kitchen_staff = (f"\n\nKitchen: cooks {', '.join(f'{p} {n}' for p, n in kr.cooks.items())}; equipment "
                     + ", ".join(f"{k} {n}" for k, n in kr.equipment.items())) if kr else ""
    late = f", late night {clock(cfg.close_min)}-{clock(cfg.late_close_min)}" if cfg.late_close_min else ""
    return (f"Date {date}, dinner {clock(cfg.open_min)}-{clock(cfg.close_min)}{late}, about "
            f"{round(cfg.crowd.expected_students * cfg.crowd.multiplier):,} dinner students"
            + (f" and {round(cfg.late_crowd.expected_students * cfg.late_crowd.multiplier):,} late-night students"
               if cfg.late_crowd is not None else "") + f".\n\nStations (id | name | staffing):\n{st}"
            f"\n\nDishes (id | name | station | batch plan):\n" + "\n".join(dishes) + f"\n\nPresets (id | what it does):\n{presets}"
            + kitchen_staff)


# ---------------------------------------------------------------- mock: keyword matching


def _mock(question: str, date: str) -> AskPlan:
    q = question.lower()
    cfg = load_baseline(date)
    plan = _empty(explanation="Matched by keywords (no AI). Set GEMINI_API_KEY (engine/.env) for free-form questions.")
    for k in PRESETS:
        if k in q or k.replace("_", " ") in q:
            plan.preset = k
            break
    if m := re.search(r"(\d+)\s*%\s*(more|busier|bigger|larger|fewer|less|smaller|quieter)", q):
        sign = -1 if m.group(2) in ("fewer", "less", "smaller", "quieter") else 1
        plan.crowd_multiplier = round(1 + sign * int(m.group(1)) / 100, 2)
    for s in cfg.stations:
        name = s.name.lower()
        if re.search(rf"(extra|another|one more|add (a|one)) server[s]? (at|to|for) (the )?{re.escape(name)}", q):
            plan.stations.append(StationChange(station_id=s.id, servers=s.servers + 1, service_median_min=None))
        elif m := re.search(rf"(\d+) servers? (at|to|for) (the )?{re.escape(name)}", q):
            plan.stations.append(StationChange(station_id=s.id, servers=int(m.group(1)), service_median_min=None))
    kr = cfg.kitchen_resources
    if kr and (m := re.search(r"(one more|another|an extra|extra|add (a|one)|(\d+) more) cooks?", q)):
        extra = int(m.group(3)) if m.group(3) else 1
        period = "late_night" if "late" in q else "dinner"
        plan.cooks.append(CookChange(period=period, cooks=kr.cooks.get(period, 0) + extra))
    for d in cfg.menu:
        name = d.name.lower()
        if re.search(rf"(remove|drop|without|no more) (the )?{re.escape(name)}", q):
            plan.remove_dishes.append(d.id)
        elif d.id in cfg.kitchen and re.search(rf"(cut|halve) (the )?{re.escape(name)}( batch(es)?)? in half|halve (the )?{re.escape(name)}", q):
            plan.kitchen.append(KitchenChange(dish_id=d.id, batch_size=max(1, cfg.kitchen[d.id].batch_size // 2),
                                              starting_batches=None, reorder_point=None, max_batches=None,
                                              last_batch_time=None))
    if plan.preset == "extra_server" and any(c.servers is not None for c in plan.stations):
        # "an extra server at Grill" names a station: that's the station lever, not the
        # "extra server at the busiest station" preset (ISSUES R6).
        plan.preset = None
    if plan == _empty(explanation=plan.explanation):
        plan.unsupported = [question]
    return plan


# ---------------------------------------------------------------- entry point


def ask(llm: LLM, question: str, date: str) -> dict:
    tables = _tables(date)

    def check(plan: AskPlan) -> list[str]:
        if plan.preset and plan.preset not in PRESETS:
            return [f"unknown preset {plan.preset!r}"]
        try:
            req = to_request(date, plan)
            build_config(req)
        except Exception as e:  # pydantic or lever errors, reported back to the model
            return [str(e)]
        if req.preset is None and req.levers is None and not plan.unsupported:
            return ["no setting was changed; put the changes in the setting fields "
                    "(stations, kitchen, crowd_multiplier, ...) or list the request under unsupported"]
        return []

    fc_error = None
    if llm.provider == "gemini":
        try:
            got = ask_with_functions(llm, question, date, tables, check)
            if got is not None:
                plan, src, model = got
                return _result(question, date, plan, src, model, None, "function_calling")
            fc_error = "function calls still invalid after one re-ask"
        except LLMError as e:
            fc_error = str(e)

    reply = llm.structured("ask", PROMPT_VERSION, system=SYSTEM, user=f"{tables}\n\nQuestion: {question}",
                           schema=AskPlan, mock=lambda: _mock(question, date), check=check, effort="low")
    plan = reply.value
    method = "keywords" if reply.source in ("mock", "fallback") else "json_schema"
    if reply.problems:  # still invalid after the re-ask: keep nothing, say why
        return {"question": question, "ok": False, "error": reply.problems[0], "explanation": plan.explanation,
                "unsupported": plan.unsupported, "source": reply.source, "model": reply.model, "method": method,
                "function_calling_error": fc_error}
    return _result(question, date, plan, reply.source, reply.model, reply.error, method) | {"function_calling_error": fc_error}


def _result(question: str, date: str, plan: AskPlan, source: str, model: str, error: str | None, method: str) -> dict:
    req = to_request(date, plan)
    cfg, base = build_config(req), load_baseline(date)
    settings = ([f"Preset: {req.preset}"] if req.preset else []) + config_changes(base, cfg)
    # no explanation from the model: the settings list says it (the Lab shows it right above)
    explanation = plan.explanation or ("" if settings else "Nothing in the question maps to a setting.")
    return {
        "question": question, "ok": True,
        "request": req.model_dump(exclude_none=True),
        "settings": settings,
        "explanation": explanation, "unsupported": plan.unsupported,
        "config_hash": config_hash(cfg), "source": source, "model": model, "error": error, "method": method,
    }


# ---------------------------------------------------------------- Gemini function calling


FC_SYSTEM = SYSTEM + """
Answer by calling functions: one call per setting to change (call several in one answer), then explain once.
Relative changes ("half", "one more", "10% busier") are computed from the current values in the tables.
Anything no function can express goes to note_unsupported."""


def _fn(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": required}}


def ask_tools(date: str) -> list[dict]:
    """One function per lever, with this date's real ids as enums."""
    cfg = load_baseline(date)
    stations = [s.id for s in cfg.stations]
    batch = [d.id for d in cfg.menu if d.id in cfg.kitchen]
    dishes = [d.id for d in cfg.menu]
    st = {"type": "string", "enum": stations, "description": "station id"}
    bd = {"type": "string", "enum": batch, "description": "batch-cooked dish id"}
    dd = {"type": "string", "enum": dishes, "description": "dish id"}
    num = lambda d: {"type": "number", "description": d}  # noqa: E731
    integer = lambda d: {"type": "integer", "description": d}  # noqa: E731
    hhmm = {"type": "string", "description": "24-hour HH:MM"}
    periods = list(cfg.kitchen_resources.cooks) if cfg.kitchen_resources else ["dinner"]
    return [
        _fn("apply_preset", "Start from a preset scenario (applied before the other changes).",
            {"preset": {"type": "string", "enum": list(PRESETS)}}, ["preset"]),
        _fn("set_crowd", "Change how many students come, as a multiple of the expected crowd (1.1 = 10% busier).",
            {"multiplier": num("0.3-3")}, ["multiplier"]),
        _fn("shift_arrivals", "Move every dinner arrival wave earlier (negative) or later (positive).",
            {"minutes": num("-120 to 120")}, ["minutes"]),
        _fn("set_servers", "Total servers at a station for the whole night.",
            {"station_id": st, "servers": integer("new total, at least 1")}, ["station_id", "servers"]),
        _fn("set_service_time", "Minutes a server needs per item at a station.",
            {"station_id": st, "minutes_per_item": num("minutes, > 0")}, ["station_id", "minutes_per_item"]),
        _fn("set_shift", "Servers at a station from a time of night on (until the next shift for that station). "
            "One more server 18:30-20:00 = two calls: 18:30 -> current+1, 20:00 -> current.",
            {"station_id": st, "from_time": hhmm, "servers": integer("servers from that time on")},
            ["station_id", "from_time", "servers"]),
        _fn("set_cooks", "Cooks in the kitchen for a service period.",
            {"period": {"type": "string", "enum": periods}, "cooks": integer("new total cooks")}, ["period", "cooks"]),
        _fn("set_batch", "Set a batch-cooked dish's plan. Give only the fields to change.",
            {"dish_id": bd, "batch_size": integer("portions per batch"), "starting_batches": integer("pans ready at opening"),
             "reorder_point": integer("pan level that starts the next batch"), "max_batches": integer("most extra batches"),
             "last_batch_time": hhmm}, ["dish_id"]),
        _fn("scale_batch", "Multiply a dish's batch size (0.5 = half-size batches).",
            {"dish_id": bd, "multiplier": num("0.1-3")}, ["dish_id", "multiplier"]),
        _fn("set_popularity", "Make a dish more or less popular, as a multiple of today (0.1-3).",
            {"dish_id": dd, "multiplier": num("0.1-3")}, ["dish_id", "multiplier"]),
        _fn("remove_dish", "Take a dish off tonight's menu.", {"dish_id": dd}, ["dish_id"]),
        _fn("note_unsupported", "A part of the question no function can express (prices, other halls, breakfast, ...).",
            {"request_part": {"type": "string"}}, ["request_part"]),
        _fn("explain", "Call once: one or two sentences for the manager on which settings you chose and why.",
            {"text": {"type": "string"}}, ["text"]),
    ]


def calls_to_plan(calls: list[dict], date: str) -> tuple[AskPlan, list[str | None]]:
    """Function calls -> AskPlan, plus one problem (or None) per call, in order."""
    cfg = load_baseline(date)
    plan, problems = _empty(), []
    stations = {s.id: s for s in cfg.stations}
    kitchen: dict[str, KitchenChange] = {}
    for c in calls:
        name, a = c.get("name"), c.get("args") or {}
        err = None
        try:
            if name == "apply_preset":
                if a.get("preset") not in PRESETS:
                    raise ValueError(f"unknown preset {a.get('preset')!r}")
                plan.preset = a["preset"]
            elif name == "set_crowd":
                plan.crowd_multiplier = float(a["multiplier"])
            elif name == "shift_arrivals":
                plan.crowd_shift_min = float(a["minutes"])
            elif name in ("set_servers", "set_service_time", "set_shift"):
                if a.get("station_id") not in stations:
                    raise ValueError(f"unknown station {a.get('station_id')!r}")
                sid = a["station_id"]
                if name == "set_shift":
                    plan.shifts.append(ShiftChange(station_id=sid, from_time=str(a["from_time"]), servers=int(a["servers"])))
                else:
                    ch = next((x for x in plan.stations if x.station_id == sid), None) or StationChange(
                        station_id=sid, servers=None, service_median_min=None)
                    if ch not in plan.stations:
                        plan.stations.append(ch)
                    if name == "set_servers":
                        ch.servers = int(a["servers"])
                    else:
                        ch.service_median_min = float(a["minutes_per_item"])
            elif name == "set_cooks":
                plan.cooks.append(CookChange(period=str(a["period"]), cooks=int(a["cooks"])))
            elif name in ("set_batch", "scale_batch"):
                did = a.get("dish_id")
                if did not in cfg.kitchen:
                    raise ValueError(f"{did!r} is not a batch-cooked dish")
                k = kitchen.setdefault(did, KitchenChange(dish_id=did, batch_size=None, starting_batches=None,
                                                          reorder_point=None, max_batches=None, last_batch_time=None))
                if name == "scale_batch":
                    k.batch_size = max(1, round(cfg.kitchen[did].batch_size * float(a["multiplier"])))
                else:
                    for f in ("batch_size", "starting_batches", "reorder_point", "max_batches"):
                        if a.get(f) is not None:
                            setattr(k, f, int(a[f]))
                    if a.get("last_batch_time"):
                        k.last_batch_time = str(a["last_batch_time"])
            elif name == "set_popularity":
                plan.popularity.append(PopularityChange(dish_id=str(a["dish_id"]), multiplier=float(a["multiplier"])))
            elif name == "remove_dish":
                plan.remove_dishes.append(str(a["dish_id"]))
            elif name == "note_unsupported":
                plan.unsupported.append(str(a["request_part"]))
            elif name == "explain":
                plan.explanation = str(a["text"])
            else:
                raise ValueError(f"unknown function {name!r}")
        except (KeyError, TypeError, ValueError) as e:
            err = f"{type(e).__name__}: {e}" if not isinstance(e, ValueError) else str(e)
        problems.append(err)
    plan.kitchen = list(kitchen.values())
    return plan, problems


def ask_with_functions(llm: LLM, question: str, date: str, tables: str, check) -> tuple[AskPlan, str, str] | None:
    """Gemini function calling, one re-ask with the errors. None if the calls are still invalid."""
    tools = ask_tools(date)
    contents = [{"role": "user", "parts": [{"text": f"{tables}\n\nQuestion: {question}"}]}]
    sources = set()
    for attempt in range(2):
        content, src, model = llm.tool_turn("ask_fc", FC_VERSION, system=FC_SYSTEM, contents=contents, tools=tools,
                                            effort="low", max_tokens=1500)
        sources.add(src)
        calls = [p["function_call"] for p in content.get("parts", []) if p.get("function_call")]
        plan, problems = calls_to_plan(calls, date)
        overall = [] if any(problems) else check(plan)
        if not any(problems) and not overall:
            return plan, ("cache" if sources == {"cache"} else "llm"), model
        if attempt == 1:
            return None
        replies = []
        for c, p in zip(calls, problems):
            fr = {"name": c.get("name"), "response": {"error": p} if p else {"ok": True}}
            if c.get("id"):
                fr["id"] = c["id"]
            replies.append({"function_response": fr})
        fix = "Some calls failed (see the errors). Call every function again for the complete, corrected answer."
        if overall:
            fix += " Problems with the whole answer: " + "; ".join(overall)
        contents += [content, {"role": "user", "parts": replies + [{"text": fix}]}]
    return None

