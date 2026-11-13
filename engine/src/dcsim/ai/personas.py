"""Persona generation (ENGINE.md §7.2): the LLM proposes, a human reviews, the survey decides.

    uv run dcsim personas generate     -> data/worcester/personas_raw.json  (never used by the engine directly)
    uv run dcsim personas adopt        -> copies the reviewed raw set into personas.json

Regenerating is a deliberate action, never a side effect. Adopting changes
personas.json, which is part of data_version, so every cached result made
with the old personas stops matching on its own.

Checks on every proposed persona (invalid -> re-ask once -> drop):
patience 0-40 students, seconds 0-0.9, dwell 5-90 min, affinities 0.1-10,
probabilities that sum to 1, keys that exist on the menu.
(ENGINE.md said seconds 0-0.6; the team-confirmed "about half go back" needs a higher ceiling.)

Until the survey arrives, shares are only matched to the sourced diet shares
(vegetarian 8.3%, vegan 1.2%, Spring 2022 dining survey) and the set stays
labeled "uncalibrated".
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..config import ROLES, Persona
from ..data import available_dates, default_data_dir, load_baseline
from .llm import LLM, LLMError

PROMPT_VERSION = "personas-v3"
N_PERSONAS = 20
BATCH = 2  # personas per call: 3 overflowed the 1,000-output-token cap
KNOWN_DIET_SHARES = {"vegetarian": 0.083, "vegan": 0.012}  # sources/population/diet_patterns.json
LIMITS = {"patience": (0, 40), "seconds_prob": (0.0, 0.9), "dwell_median_min": (5, 90), "affinity": (0.1, 10.0),
          "plate_size": (1, 4)}


class Bucket(BaseModel):
    value: int = Field(description="patience: longest line (students already waiting) they'd join; plate_size: items on the first trip")
    prob: float


class Weight(BaseModel):
    key: str = Field(description="an item role or a station id from the lists given")
    value: float = Field(description="multiplier on popularity, 1.0 = average; 0.1-10")


class PersonaDraft(BaseModel):
    id: str = Field(description="snake_case, unique")
    label: str = Field(description="short, e.g. 'Athlete, high protein'")
    description: str = Field(description="ONE sentence, at most 25 words, about how this student eats dinner here")
    proposed_share: float = Field(description="share of dinner diners, 0-1")
    diet: Literal["none", "vegetarian", "vegan", "halal"]
    patience: list[Bucket]
    plate_size: list[Bucket]
    seconds_prob: float
    dwell_median_min: float
    arrival_bias: Literal["earlier", "none", "later"]
    category_affinity: list[Weight]
    station_affinity: list[Weight]


class PersonaSet(BaseModel):
    personas: list[PersonaDraft]


SYSTEM = """You design student personas for a dining-hall simulation at UMass Amherst (Worcester Dining Commons, dinner).
Each persona is a behavior pattern, not a demographic stereotype: describe how a student eats (time pressure, appetite,
patience for lines, social habits, diet rules), never ethnicity, gender, or nationality. Diet 'halal' is a rule about which
dishes are allowed, nothing more. Personas must differ from each other in ways that change where and when they eat.
Numbers must be plausible for a busy all-you-can-eat dining hall. A survey will later correct the shares and dish
popularity, so focus on realistic differences between personas."""


def _context() -> dict:
    stations, roles = {}, {r: 0 for r in ROLES}
    for date in available_dates():
        cfg = load_baseline(date)
        for s in cfg.stations:
            stations.setdefault(s.id, {"id": s.id, "name": s.name, "made_to_order": s.to_order,
                                       "self_serve": s.self_serve, "examples": set()})
        for d in cfg.menu:
            roles[d.role] += 1
            ex = stations[d.station]["examples"]
            if len(ex) < 4:
                ex.add(d.name)
    for s in stations.values():
        s["examples"] = sorted(s["examples"])
    return {"stations": list(stations.values()), "roles": roles}


def _user(ctx: dict, n: int) -> str:
    return f"""Propose {n} personas for dinner (4:30-9:00 pm), about 3,000 students a night.

Stations (id, name, examples):
{json.dumps(ctx["stations"], indent=1)}

Item roles and how many appear across this week's menus: {json.dumps(ctx["roles"])}

Known facts:
- Vegetarian 8.3% and vegan 1.2% of students (UMass Dining survey, Spring 2022). Halal share is unknown (guess).
- About half of students go back for seconds. Busiest hour is 7-8 pm.
- Long lines form at Street Food, Noodle Bowl, Grill, Latino and Sushi. Students who give up on a line try another
  station and often come back later for their favorite.

Fields:
- patience: distribution over the longest line they'd join, values 0-40, probs sum to 1.
- plate_size: distribution over items on the first trip, values 1-4, probs sum to 1.
- seconds_prob 0-0.9, dwell_median_min 5-90 (minutes seated).
- category_affinity keys are item roles; station_affinity keys are station ids. List at most 4 keys in each,
  only the ones that differ clearly from 1.0 (leave a key out for 1.0).
- description: one sentence, at most 25 words. Keep the JSON compact.
- proposed_share values should sum to about 1."""


def problems(p: PersonaDraft, station_ids: set[str]) -> list[str]:
    out = []
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", p.id):
        out.append("id must be snake_case")
    for name in ("patience", "plate_size"):
        b = getattr(p, name)
        lo, hi = LIMITS[name]
        if not b:
            out.append(f"{name} is empty")
        elif any(not lo <= x.value <= hi or x.prob < 0 for x in b):
            out.append(f"{name} values must be {lo}-{hi} with non-negative probs")
        elif abs(sum(x.prob for x in b) - 1) > 0.05:
            out.append(f"{name} probs sum to {sum(x.prob for x in b):.2f}, not 1")
    for name in ("seconds_prob", "dwell_median_min"):
        lo, hi = LIMITS[name]
        if not lo <= getattr(p, name) <= hi:
            out.append(f"{name} must be {lo}-{hi}")
    lo, hi = LIMITS["affinity"]
    for name, keys in (("category_affinity", set(ROLES)), ("station_affinity", station_ids)):
        for w in getattr(p, name):
            if w.key not in keys:
                out.append(f"{name} key {w.key!r} is not one of {sorted(keys)}")
            if not lo <= w.value <= hi:
                out.append(f"{name}[{w.key}] must be {lo}-{hi}")
    if not 0 < p.proposed_share <= 1:
        out.append("proposed_share must be in (0, 1]")
    return [f"{p.id}: {x}" for x in out]


def _dist(b: list[Bucket]) -> list[list]:
    agg: dict[int, float] = {}
    for x in b:
        agg[x.value] = agg.get(x.value, 0.0) + x.prob
    total = sum(agg.values())
    return [[v, round(pr / total, 4)] for v, pr in sorted(agg.items())]


def to_record(p: PersonaDraft, share: float) -> dict:
    rec = {
        "id": p.id, "label": p.label, "description": p.description, "share": round(share, 4), "diet": p.diet,
        "patience": _dist(p.patience), "plate_size": _dist(p.plate_size),
        "seconds_prob": round(p.seconds_prob, 3), "dwell_median_min": round(p.dwell_median_min, 1),
        "category_affinity": {w.key: round(w.value, 3) for w in p.category_affinity},
        "station_affinity": {w.key: round(w.value, 3) for w in p.station_affinity},
    }
    rec["_proposed_share"], rec["_arrival_bias"] = p.proposed_share, p.arrival_bias  # kept for review, dropped on adopt
    # Fix rounding so each distribution sums to exactly 1.
    for k in ("patience", "plate_size"):
        rec[k][-1][1] = round(1 - sum(x[1] for x in rec[k][:-1]), 4)
    return rec


def match_diet_shares(drafts: list[PersonaDraft], known: dict[str, float] = KNOWN_DIET_SHARES) -> list[float]:
    """Closest shares to the proposals that hit the sourced diet totals (scale within each diet group)."""
    total = sum(p.proposed_share for p in drafts)
    shares = [p.proposed_share / total for p in drafts]
    fixed = {d: v for d, v in known.items() if any(p.diet == d for p in drafts)}
    free_total = 1 - sum(fixed.values())
    free_now = sum(s for s, p in zip(shares, drafts) if p.diet not in fixed)
    out = []
    for s, p in zip(shares, drafts):
        if p.diet in fixed:
            group = sum(x for x, q in zip(shares, drafts) if q.diet == p.diet)
            out.append(s / group * fixed[p.diet])
        else:
            out.append(s / free_now * free_total)
    return out


def _mock_set() -> PersonaSet:
    """The hand-written set in personas.json, as if the model had proposed it (tests, offline)."""
    cur = json.loads((default_data_dir() / "personas.json").read_text())["personas"]
    return PersonaSet(personas=[PersonaDraft(
        id=p["id"], label=p["label"], description=p.get("description") or p["label"], proposed_share=p["share"],
        diet=p["diet"], patience=[Bucket(value=v, prob=pr) for v, pr in p["patience"]],
        plate_size=[Bucket(value=v, prob=pr) for v, pr in p["plate_size"]], seconds_prob=p["seconds_prob"],
        dwell_median_min=p["dwell_median_min"], arrival_bias="none",
        category_affinity=[Weight(key=k, value=v) for k, v in p.get("category_affinity", {}).items()],
        station_affinity=[Weight(key=k, value=v) for k, v in p.get("station_affinity", {}).items()],
    ) for p in cur])


def generate(llm: LLM, n: int = N_PERSONAS) -> dict:
    ctx = _context()
    station_ids = {s["id"] for s in ctx["stations"]}

    # Ask in small batches; each batch sees what's already proposed, and the first three each
    # must include one of the diets the engine has rules for.
    must = ["vegetarian", "vegan", "halal"]
    proposed: list[PersonaDraft] = []
    failed: list[str] = []
    source, model, set_problems = "mock", llm.model, []
    batches = 1 if llm.is_mock else -(-n // BATCH)
    for i in range(batches):
        k = n if llm.is_mock else min(BATCH, n - i * BATCH)
        need = must[i] if i < len(must) else None
        taken = {p.id for p in proposed}

        def check(ps: PersonaSet, need=need, taken=taken, k=k) -> list[str]:
            errs = [e for p in ps.personas for e in problems(p, station_ids)]
            ids = [p.id for p in ps.personas]
            if len(ids) != len(set(ids)) or set(ids) & taken:
                errs.append(f"persona ids must be unique and not one of {sorted(taken)}")
            if len(ids) != k:
                errs.append(f"propose exactly {k} personas")
            if need and not any(p.diet == need for p in ps.personas):
                errs.append(f"one persona must have diet {need!r}")
            return errs

        user = _user(ctx, k)
        if proposed:
            user += "\n\nAlready proposed (propose different eating behaviors, new ids):\n" + "\n".join(
                f"- {p.id}: {p.label} ({p.diet})" for p in proposed)
        if need:
            user += f"\n\nOne of these {k} personas must have diet '{need}'."
        reply = None
        for attempt in (1, 2):  # a small model sometimes emits broken JSON; one fresh try, then skip the batch
            try:
                reply = llm.structured("persona", PROMPT_VERSION, system=SYSTEM,
                                       user=user + ("" if attempt == 1 else "\n\n(Second attempt: keep it short.)"),
                                       schema=PersonaSet, mock=_mock_set, check=check, effort="low", max_tokens=1000,
                                       fallback=False)  # never write the hand set as if the model proposed it
                break
            except LLMError as e:
                failed.append(f"batch {i + 1} attempt {attempt}: {str(e)[:160]}")
        if reply is None:
            continue
        proposed += reply.value.personas
        source, model = reply.source, reply.model
        set_problems += [e for e in reply.problems if ":" not in e]

    seen, keep, dropped = set(), [], []
    for p in proposed:
        errs = problems(p, station_ids)
        if errs or p.id in seen:
            dropped.append({"id": p.id, "problems": errs or ["duplicate id"]})
        else:
            seen.add(p.id)
            keep.append(p)
    if not keep:
        raise ValueError("no valid personas; nothing written")
    missing = [d for d in KNOWN_DIET_SHARES if not any(p.diet == d for p in keep)]
    shares = match_diet_shares(keep)
    return {
        "_note": "LLM-PROPOSED personas (ENGINE.md §7.2). Review before `dcsim personas adopt`: remove stereotypes, "
                 "check variety, check every number. Shares are matched only to sourced diet shares; the rest are the "
                 "model's guesses until the survey adjustment runs.",
        "status": "incomplete" if missing else "uncalibrated",
        "source": source, "model": model, "prompt_version": PROMPT_VERSION,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "review": {"dropped": dropped, "unresolved_set_problems": set_problems, "failed_batches": failed, "missing_diets": missing,
                   "checklist": ["no demographic stereotypes in labels or descriptions",
                                 "personas differ in patience, seconds, dwell and station tastes",
                                 "halal share is a guess; check with the team",
                                 "every affinity has a reason a dining manager would agree with"]},
        "personas": [to_record(p, s) for p, s in zip(keep, shares)],
    }


def raw_path() -> Path:
    return default_data_dir() / "personas_raw.json"


def write_raw(data: dict, path: Path | None = None) -> Path:
    p = path or raw_path()
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return p


def adopt(raw: Path | None = None, target: Path | None = None) -> Path:
    """Reviewed raw set -> personas.json (the file the engine reads). Validates every record first."""
    data = json.loads((raw or raw_path()).read_text())
    if data.get("status") == "incomplete":
        raise ValueError(f"raw set is incomplete (missing diets {data['review'].get('missing_diets')}); regenerate first")
    personas = [{k: v for k, v in p.items() if not k.startswith("_")} for p in data["personas"]]
    for p in personas:
        Persona.model_validate(p)
    if abs(sum(p["share"] for p in personas) - 1) > 1e-3:
        raise ValueError("shares must sum to 1")
    personas[-1]["share"] = round(1 - sum(p["share"] for p in personas[:-1]), 4)
    target = target or default_data_dir() / "personas.json"
    old = json.loads(target.read_text()) if target.exists() else {}
    out = {
        "_note": f"Adopted from personas_raw.json ({data.get('source')}, {data.get('model')}, {data.get('generated_at')}) "
                 "after human review. Shares match sourced diet shares only; uncalibrated until the survey adjustment.",
        "status": "uncalibrated",
        "_sources": old.get("_sources", ""),
        "personas": personas,
    }
    target.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    return target
