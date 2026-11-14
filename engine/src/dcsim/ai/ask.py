"""Plain English -> lever settings (SPEC §6.1 stretch, ENGINE.md §7.1).

"What if we add a server at Grill and cut the meatloaf batches in half?" becomes
a ScenarioRequest. Nothing runs here: the manager sees the settings it chose
(and what it couldn't express), can edit them, then runs. The request is
validated by building the config; one re-ask with the error if it fails.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field # pyright: ignore[reportMissingImports]

from ..config import config_hash
from ..data import load_baseline
from ..levers import KitchenLever, Levers, ScenarioRequest, StationLever, build_config
from ..presets import PRESETS
from ..reports import clock
from .describe import config_changes
from .llm import LLM

PROMPT_VERSION = "ask-v2"


class StationChange(BaseModel):
    station_id: str
    servers: int | None = Field(description="new total servers, or null to keep")
    service_median_min: float | None = Field(description="minutes per item, or null to keep")


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
    explanation: str = Field(description="one or two sentences: which settings you chose and why")
    unsupported: list[str] = Field(description="parts of the question no setting can express")


def _empty(**kw) -> AskPlan:
    return AskPlan(**({"preset": None, "crowd_multiplier": None, "crowd_shift_min": None, "stations": [], "kitchen": [],
                       "popularity": [], "remove_dishes": [], "explanation": "", "unsupported": []} | kw))


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
    lv = Levers(
        crowd_multiplier=plan.crowd_multiplier, crowd_shift_min=plan.crowd_shift_min,
        stations={s.station_id: StationLever(**s.model_dump(exclude={"station_id"}, exclude_none=True)) for s in plan.stations},
        kitchen=kitchen, popularity={p.dish_id: p.multiplier for p in plan.popularity},
        remove_dishes=plan.remove_dishes,
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
    late = f", late night {clock(cfg.close_min)}-{clock(cfg.late_close_min)}" if cfg.late_close_min else ""
    return (f"Date {date}, dinner {clock(cfg.open_min)}-{clock(cfg.close_min)}{late}, about "
            f"{round(cfg.crowd.expected_students * cfg.crowd.multiplier):,} dinner students"
            + (f" and {round(cfg.late_crowd.expected_students * cfg.late_crowd.multiplier):,} late-night students"
               if cfg.late_crowd is not None else "") + f".\n\nStations (id | name | staffing):\n{st}"
            f"\n\nDishes (id | name | station | batch plan):\n" + "\n".join(dishes) + f"\n\nPresets (id | what it does):\n{presets}")


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
    for d in cfg.menu:
        name = d.name.lower()
        if re.search(rf"(remove|drop|without|no more) (the )?{re.escape(name)}", q):
            plan.remove_dishes.append(d.id)
        elif d.id in cfg.kitchen and re.search(rf"(cut|halve) (the )?{re.escape(name)}( batch(es)?)? in half|halve (the )?{re.escape(name)}", q):
            plan.kitchen.append(KitchenChange(dish_id=d.id, batch_size=max(1, cfg.kitchen[d.id].batch_size // 2),
                                              starting_batches=None, reorder_point=None, max_batches=None,
                                              last_batch_time=None))
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

    reply = llm.structured("ask", PROMPT_VERSION, system=SYSTEM, user=f"{tables}\n\nQuestion: {question}",
                           schema=AskPlan, mock=lambda: _mock(question, date), check=check, effort="low")
    plan = reply.value
    if reply.problems:  # still invalid after the re-ask: keep nothing, say why
        return {"question": question, "ok": False, "error": reply.problems[0], "explanation": plan.explanation,
                "unsupported": plan.unsupported, "source": reply.source, "model": reply.model}
    req = to_request(date, plan)
    cfg, base = build_config(req), load_baseline(date)
    return {
        "question": question, "ok": True,
        "request": req.model_dump(exclude_none=True),
        "settings": ([f"Preset: {req.preset}"] if req.preset else []) + config_changes(base, cfg),
        "explanation": plan.explanation, "unsupported": plan.unsupported,
        "config_hash": config_hash(cfg), "source": reply.source, "model": reply.model, "error": reply.error,
    }
