"""Sam the Minuteman: a planning agent that simulates its way to a production plan (SPONSORS.md §2).

The manager gives a goal ("cut waste 20% without any vegetarian dish running out"). Sam reads
tonight's menu and baseline, proposes batch-plan changes, runs them through the simulator, reads
the results, adjusts and tries again, then hands back a plan with its reasoning.

    goal text --(LLM, keyword fallback)--> Goal
    loop (Gemini function calling, at most `max_tries` simulations):
        run_simulation(plan) -> engine runs N paired nights -> small summary + check_goal (code)
        finish(try_id, reasoning)
    validate: the chosen plan re-run on unseen seeds (N+1..2N), checked again

Rules (LLM.md): the model only proposes settings. Every number comes from the engine, and the
goal check is deterministic code. Without a key (LLM_PROVIDER=mock), or if Gemini fails mid-run,
a rule-based planner takes the same steps through the same tools.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from ..config import ScenarioConfig, config_hash
from ..data import load_baseline
from ..levers import KitchenLever, Levers, ScenarioRequest, build_config
from ..service import Service
from .describe import config_changes
from .llm import LLM, LLMError

AGENT_NAME = "Sam the Minuteman"
PROMPT_VERSION = "agent-v2"  # v2: Tiger lookups through the Tiger MCP server, at most 5 tries
GOAL_VERSION = "agent-goal-v2"  # v2: plan vs question
MAX_TRIES = 5  # simulations per plan, never more
MAX_TIGER = 6  # Tiger lookups per run
DIETS = ("vegetarian", "vegan", "halal")
PRESET_CHOICES = ("none", "forecast_reorder", "smaller_batches", "trim_slow")  # batch-plan presets only
FACTOR_MIN, FACTOR_MAX = 0.3, 2.5
# "Runs out no more than today": a protected dish may not run out more often (share of nights)
# or stand empty longer (median minutes) than in the baseline, beyond these noise allowances.
RUNOUT_SHARE_TOL = 0.10
STOCKOUT_MIN_TOL = 1.0
PLAN_FIELDS = ("batch_size", "starting_batches", "reorder_point", "max_batches")

Emit = Callable[[str, dict], None]


# ---------------------------------------------------------------- goal


class Goal(BaseModel):
    kind: Literal["plan", "question"] = Field(
        "plan", description="plan = the manager wants a production plan (batch changes, a waste target); question = a "
                            "question about past or recorded nights, answered from the history with no plan")
    waste_cut_pct: float = Field(ge=0, le=80, description="cut waste by this many percent, e.g. 20; 0 = no waste target")
    protect_diets: list[Literal["vegetarian", "vegan", "halal"]] = Field(
        description="dishes with these diet tags must not run out more than they do today")
    protect_dishes: list[str] = Field(description="dish ids that must not run out more than today")
    keep_missed_meals: bool = Field(description="students leaving without a meal must not get worse (default true)")
    keep_wait: bool = Field(description="the 90th-percentile wait must not get worse (default true)")
    summary: str = Field(description="the goal restated in one short sentence")
    unsupported: list[str] = Field(description="parts of the goal these targets cannot express")


GOAL_SYSTEM = """You turn a dining manager's goal into targets for a planning agent that changes batch plans.
Targets: a waste cut in percent; diets (vegetarian, vegan, halal) or dish ids whose dishes must not run out
more than today; whether missed meals and the long wait must stay no worse (true unless the manager says otherwise).
Use only dish ids from the table. Put anything else (prices, staffing, other halls) in `unsupported`.
If the manager asks a question about what happened (past nights, history, "which dishes ran out on rainy Fridays?")
rather than asking for a plan, set kind = question, waste_cut_pct = 0 and no protected diets or dishes."""


def is_question(text: str) -> bool:
    """Keyword guess (no AI): a question about the history, not a goal to plan for."""
    q = text.lower().strip()
    asks = q.endswith("?") or re.match(r"^(which|what|when|how|did|does|do|is|are|was|were|show|list|why)\b", q)
    planning = re.search(r"\b(cut|reduce|lower|plan|keep|without|protect)\b", q) and re.search(r"\d+\s*(%|percent)", q)
    return bool(asks) and not planning


def _goal_mock(text: str, cfg: ScenarioConfig) -> Goal:
    q = text.lower()
    if is_question(text):
        return Goal(kind="question", waste_cut_pct=0, protect_diets=[], protect_dishes=[], keep_missed_meals=True,
                    keep_wait=True, summary=text.strip()[:160], unsupported=[])
    m = re.search(r"(\d+(?:\.\d+)?)\s*(%|percent)", q)
    cut = float(m.group(1)) if m else (20.0 if "waste" in q else 0.0)
    diets = [d for d in DIETS if d in q or (d == "vegetarian" and "veggie" in q) or (d == "vegan" and "plant" in q)]
    dishes = [d.id for d in cfg.menu if d.id in cfg.kitchen and len(d.name) > 3 and d.name.lower() in q]
    parts = [f"cut waste {cut:g}%"] if cut else []
    if diets:
        parts.append(f"no {'/'.join(diets)} dish runs out more than today")
    if dishes:
        parts.append(f"protect {len(dishes)} named dish{'es' if len(dishes) > 1 else ''}")
    return Goal(waste_cut_pct=min(cut, 80), protect_diets=diets, protect_dishes=dishes, keep_missed_meals=True,
                keep_wait=True, summary=", ".join(parts).capitalize() or text[:120], unsupported=[])


def parse_goal(llm: LLM, text: str, date: str) -> tuple[Goal, str]:
    cfg = load_baseline(date)
    table = "\n".join(f"{d.id} | {d.name} | {', '.join(d.diet_tags) or '-'}" for d in cfg.menu if d.id in cfg.kitchen)

    def check(g: Goal) -> list[str]:
        bad = [x for x in g.protect_dishes if x not in cfg.kitchen]
        return [f"unknown or made-to-order dish ids: {bad}"] if bad else []

    reply = llm.structured("agent_goal", GOAL_VERSION, system=GOAL_SYSTEM,
                           user=f"Dishes (id | name | diet tags):\n{table}\n\nGoal: {text}", schema=Goal,
                           mock=lambda: _goal_mock(text, cfg), check=check, effort="low",
                           cache_inputs={"date": date, "goal": text})
    g = reply.value
    if reply.problems:  # drop what still doesn't validate rather than fail the run
        g = g.model_copy(update={"protect_dishes": [x for x in g.protect_dishes if x in cfg.kitchen]})
    return g, reply.source


def protected_ids(goal: Goal, cfg: ScenarioConfig) -> set[str]:
    diets = set(goal.protect_diets)
    return {d.id for d in cfg.menu if d.id in cfg.kitchen and (diets & set(d.diet_tags) or d.id in goal.protect_dishes)}


# ---------------------------------------------------------------- the agent's levers


class Scale(BaseModel):
    target: str
    batch_size: float | None = None
    reorder_point: float | None = None
    max_batches: float | None = None
    starting_batches: float | None = None


class DishSet(BaseModel):
    dish_id: str
    batch_size: int | None = Field(None, ge=1)
    starting_batches: int | None = Field(None, ge=0)
    reorder_point: int | None = Field(None, ge=0)
    max_batches: int | None = Field(None, ge=0)


class TryPlan(BaseModel):
    note: str = ""
    preset: str | None = None
    scale: list[Scale] = []
    dishes: list[DishSet] = []


def _num(desc: str) -> dict:
    return {"type": "number", "description": desc}


TOOLS = [
    {
        "name": "run_simulation",
        "description": "Simulate tonight's dinner and late night with a changed batch plan (paired nights against "
                       "the baseline, as many as the budget says) and check it against the goal. Changes apply on top of the chosen preset: "
                       "first every `scale` entry in order (factors multiply the current values), then `dishes` "
                       "(absolute values). Returns waste, missed meals, wait, the goal check, the protected dishes "
                       "that got worse and the dishes that still waste the most.",
        "parameters": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "One or two sentences for the manager: what you change "
                                                          "and why, citing numbers from earlier tries."},
                "preset": {"type": "string", "enum": list(PRESET_CHOICES),
                           "description": "starting point: none (tonight's plan), forecast_reorder (cook to the "
                                          "expected rush), smaller_batches (favorites in half batches), trim_slow"},
                "scale": {"type": "array", "description": "multiply plan fields for a group of dishes",
                          "items": {"type": "object", "properties": {
                              "target": {"type": "string", "description": "'unprotected', 'protected', 'all', a "
                                                                          "station id, or a dish id"},
                              "batch_size": _num("factor for portions per batch, e.g. 0.8"),
                              "reorder_point": _num("factor for the pan level that starts the next batch"),
                              "max_batches": _num("factor for the most extra batches allowed"),
                              "starting_batches": _num("factor for pans ready at opening")},
                              "required": ["target"]}},
                "dishes": {"type": "array", "description": "set exact plan values for single dishes",
                           "items": {"type": "object", "properties": {
                               "dish_id": {"type": "string"},
                               "batch_size": {"type": "integer"}, "starting_batches": {"type": "integer"},
                               "reorder_point": {"type": "integer"}, "max_batches": {"type": "integer"}},
                               "required": ["dish_id"]}},
            },
            "required": ["note"],
        },
    },
    {
        "name": "finish",
        "description": "Hand the manager your plan. Call it when a try meets the goal, or when the budget is used "
                       "up (then pick the closest try and say plainly that the goal was not met).",
        "parameters": {
            "type": "object",
            "properties": {
                "try_id": {"type": "integer", "description": "the try to recommend"},
                "reasoning": {"type": "string", "description": "3-5 sentences for the manager: what the plan changes, "
                                                               "what each try taught you, and the trade-off. Use only "
                                                               "numbers the tools returned."},
                "caveats": {"type": "string", "description": "one sentence: what to watch tonight"},
            },
            "required": ["try_id", "reasoning"],
        },
    },
]


TIGER_TOOLS = [
    {
        "name": "query_tiger",
        "description": "Ask the recorded history in Tiger Data (through the Tiger MCP server): run ONE read-only "
                       "PostgreSQL SELECT on schema dcsim and get back at most 40 rows. Aggregate in SQL. Use it to "
                       "learn from past nights (which dishes ran out, waste by weekday/weather, forecast accuracy). "
                       f"At most {MAX_TIGER} lookups per run; they don't use simulation tries.",
        "parameters": {"type": "object", "properties": {
            "sql": {"type": "string", "description": "one SELECT (or WITH ... SELECT) statement, no semicolons"},
            "why": {"type": "string", "description": "one short sentence for the manager: what you want to learn"}},
            "required": ["sql", "why"]},
    },
    {
        "name": "tiger_schema",
        "description": "The full dcsim schema in Tiger with table and column comments (long). Only if the short "
                       "schema you were given isn't enough.",
        "parameters": {"type": "object", "properties": {}},
    },
]
ANSWER_TOOL = {
    "name": "answer",
    "description": "Give the manager your answer, built only from what query_tiger returned.",
    "parameters": {"type": "object", "properties": {
        "answer": {"type": "string", "description": "2-6 sentences with the numbers from the query results"},
        "caveats": {"type": "string", "description": "one sentence: limits of the data (e.g. SYNTHETIC history, few nights)"}},
        "required": ["answer"]},
}


def plan_to_request(date: str, plan: TryPlan, protected: set[str]) -> ScenarioRequest:
    """TryPlan -> ScenarioRequest with absolute kitchen levers. Raises ValueError with a message for the model."""
    preset = None if plan.preset in (None, "", "none") else plan.preset
    if preset is not None and preset not in PRESET_CHOICES:
        raise ValueError(f"unknown preset {preset!r}; choose from {list(PRESET_CHOICES)}")
    start = build_config(ScenarioRequest(date=date, preset=preset))
    batch = [d for d in start.menu if d.id in start.kitchen]
    stations = {s.id for s in start.stations}
    vals = {d.id: {f: getattr(start.kitchen[d.id], f) for f in PLAN_FIELDS} for d in batch}

    def targets(t: str) -> list[str]:
        t = t.strip()
        if t == "all":
            return [d.id for d in batch]
        if t in ("unprotected", "protected"):
            return [d.id for d in batch if (d.id in protected) == (t == "protected")]
        if t in stations:
            return [d.id for d in batch if d.station == t]
        if t in vals:
            return [t]
        raise ValueError(f"unknown scale target {t!r}: use 'unprotected', 'protected', 'all', a station id or a dish id")

    for s in plan.scale:
        for f in PLAN_FIELDS:
            k = getattr(s, f)
            if k is None:
                continue
            if not FACTOR_MIN <= k <= FACTOR_MAX:
                raise ValueError(f"factor {f}={k} for {s.target!r} is outside {FACTOR_MIN}-{FACTOR_MAX}")
            for d in targets(s.target):
                v = vals[d][f] * k
                vals[d][f] = max(1, round(v)) if f == "batch_size" else (math.ceil(v) if f == "max_batches" else max(0, round(v)))
    for ds in plan.dishes:
        if ds.dish_id not in vals:
            raise ValueError(f"unknown or made-to-order dish id {ds.dish_id!r}")
        vals[ds.dish_id] |= ds.model_dump(exclude={"dish_id"}, exclude_none=True)

    kitchen = {}
    for d, v in vals.items():
        diff = {f: v[f] for f in PLAN_FIELDS if v[f] != getattr(start.kitchen[d], f)}
        if diff:
            kitchen[d] = KitchenLever(**diff)
    return ScenarioRequest(date=date, preset=preset, levers=Levers(kitchen=kitchen) if kitchen else None)


def describe_plan(plan: TryPlan, names: dict[str, str]) -> list[str]:
    """The try in the manager's words (the full list of changed settings is separate)."""
    out = []
    if plan.preset and plan.preset != "none":
        out.append(f"Start from preset: {plan.preset.replace('_', ' ')}")
    for s in plan.scale:
        label = {"unprotected": "Unprotected dishes", "protected": "Protected dishes", "all": "All dishes"}.get(
            s.target, names.get(s.target, s.target.replace("_", " ").title() + " station"))
        fs = [f"{f.replace('_', ' ')} ×{getattr(s, f):g}" for f in PLAN_FIELDS if getattr(s, f) is not None]
        if fs:
            out.append(f"{label}: {', '.join(fs)}")
    for ds in plan.dishes:
        fs = [f"{f.replace('_', ' ')} {v}" for f, v in ds.model_dump(exclude={"dish_id"}, exclude_none=True).items()]
        if fs:
            out.append(f"{names.get(ds.dish_id, ds.dish_id)}: {', '.join(fs)}")
    return out or ["Tonight's plan, unchanged"]


# ---------------------------------------------------------------- goal check (code, not the LLM)


def check_goal(goal: Goal, protected: set[str], base: dict, res: dict) -> dict:
    """Deterministic: does `res` (with vs_baseline) meet the goal against `base`? Both are Service.result dicts."""
    checks = []
    bw = base["headline"]["waste_total"]["p50"] or 1.0
    vs = res.get("vs_baseline", {}).get("metrics", {})
    waste_pct = 100 * vs["waste_total"]["diff"]["p50"] / bw if vs else 0.0
    if goal.waste_cut_pct > 0:
        ok = waste_pct <= -goal.waste_cut_pct
        checks.append({"name": "waste", "ok": ok,
                       "text": f"Waste {waste_pct:+.1f}% (goal −{goal.waste_cut_pct:g}%)"})
    brow = {r["dish_id"]: r for r in base["cook_sheet"]}
    worse = []
    for r in res["cook_sheet"]:
        if r["dish_id"] not in protected or r["dish_id"] not in brow:
            continue
        b = brow[r["dish_id"]]
        if (r["stockout_min"]["p50"] > b["stockout_min"]["p50"] + STOCKOUT_MIN_TOL
                or r["runout_share"] > b["runout_share"] + RUNOUT_SHARE_TOL):
            worse.append({"dish_id": r["dish_id"], "name": r["name"], "station": r["station"],
                          "runout_share": [b["runout_share"], r["runout_share"]],
                          "empty_min_p50": [b["stockout_min"]["p50"], r["stockout_min"]["p50"]]})
    if protected:
        checks.append({"name": "protected", "ok": not worse,
                       "text": f"{len(worse)} of {len(protected)} protected dishes run out more than today"
                       if worse else f"All {len(protected)} protected dishes run out no more than today"})
    for key, on, label, unit in (("missed_meal", goal.keep_missed_meals, "Missed meals", "students"),
                                 ("wait_p90", goal.keep_wait, "Long wait (p90)", "min")):
        if on and vs:
            v = vs[key]["verdict"]
            checks.append({"name": key, "ok": v != "worse",
                           "text": f"{label}: {v} ({vs[key]['diff']['p50']:+.1f} {unit}, median of paired nights)"})
    return {"met": all(c["ok"] for c in checks), "checks": checks, "waste_pct": round(waste_pct, 1),
            "protected_worse": worse}


def _rank(t: "Try") -> tuple:
    return (not t.check["met"], sum(not c["ok"] for c in t.check["checks"]), t.check["waste_pct"])


# ---------------------------------------------------------------- runs


@dataclass
class Try:
    id: int
    plan: TryPlan
    request: ScenarioRequest
    hash: str
    result: dict
    check: dict
    moves: list[str]
    settings: list[str]


@dataclass
class AgentRun:
    svc: Service
    llm: LLM
    goal_text: str
    date: str
    n_nights: int = 30
    max_tries: int = MAX_TRIES
    emit: Emit = lambda kind, data: None
    tries: list[Try] = field(default_factory=list)
    source: str = "mock"  # who planned: llm, cache, mock (rule-based), mixed
    notices: list[str] = field(default_factory=list)
    tiger_lookups: list[dict] = field(default_factory=list)
    tiger: object | None = None  # TigerMCP, opened on the first lookup

    def __post_init__(self):
        self.max_tries = max(1, min(MAX_TRIES, int(self.max_tries)))
        # The strong (thinking) model by default: it plans far better than flash-lite. DCSIM_AGENT_MODEL=<id> overrides;
        # if it is busy or fails, the run switches to the main model (with its backups), then to the rule-based search.
        m = os.environ.get("DCSIM_AGENT_MODEL", "strong").strip() or "strong"
        self.model = None if self.llm.is_mock else (self.llm.strong_model if m == "strong" else m)
        if self.model == self.llm.model:
            self.model = None  # the main model, with its backups
        self.effort = os.environ.get("DCSIM_AGENT_EFFORT", "low")
        self.used_model = None if self.llm.is_mock else (self.model or self.llm.model)
        self.base_cfg = load_baseline(self.date)
        self.names = {d.id: d.name for d in self.base_cfg.menu}

    # ------------------------------------------------------------ tools

    def simulate(self, cfg: ScenarioConfig, n: int, label: str) -> list:
        def on_batch(done: int, total: int) -> None:
            self.emit("progress", {"what": label, "done": done, "total": total})
        return self.svc.ensure_nights(cfg, n, on_batch)

    def baseline(self) -> dict:
        nights = self.simulate(self.base_cfg, self.n_nights, "baseline")
        self.base_nights = nights
        self.base = self.svc.result(self.base_cfg, nights)
        return self.base

    def run_try(self, plan: TryPlan) -> dict:
        """The run_simulation tool. Returns the small summary the model reads (or {"error": ...})."""
        if len(self.tries) >= self.max_tries:
            return {"error": f"simulation budget used ({self.max_tries} tries); call finish with your best try"}
        try:
            req = plan_to_request(self.date, plan, self.protected)
            cfg = build_config(req)
        except (ValueError, ValidationError) as e:
            return {"error": str(e)}
        k = len(self.tries) + 1
        moves = describe_plan(plan, self.names)
        settings = config_changes(self.base_cfg, cfg)
        self.emit("try_start", {"id": k, "note": plan.note, "moves": moves})
        nights = self.simulate(cfg, self.n_nights, f"try {k}")
        res = self.svc.result(cfg, nights, self.base_nights)
        chk = check_goal(self.goal, self.protected, self.base, res)
        t = Try(k, plan, req, config_hash(cfg), res, chk, moves, settings)
        self.tries.append(t)
        self.emit("try", self.try_public(t))
        return self.try_summary(t)

    def _headline(self, res: dict, key: str) -> dict:
        b, n = self.base["headline"][key]["p50"], res["headline"][key]["p50"]
        out = {"baseline": round(b, 1), "now": round(n, 1)}
        vs = res.get("vs_baseline", {}).get("metrics", {}).get(key)
        if vs:
            out |= {"median_change": round(vs["diff"]["p50"], 1), "verdict": vs["verdict"]}
        return out

    def try_summary(self, t: Try) -> dict:
        brow = {r["dish_id"]: r for r in self.base["cook_sheet"]}
        rows = [r for r in t.result["cook_sheet"] if not r["to_order"]]
        top = sorted((r for r in rows if r["dish_id"] not in self.protected), key=lambda r: -r["waste"]["p50"])[:8]
        return {
            "try_id": t.id,
            "goal_met": t.check["met"],
            "checks": [("PASS " if c["ok"] else "FAIL ") + c["text"] for c in t.check["checks"]],
            "waste_portions": self._headline(t.result, "waste_total"),
            "missed_meals": self._headline(t.result, "missed_meal"),
            "wait_p90_min": self._headline(t.result, "wait_p90"),
            "protected_worse": [{"dish_id": w["dish_id"], "runs_out_share_of_nights": w["runout_share"],
                                 "empty_minutes_median": w["empty_min_p50"]} for w in t.check["protected_worse"][:10]],
            "most_waste_unprotected": [{"dish_id": r["dish_id"], "waste": r["waste"]["p50"],
                                        "baseline_waste": brow[r["dish_id"]]["waste"]["p50"],
                                        "runs_out_share": r["runout_share"]} for r in top],
            "unprotected_running_out_more": [{"dish_id": r["dish_id"], "runs_out_share": [brow[r["dish_id"]]["runout_share"],
                                                                                           r["runout_share"]]}
                                             for r in self.runout_up(t)[:8]],
            "diagnosis": self.diagnosis(t),
            "tries_left": self.max_tries - len(self.tries),
        }

    def runout_up(self, t: Try) -> list[dict]:
        """Unprotected dishes that now run out in many more nights (students then miss their meal or switch)."""
        brow = {r["dish_id"]: r for r in self.base["cook_sheet"]}
        rows = [r for r in t.result["cook_sheet"] if not r["to_order"] and r["dish_id"] not in self.protected
                and r["dish_id"] in brow and r["runout_share"] - brow[r["dish_id"]]["runout_share"] >= 0.2]
        return sorted(rows, key=lambda r: brow[r["dish_id"]]["runout_share"] - r["runout_share"])

    def diagnosis(self, t: Try) -> list[str]:
        """Facts the code reads off the checks (no advice the numbers don't support)."""
        c, out = {x["name"]: x for x in t.check["checks"]}, []
        if t.check["met"]:
            return [f"Try {t.id} passes every check: call finish with try_id {t.id}."]
        if "waste" in c and not c["waste"]["ok"]:
            gap = self.goal.waste_cut_pct + t.check["waste_pct"]
            out.append(f"Waste needs about {gap:.1f} more percentage points of cut.")
        elif "waste" in c:
            out.append(f"Waste is {-self.goal.waste_cut_pct - t.check['waste_pct']:.1f} points past the goal: "
                       "there is room to cut less if another check fails.")
        if t.check["protected_worse"]:
            ids = ", ".join(w["dish_id"] for w in t.check["protected_worse"][:10])
            out.append(f"Protected dishes running out more: {ids}. They run out when their pans aren't refilled in "
                       "time (reorder point) or the batch cap is hit (max batches), or when students switch to them "
                       "from an unprotected dish that ran out.")
        for key in ("missed_meal", "wait_p90"):
            if key in c and not c[key]["ok"]:
                up = len(self.runout_up(t))
                out.append(("Missed meals got worse" if key == "missed_meal" else "The long wait got worse")
                           + f"; {up} unprotected dishes now run out in at least 20 points more nights."
                           + (" Lowering max_batches caps how much can be cooked." if any(
                               s.max_batches is not None and s.max_batches < 1 for s in t.plan.scale) else ""))
        return out

    def try_public(self, t: Try) -> dict:
        return {"id": t.id, "note": t.plan.note, "moves": t.moves, "settings": t.settings, "config_hash": t.hash,
                "check": t.check, "waste": self._headline(t.result, "waste_total"),
                "missed_meal": self._headline(t.result, "missed_meal"), "wait_p90": self._headline(t.result, "wait_p90"),
                "request": t.request.model_dump(exclude_none=True)}

    # ------------------------------------------------------------ planners

    def intro(self) -> str:
        rows = []
        for r in self.base["cook_sheet"]:
            if r["to_order"]:
                continue
            p = r["plan"]
            rows.append(f"{r['dish_id']} | {r['station']} | {r['period']} | {'PROTECTED' if r['dish_id'] in self.protected else '-'} | "
                        f"{p['batch_size']}/{p['starting_batches']}/{p['reorder_point']}/{p['max_batches']} | "
                        f"{r['waste']['p50']:g} | {r['runout_share']:.2f} | {r['stockout_min']['p50']:g}")
        h = self.base["headline"]
        g = self.goal
        return (
            f"Goal from the manager: {self.goal_text}\n"
            f"Targets: waste cut ≥ {g.waste_cut_pct:g}%; protected dishes ({len(self.protected)}, diets "
            f"{', '.join(g.protect_diets) or 'none'}) must not run out more than today (share of nights +{RUNOUT_SHARE_TOL:.2f}, "
            f"median empty minutes +{STOCKOUT_MIN_TOL:g}); missed meals no worse: {g.keep_missed_meals}; "
            f"long wait no worse: {g.keep_wait}.\n"
            f"Budget: {self.max_tries} simulations of {self.n_nights} nights each.\n\n"
            f"Tonight ({self.date}, a {_weekday(self.date)}), baseline medians: waste {h['waste_total']['p50']:g} portions, missed meals "
            f"{h['missed_meal']['p50']:g}, wait p90 {h['wait_p90']['p50']:g} min.\n"
            "Batch-cooked dishes (id | station | period | protected | batch/opening pans/reorder point/max extra batches "
            "| waste p50 | share of nights it runs out | median empty minutes):\n" + "\n".join(rows)
        )

    # ------------------------------------------------------------ Tiger (through the Tiger MCP server)

    def tiger_on(self) -> bool:
        from .tiger_mcp import available
        return not self.llm.is_mock and available()

    def tiger_tool(self, name: str, args: dict) -> dict:
        from .tiger_mcp import MAX_ROWS, TigerError, TigerMCP

        if len(self.tiger_lookups) >= MAX_TIGER:
            return {"error": f"Tiger lookup limit reached ({MAX_TIGER} per run)"}
        try:
            if self.tiger is None:
                self.tiger = TigerMCP()
            if name == "tiger_schema":
                text = self.tiger.schema()
                self.tiger_lookups.append({"why": "Read the database schema", "sql": None, "row_count": None})
                self.emit("tiger", {"n": len(self.tiger_lookups), "why": "Read the database schema", "sql": None})
                return {"schema": text[:12000]}
            sql, why = str(args.get("sql", "")), str(args.get("why", ""))
            out = self.tiger.query(sql)
            rec = {"why": why, "sql": sql, "row_count": out["row_count"], "columns": out["columns"],
                   "rows": out["rows"][:12], "truncated": out["truncated"]}
        except TigerError as e:
            rec = {"why": str(args.get("why", "")), "sql": str(args.get("sql", "")), "error": str(e)}
            out = {"error": str(e)}
        self.tiger_lookups.append(rec)
        self.emit("tiger", {"n": len(self.tiger_lookups)} | rec)
        return out if "error" in out else out | {"note": f"at most {MAX_ROWS} rows shown"}

    def close(self) -> None:
        if self.tiger is not None:
            self.tiger.close()
            self.tiger = None

    # ------------------------------------------------------------ the Gemini tool loop

    def tool_loop(self, system: str, tools: list[dict], intro: str, turns: int, on_call) -> object | None:
        """Gemini function calling until `on_call(name, args)` returns (_, value) with a value; None if out of turns."""
        contents: list[dict] = [{"role": "user", "parts": [{"text": intro}]}]
        sources = set()
        for _turn in range(turns):
            try:
                content, src, model = self.llm.tool_turn("agent", PROMPT_VERSION, system=system, contents=contents,
                                                         tools=tools, model=self.model, effort=self.effort)
            except LLMError as e:
                if self.model is None:
                    raise
                self.notice(f"{self.model} didn't answer ({e}); Sam carries on with {self.llm.model}.")
                self.model = None
                content, src, model = self.llm.tool_turn("agent", PROMPT_VERSION, system=system, contents=contents,
                                                         tools=tools, effort=self.effort)
            self.used_model = model
            sources.add(src)
            self.source = "cache" if sources == {"cache"} else "llm"
            contents.append(content)
            replies = []
            for part in content.get("parts", []):
                if part.get("text") and not part.get("thought"):
                    self.emit("thought", {"text": part["text"]})
                call = part.get("function_call")
                if not call:
                    continue
                name, args = call.get("name"), call.get("args") or {}
                if name in ("query_tiger", "tiger_schema") and any(t["name"] == name for t in tools):
                    out, done = self.tiger_tool(name, args), None
                else:
                    out, done = on_call(name, args)
                if done is not None:
                    return done
                fr = {"name": name, "response": {"result": out}}
                if call.get("id"):
                    fr["id"] = call["id"]
                replies.append({"function_response": fr})
            if not replies:
                replies = [{"text": "Call one of your tools."}]
            contents.append({"role": "user", "parts": replies})
        return None

    def plan_with_llm(self) -> tuple[int, str, str] | None:
        """Gemini plans. Returns (try_id, reasoning, caveats), or None if it stopped without finishing."""
        def on_call(name: str, args: dict):
            if name == "finish":
                tid = args.get("try_id")
                if any(t.id == tid for t in self.tries):
                    return None, (int(tid), str(args.get("reasoning", "")), str(args.get("caveats", "")))
                return {"error": f"no try {tid}; tries so far: {[t.id for t in self.tries]}"}, None
            if name == "run_simulation":
                try:
                    return self.run_try(TryPlan.model_validate(args)), None
                except ValidationError as e:
                    return {"error": f"bad arguments: {e.error_count()} errors: {str(e)[:300]}"}, None
            return {"error": f"unknown tool {name!r}"}, None

        tiger = self.tiger_on()
        tools = TOOLS + (TIGER_TOOLS if tiger else [])
        intro = self.intro() + (f"\n\n{_tiger_digest()}" if tiger else "")
        return self.tool_loop(SYSTEM + (TIGER_RULES if tiger else ""), tools, intro,
                              self.max_tries + MAX_TIGER + 4, on_call)

    def answer_question(self) -> dict:
        """Question mode: answer from Tiger's history only (no simulations)."""
        base = {"kind": "question", "agent": AGENT_NAME, "goal_text": self.goal_text, "goal": self.goal.model_dump(),
                "date": self.date, "notices": self.notices}
        if self.llm.is_mock:
            self.source = "mock"
            return base | {"answer": "Sam needs Gemini (GEMINI_API_KEY in engine/.env) to turn a free question into "
                                     "a database query. Without it, ask for a plan instead, e.g. 'cut waste 15%'.",
                           "caveats": "", "tiger_lookups": [], "source": "mock", "model": None}
        if not self.tiger_on():
            raise RuntimeError("Tiger isn't reachable: set TIGER_SERVICE_ID in engine/.env and log in with "
                               "`tiger auth login` (or DCSIM_TIGER_MCP=0 turned it off)")

        def on_call(name: str, args: dict):
            if name == "answer":
                if not any("error" not in x for x in self.tiger_lookups if x.get("sql")):
                    return {"error": "run at least one successful query_tiger first"}, None
                return None, (str(args.get("answer", "")), str(args.get("caveats", "")))
            return {"error": f"unknown tool {name!r}"}, None

        intro = (f"Question from the manager (tonight is {self.date}): {self.goal_text}\n\n{_tiger_digest()}")
        got = self.tool_loop(QUESTION_SYSTEM, TIGER_TOOLS + [ANSWER_TOOL], intro, MAX_TIGER + 3, on_call)
        answer, caveats = got or ("Sam couldn't settle on an answer within its lookups; the queries it ran are below.", "")
        return base | {"answer": answer, "caveats": caveats, "tiger_lookups": self.tiger_lookups,
                       "source": self.source, "model": self.used_model}

    def plan_with_rules(self) -> tuple[int, str, str]:
        """No-AI planner: cut unprotected batches step by step, shore up protected dishes that run out more."""
        steps = [0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.5]
        i, boost = 0, {}  # step index; protected dish -> reorder/max factor
        while len(self.tries) < self.max_tries:
            last = self.tries[-1] if self.tries else None
            if last and last.check["met"]:
                break
            why = "Shrink batches and reorder points of the unprotected dishes; leave the protected ones alone."
            if last:
                c = {x["name"]: x["ok"] for x in last.check["checks"]}
                bits = []
                for w in last.check["protected_worse"]:
                    boost[w["dish_id"]] = round(boost.get(w["dish_id"], 1.0) * 1.3, 3)
                if last.check["protected_worse"]:
                    bits.append(f"{len(last.check['protected_worse'])} protected dishes ran out more, so they start "
                                "their next batch earlier and may cook more")
                if not c.get("waste", True) and i < len(steps) - 1:
                    i += 1
                    bits.append(f"waste was {last.check['waste_pct']:+.1f}%, short of the goal, so cut deeper")
                elif not (c.get("missed_meal", True) and c.get("wait_p90", True)) and i > 0:
                    i -= 1
                    bits.append("students lost meals or waited longer, so back off the cut")
                why = ("; ".join(bits) or "repeat with small changes") + "."
                why = why[0].upper() + why[1:]
            f = steps[i]
            plan = TryPlan(note=why, scale=[Scale(target="unprotected", batch_size=f, reorder_point=f)]
                           + [Scale(target=d, reorder_point=min(b, FACTOR_MAX), max_batches=min(b, FACTOR_MAX))
                              for d, b in sorted(boost.items())])
            self.run_try(plan)
        best = min(self.tries, key=_rank)
        bc = best.check
        steps_txt = "; ".join(f"try {t.id}: waste {t.check['waste_pct']:+.1f}%, "
                              f"{len(t.check['protected_worse'])} protected dishes worse" for t in self.tries)
        reasoning = (f"I ran {len(self.tries)} tries ({steps_txt}). Try {best.id} "
                     + ("meets every target" if bc["met"] else "comes closest but does not meet every target")
                     + f": waste {bc['waste_pct']:+.1f}%. Its changes: " + "; ".join(best.moves) + ".")
        return best.id, reasoning, "Rule-based search (no AI key set); the numbers come from the simulator."

    # ------------------------------------------------------------ the whole run

    def run(self) -> dict:
        self.emit("start", {"agent": AGENT_NAME, "goal_text": self.goal_text, "date": self.date,
                            "provider": self.llm.provider, "model": self.used_model, "max_tries": self.max_tries,
                            "n_nights": self.n_nights})
        self.goal, goal_src = parse_goal(self.llm, self.goal_text, self.date)
        if self.goal.kind == "question":
            self.emit("goal", {"goal": self.goal.model_dump(), "source": goal_src, "protected": [], "baseline": None})
            return self.answer_question()
        self.protected = protected_ids(self.goal, self.base_cfg)
        base = self.baseline()
        self.emit("goal", {"goal": self.goal.model_dump(), "source": goal_src,
                           "protected": [{"dish_id": d, "name": self.names[d]} for d in sorted(self.protected)],
                           "baseline": {k: base["headline"][k]["p50"] for k in ("waste_total", "missed_meal", "wait_p90")},
                           "baseline_hash": base["config_hash"]})
        picked, by = None, "agent"
        if not self.llm.is_mock:
            try:
                picked = self.plan_with_llm()
                if picked is None:
                    self.notice("Sam ran out of turns without choosing; picking the closest try by the goal check.")
            except LLMError as e:
                self.notice(f"Gemini stopped answering ({e}); continuing with the rule-based search.")
                self.source = "mixed" if self.tries else "mock"
        if picked is None and len(self.tries) < self.max_tries and not (self.tries and min(self.tries, key=_rank).check["met"]):
            picked, by = self.plan_with_rules(), "rules"
        if not self.tries:
            raise RuntimeError("no simulation ran")
        if picked is None:
            best = min(self.tries, key=_rank)
            picked, by = (best.id, f"Try {best.id} came closest to the goal.", ""), "goal check"
        tid, reasoning, caveats = picked
        chosen = next(t for t in self.tries if t.id == tid)
        best = min(self.tries, key=_rank)
        chosen_by = by
        if not chosen.check["met"] and best.check["met"]:
            self.notice(f"Sam picked try {tid}, which misses the goal; try {best.id} meets it, so that is the plan.")
            chosen, chosen_by = best, "goal check"
        return self.final(chosen, chosen_by, reasoning, caveats)

    def notice(self, text: str) -> None:
        self.notices.append(text)
        self.emit("notice", {"text": text})

    def holdout(self, t: Try) -> dict:
        """Re-run the plan on seeds the agent never saw (N+1..2N) and check the goal there."""
        self.emit("validate_start", {"try_id": t.id, "seeds": [self.n_nights + 1, 2 * self.n_nights]})
        cfg = build_config(t.request)
        n2 = 2 * self.n_nights
        bn = [m for m in self.simulate(self.base_cfg, n2, "baseline, unseen nights") if m.seed > self.n_nights]
        sn = [m for m in self.simulate(cfg, n2, "plan, unseen nights") if m.seed > self.n_nights]
        base = self.svc.result(self.base_cfg, bn)
        res = self.svc.result(cfg, sn, bn)
        return check_goal(self.goal, self.protected, base, res) | {"seeds": [self.n_nights + 1, n2]}

    def final(self, t: Try, chosen_by: str, reasoning: str, caveats: str) -> dict:
        hold = self.holdout(t)
        brow = {r["dish_id"]: r for r in self.base["cook_sheet"]}
        plan_kitchen = build_config(t.request).kitchen
        changed = []
        for r in t.result["cook_sheet"]:
            b = brow.get(r["dish_id"])
            if r["to_order"] or b is None:
                continue
            before = {f: b["plan"][f] for f in PLAN_FIELDS}
            after = {f: r["plan"][f] for f in PLAN_FIELDS}
            if plan_kitchen[r["dish_id"]] == self.base_cfg.kitchen[r["dish_id"]]:
                continue
            changed.append({"dish_id": r["dish_id"], "name": r["name"], "station": r["station"], "period": r["period"],
                            "protected": r["dish_id"] in self.protected, "diet_tags": r["diet_tags"],
                            "before": before, "after": after,
                            "last_batch": r["plan"]["last_batch_clock"],
                            "waste": [b["waste"]["p50"], r["waste"]["p50"]],
                            "runout_share": [b["runout_share"], r["runout_share"]]})
        changed.sort(key=lambda c: c["waste"][1] - c["waste"][0])
        nights = (len(self.tries) + 1) * self.n_nights + 2 * self.n_nights  # baseline + tries, then unseen seeds for both
        return {
            "kind": "plan", "agent": AGENT_NAME, "goal_text": self.goal_text, "goal": self.goal.model_dump(), "date": self.date,
            "chosen": t.id, "chosen_by": chosen_by, "reasoning": reasoning, "caveats": caveats,
            "check": t.check, "holdout": hold, "request": t.request.model_dump(exclude_none=True),
            "config_hash": t.hash, "baseline_hash": self.base["config_hash"],
            "label": f"Sam's plan: {self.goal.summary}"[:120],
            "moves": t.moves, "settings": t.settings, "changed_dishes": changed,
            "tries": [self.try_public(x) for x in self.tries], "notices": self.notices,
            "source": self.source, "model": self.used_model if self.source != "mock" else None,
            "nights_simulated": nights, "tiger_lookups": self.tiger_lookups,
        }


SYSTEM = f"""You are {AGENT_NAME}, the UMass Minuteman, working as the planning agent for the Worcester Commons
dining manager. You find a batch plan for tonight that meets the manager's goal by running the simulator yourself.

How to work:
- Each run_simulation call simulates a set of paired nights (same seeds as the baseline). Read its goal checks, then adjust and try again.
- Change as little as needed. Prefer broad moves first ('unprotected' dishes, a station), then fix single dishes.
- Waste is food cooked but not eaten. Smaller batches (batch_size) with matching reorder points cut it the most
  while pans keep getting refilled; lowering max_batches caps the food and makes dishes run out (missed meals).
- Leave protected dishes out of the cuts (target 'unprotected'). A protected dish that runs out more needs a
  higher reorder_point (start the next batch sooner) or more max_batches; set it per dish id.
- Students who find their dish gone pick another, so cutting unprotected dishes too far makes protected ones
  run out and students miss meals. If waste is past the goal but another check fails, cut a little less.
- Each result has a `diagnosis` computed from the checks: read it before the next try.
- Presets are blunt; tonight's plan ('none') plus your own changes is usually the better start.
- Stop as soon as a try meets every check: call finish. If the budget runs out, finish with the closest try and
  say plainly which checks fail.
- In notes and reasoning, use only numbers the tools returned. Be brief and friendly, like a calm Minuteman
  ready at a minute's notice; no jokes about the numbers."""

TIGER_RULES = """
- You can look at the recorded history in Tiger Data with query_tiger (it doesn't use tries). Before your first
  try, make one lookup: which protected dishes ran out, and when, on past observed nights with the same weekday as
  tonight. Use it to pick the dishes that need a higher reorder point from the start. More lookups only if they
  change your next move. The history is SYNTHETIC; say so if you rely on it."""

QUESTION_SYSTEM = f"""You are {AGENT_NAME}, the UMass Minuteman, answering the Worcester Commons dining manager's
question from the recorded history in Tiger Data. Write read-only SQL with query_tiger (one SELECT, aggregate in
the database, keep results small), check the result, refine if needed, then call answer.
- Use only numbers the queries returned. Recorded ("observed") history is SYNTHETIC: say so in caveats.
- For "ran out" use pan_windows.portions_left = 0, excluding each dish's last window of the night (close-out).
- Be brief and friendly, like a calm Minuteman."""


def _weekday(date: str) -> str:
    import datetime as _dt
    return _dt.date.fromisoformat(date).strftime("%A")


def _tiger_digest() -> str:
    from .tiger_mcp import SCHEMA_DIGEST
    return SCHEMA_DIGEST


def history_entry(final: dict) -> dict:
    """What Sam's history keeps: the final analysis only (no per-try details)."""
    keep = ("kind", "agent", "goal_text", "goal", "date", "answer", "caveats", "reasoning", "chosen", "chosen_by",
            "check", "holdout", "request", "config_hash", "baseline_hash", "label", "moves", "changed_dishes",
            "source", "model", "nights_simulated", "notices")
    out = {k: final[k] for k in keep if k in final}
    out["tries_run"] = len(final.get("tries") or [])
    out["tiger_lookups"] = [{k: x.get(k) for k in ("why", "sql", "row_count", "error")} for x in final.get("tiger_lookups") or []]
    if final.get("kind") == "question":
        out["headline"] = re.sub(r"[*_`#]+", "", final.get("answer") or "").strip()[:160]
    else:
        c = final.get("check") or {}
        out["headline"] = (f"{'Goal met' if c.get('met') else 'Closest plan'} · waste {c.get('waste_pct', 0):+.1f}% "
                           f"· try {final.get('chosen')} of {out['tries_run']}")
    return out


def run_agent(svc: Service, llm: LLM, goal: str, date: str, *, n_nights: int = 30, max_tries: int = MAX_TRIES,
              emit: Emit | None = None) -> dict:
    """Plan for a goal (at most MAX_TRIES simulations) or answer a question from Tiger."""
    run = AgentRun(svc, llm, goal, date, n_nights=n_nights, max_tries=max_tries, emit=emit or (lambda k, d: None))
    try:
        return run.run()
    finally:
        run.close()


__all__ = ["AGENT_NAME", "Goal", "TryPlan", "Scale", "DishSet", "check_goal", "parse_goal", "plan_to_request",
           "protected_ids", "run_agent", "AgentRun", "TOOLS", "TIGER_TOOLS", "MAX_TRIES", "history_entry", "is_question"]
