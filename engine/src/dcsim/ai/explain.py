"""Why a scenario won or lost (ENGINE.md §7.1 stretch), from the paired comparison only.

The prompt holds the settings that changed, the paired headline differences
(median and range over the same nights, win rate, verdict) and the dishes whose
waste or stockout moved most. Check: every number in the answer must appear in
those facts, otherwise the template is used. Results are simulated, never
"measured" or "saved".
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

from ..service import Service, baseline_of
from .describe import config_changes
from .llm import LLM

PROMPT_VERSION = "explain-v3"

LABELS = {"waste_total": "wasted portions", "stockout_min_total": "minutes out of stock",
          "missed_first_choice_rate": "share who never got their first choice", "wait_p90": "slowest-10% wait (min)",
          "missed_meal": "missed meals"}


class Explanation(BaseModel):
    text: str


SYSTEM = """You explain to a dining manager why a what-if scenario did better or worse than the current plan in a
dinner simulation. Write at most 3 short sentences, under 80 words. Use only the facts given and copy numbers exactly
as they appear in them (you may round to whole numbers); never compute new numbers. Lead with the result that matters most, name the trade-off if there is one, and say plainly when a
difference is inconclusive. If a dish runs out earlier, you may say when, using the window given. These are simulated results over the same nights, not measurements: never say "saved",
"measured" or "will"."""


def facts(svc: Service, baseline_hash: str, scenario_hash: str, n: int) -> dict:
    cmp = svc.compare_hashes(baseline_hash, scenario_hash, n)
    b_cfg, s_cfg = svc.store.get_config(baseline_hash), svc.store.get_config(scenario_hash)
    if b_cfg is None or s_cfg is None:
        raise KeyError("unknown config")
    b = svc.result(b_cfg, svc.cached_nights(b_cfg, n))
    s = svc.result(s_cfg, svc.cached_nights(s_cfg, n))
    bd, sd = {r["dish_id"]: r for r in b["cook_sheet"]}, {r["dish_id"]: r for r in s["cook_sheet"]}
    moves = []
    for k in bd.keys() & sd.keys():
        dw = sd[k]["waste"]["p50"] - bd[k]["waste"]["p50"]
        ds = sd[k]["stockout_min"]["p50"] - bd[k]["stockout_min"]["p50"]
        moves.append({"dish": sd[k]["name"], "waste_change": round(dw), "stockout_min_change": round(ds)})
    moves.sort(key=lambda m: -(abs(m["waste_change"]) + abs(m["stockout_min_change"])))
    runouts = []  # when dishes run out under the scenario, next to the current plan
    for k in sd:
        w, share = sd[k].get("runout_window"), sd[k].get("runout_share", 0)
        if w and share >= 0.5:
            bw = bd.get(k, {}).get("runout_window")
            runouts.append({"dish": sd[k]["name"], "runs_out_in_share_of_nights": round(share, 2),
                            "scenario_window": f"{w['from']} to {w['to']}",
                            "baseline_window": f"{bw['from']} to {bw['to']}" if bw else "does not run out in most nights",
                            "_p50": w["p50_min"]})
    runouts = [{k: v for k, v in r.items() if k != "_p50"} for r in sorted(runouts, key=lambda r: r["_p50"])[:4]]
    return {
        "nights": cmp["n_pairs"],
        "changes": config_changes(b_cfg, s_cfg),
        "metrics": [{"metric": LABELS[k], "baseline_median": b["headline"][k]["p50"], "scenario_median": s["headline"][k]["p50"],
                     "change_median": v["diff"]["p50"], "change_range": [v["diff"]["p10"], v["diff"]["p90"]],
                     "change_pct_of_baseline": round(100 * v["diff"]["p50"] / b["headline"][k]["p50"], 1)
                     if b["headline"][k]["p50"] else None,
                     "better_in_share_of_nights": v["win_rate"], "verdict": v["verdict"]}
                    for k, v in cmp["metrics"].items()],
        "biggest_dish_moves": [m for m in moves if m["waste_change"] or m["stockout_min_change"]][:4],
        "earliest_runouts": runouts,
    }


def _numbers(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for v in obj.values():
            out |= _numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _numbers(v)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        x = abs(float(obj))
        for y in (x, x * 100):  # shares may be written as percentages
            out |= {f"{round(y):d}", f"{round(y, 1):g}", f"{round(y, 2):g}"}
    elif isinstance(obj, str):
        out |= set(re.findall(r"\d+(?:\.\d+)?", obj))
    return out


def check(text: str, f: dict) -> list[str]:
    allowed = _numbers(f)
    used = re.findall(r"\d+(?:[.,]\d+)?", text.replace(",", ""))
    bad = sorted({u for u in used if f"{float(u):g}" not in allowed and u not in allowed})
    errs = [f"numbers not in the facts: {bad}"] if bad else []
    if re.search(r"\b(saved|measured|will)\b", text, re.I):
        errs.append("simulated results: don't say saved, measured or will")
    if len(text) > 700:
        errs.append("too long")
    return errs


def template(f: dict) -> str:
    better = [m for m in f["metrics"] if m["verdict"] == "better"]
    worse = [m for m in f["metrics"] if m["verdict"] == "worse"]

    def num(m, x):
        return f"{x * 100:.1f}%" if m["metric"].startswith("share") else (f"{x:.0f}" if abs(x) >= 10 else f"{x:.1f}")

    def one(m):
        return (f"{m['metric']} {num(m, m['baseline_median'])} -> {num(m, m['scenario_median'])} "
                f"(better in {round(m['better_in_share_of_nights'] * 100)}% of {f['nights']} nights)")

    parts = []
    if better:
        parts.append("Better: " + "; ".join(one(m) for m in better) + ".")
    if worse:
        parts.append("Worse: " + "; ".join(one(m) for m in worse) + ".")
    if not better and not worse:
        parts.append(f"No clear difference on any headline number over {f['nights']} simulated nights.")
    if f["biggest_dish_moves"]:
        m = f["biggest_dish_moves"][0]
        parts.append(f"Biggest dish change: {m['dish']} ({m['waste_change']:+d} wasted portions, "
                     f"{m['stockout_min_change']:+d} min out of stock).")
    return " ".join(parts)


def explain(llm: LLM, svc: Service, baseline_hash: str, scenario_hash: str, n: int = 30) -> dict:
    f = facts(svc, baseline_hash, scenario_hash, n)
    base = {"facts": f, "prompt_version": PROMPT_VERSION, **llm.info()}
    reply = llm.structured(
        "explain", PROMPT_VERSION, system=SYSTEM, user=json.dumps(f, indent=1), schema=Explanation,
        mock=lambda: Explanation(text=template(f)), check=lambda e: check(e.text, f), effort="low", max_tokens=600,
        cache_inputs={"baseline": baseline_hash, "scenario": scenario_hash, "n": n},
    )
    if reply.source in ("mock", "fallback"):
        return base | {"text": reply.value.text, "source": "template"} | ({"reason": reply.error} if reply.error else {})
    if reply.problems:
        return base | {"text": template(f), "source": "template", "reason": reply.problems[0]}
    return base | {"text": reply.value.text, "source": reply.source}


def default_baseline(svc: Service, scenario_hash: str) -> str:
    cfg = svc.store.get_config(scenario_hash)
    if cfg is None:
        raise KeyError(f"unknown config {scenario_hash}")
    from ..config import config_hash

    return config_hash(baseline_of(cfg))
