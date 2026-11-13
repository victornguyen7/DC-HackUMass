"""Persona affinities for a dish that isn't on the menu yet (ENGINE.md §7.3 step 5).

The LLM keeps the differences between personas (an athlete likes a burger more
than a rushed student does); the dish's overall level is borrowed from the
"similar to" dishes, never from the model. The result goes into a NewDish
lever as plain numbers, so the run is still deterministic and cacheable.
Labeled "AI estimate" with a wider popularity range.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from ..data import load_baseline
from ..levers import NewDish
from ..population import eligible
from .llm import LLM

PROMPT_VERSION = "dish-prior-v2"
MAX_REASON_WORDS = 8
RANGE = (0.6, 1.5)  # widened popularity range shown for AI estimates


class PersonaAffinity(BaseModel):
    persona_id: str
    affinity: float = Field(description="multiplier, 0.1-10; 1.0 = same taste as for the similar dishes on average")
    reason: str = Field(description="at most 8 words")


class DishPrior(BaseModel):
    affinities: list[PersonaAffinity]


SYSTEM = """You estimate how much each student persona at a university dining hall would want a new dish, relative to
dishes it is similar to. You are given each persona's current taste for the similar dishes. Only change a persona's
value when the new dish differs from the similar ones in a way that persona cares about. Keep values between 0.1 and 10.
Do not judge diet rules; ineligible personas are already excluded. Each reason is at most 8 words."""


def estimate(llm: LLM, date: str, dish: NewDish) -> dict:
    cfg = load_baseline(date)
    menu = {d.id: d for d in cfg.menu}
    unknown = [x for x in dish.similar_to if x not in menu]
    if unknown:
        raise ValueError(f"unknown 'similar to' dishes: {unknown}")
    if dish.station not in {s.id for s in cfg.stations}:
        raise ValueError(f"unknown station {dish.station!r}")
    similar = [menu[x] for x in dish.similar_to]
    probe = similar[0].model_copy(update={"diet_tags": tuple(dish.diet_tags)})
    personas = [p for p in cfg.personas if eligible(p.diet, probe)]
    base = {p.id: round(sum(p.affinity(d) for d in similar) / len(similar), 2) for p in personas}
    ids = set(base)

    user = (f"New dish: {dish.name} ({dish.role}, {'hot' if dish.hot else 'cold'}) at {cfg.station(dish.station).name}; "
            f"diet tags {dish.diet_tags or 'none'}.\nSimilar to: {', '.join(d.name for d in similar)}.\n\nPersonas "
            "(id | label | description | current taste for the similar dishes):\n"
            + "\n".join(f"{p.id} | {p.label} | {p.description or '-'} | {base[p.id]}" for p in personas)
            + "\n\nGive every persona listed an affinity for the new dish.")

    def check(r: DishPrior) -> list[str]:
        got = {a.persona_id for a in r.affinities}
        errs = [f"missing persona {x}" for x in sorted(ids - got)] + [f"unknown persona {x}" for x in sorted(got - ids)]
        errs += [f"{a.persona_id}: affinity must be 0.1-10" for a in r.affinities if not 0.1 <= a.affinity <= 10]
        errs += [f"{a.persona_id}: reason must be at most {MAX_REASON_WORDS} words"
                 for a in r.affinities if len(a.reason.split()) > MAX_REASON_WORDS]
        return errs

    reply = llm.structured(
        "dish_prior", PROMPT_VERSION, system=SYSTEM, user=user, schema=DishPrior, effort="low", max_tokens=8000,
        model=llm.strong_model,  # one-off and not time-critical: the stronger model is worth a few seconds
        mock=lambda: DishPrior(affinities=[PersonaAffinity(persona_id=k, affinity=v, reason="same as similar dishes")
                                           for k, v in base.items()]),
        check=check,
    )
    aff, reasons = dict(base), {}
    for a in reply.value.affinities:  # anything still invalid keeps the similar-dish value
        if a.persona_id in ids and 0.1 <= a.affinity <= 10:
            aff[a.persona_id] = round(a.affinity, 2)
            reasons[a.persona_id] = " ".join(a.reason.split()[:MAX_REASON_WORDS])  # display only
    pop = math.exp(sum(math.log(d.popularity) for d in similar) / len(similar)) * dish.popularity_multiplier
    return {
        "dish": dish.model_dump() | {"id": dish.id},
        "affinity": aff,
        "reasons": reasons,
        "similar_dish_affinity": base,
        "popularity": {"value": round(pop, 3), "range": [round(pop * RANGE[0], 3), round(pop * RANGE[1], 3)],
                       "borrowed_from": [d.name for d in similar]},
        "label": "AI estimate" if reply.source in ("llm", "cache") else "similar-dish average (no AI)",
        "source": reply.source, "model": reply.model, "problems": reply.problems, "error": reply.error,
        "lever": dish.model_copy(update={"affinity": aff}).model_dump(exclude_none=True),
    }
