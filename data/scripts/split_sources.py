"""Split the raw research uploads and menu snapshot into small topic files.

    python3 data/scripts/split_sources.py

Reads  data/raw/research/*.json, data/raw/menu/worcester_menus_*.json
Writes data/sources/**  (one topic per file, citations in sources/registry.json)
       data/menu/<date>/<meal>.json  (one meal per file, source_attributes/icons dropped)
       data/worcester/calendar.json  (sim dates -> weekday + dated events that touch Worcester)

Values are copied, never edited or filled in: null stays null. Re-run after
replacing a raw file; outputs are overwritten.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

DATA = Path(__file__).resolve().parents[1]
RAW = DATA / "raw"
OUT = DATA / "sources"

FILES = {
    "attendance": RAW / "research" / "worcester_dining_popularity_report.json",
    "population": RAW / "research" / "umass_campus_dining_demand_profile.json",
    "waste": RAW / "research" / "umass_food_waste_FY2017_FY2026.json",
    "ecosystem": RAW / "research" / "umass_dining_current_tools_ecosystem.json",
    "audit": RAW / "research" / "worcester_simulation_assumption_evidence.json",
}


def load(p: Path) -> dict:
    return json.loads(p.read_text())


def write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def source_ids(obj) -> list[str]:
    """Every source id referenced anywhere inside obj."""
    found: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("source_id", "operational_guidance_source_id") and isinstance(v, str):
                    found.add(v)
                elif k in ("source_ids",) and isinstance(v, list):
                    found.update(x for x in v if isinstance(x, str))
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(obj)
    return sorted(found)


def topic(raw_key: str, raw: dict, body: dict, as_of: str | None) -> dict:
    return {
        "_from": str(FILES[raw_key].relative_to(DATA)),
        "as_of": as_of,
        "source_ids": source_ids(body),
        "null_means": "not established by the reviewed sources, not zero",
        **body,
    }


def build_registry(raws: dict[str, dict]) -> dict:
    reg: dict[str, dict] = {}
    conflicts = []
    for key, raw in raws.items():
        for s in raw.get("sources", []):
            sid = s.get("source_id") or s.get("id")
            entry = {k: v for k, v in s.items() if k not in ("source_id", "id", "supports_record_ids")}
            if sid in reg:
                for k, v in entry.items():
                    old = reg[sid].get(k)
                    if old is None:
                        reg[sid][k] = v
                    elif v is not None and v != old:
                        conflicts.append({"source_id": sid, "field": k, "values": [old, v]})
            else:
                reg[sid] = dict(entry)
            reg[sid].setdefault("cited_in", [])
            reg[sid]["cited_in"].append(str(FILES[key].relative_to(DATA)))
    # Different uploads use different id schemes (e.g. "fy26" and "S16"); link ids that share a URL.
    by_url: dict[str, list[str]] = {}
    for sid, s in reg.items():
        if s.get("url"):
            by_url.setdefault(s["url"].rstrip("/"), []).append(sid)
    for ids in by_url.values():
        for sid in ids:
            if len(ids) > 1:
                reg[sid]["same_as"] = sorted(i for i in ids if i != sid)
    return {"sources": dict(sorted(reg.items())), "conflicts": conflicts}


def split_attendance(raw: dict) -> None:
    d = OUT / "attendance"
    a = raw["prepared_date"]
    write(d / "worcester_meal_volume.json", topic("attendance", raw, {
        "location": raw["location"],
        "summary": raw["summary"],
        "reported_metrics": raw["reported_metrics"],
        "data_quality": raw["data_quality"],
        "project_use": raw["project_use"],
    }, a))
    write(d / "missing_and_data_request.json", topic("attendance", raw, {
        "metric_definitions": raw["metric_definitions"],
        "missing_data": raw["missing_data"],
        "recommended_data_request": raw["recommended_data_request"],
    }, a))


def split_population(raw: dict) -> None:
    d = OUT / "population"
    a = raw["prepared_date"]
    rules = {"evidence_rules": raw["evidence_rules"]}
    write(d / "enrollment.json", topic("population", raw, {"population": raw["population"]}, a))
    write(d / "international.json", topic("population", raw, {"international_community": raw["international_community"]}, a))
    write(d / "diet_patterns.json", topic("population", raw, {"diet_patterns": raw["diet_patterns"]}, a))
    write(d / "allergies.json", topic("population", raw, {"allergies_and_intolerances": raw["allergies_and_intolerances"]}, a))
    write(d / "cuisine_preferences.json", topic("population", raw, {
        "cuisine_preferences": raw["cuisine_preferences"],
        "popular_dish": raw["popular_dish"],
    }, a))
    write(d / "athletics.json", topic("population", raw, {"athletics_and_fitness": raw["athletics_and_fitness"]}, a))
    write(d / "religious_diets.json", topic("population", raw, {"religious_and_special_diets": raw["religious_and_special_diets"]}, a))
    write(d / "choice_drivers.json", topic("population", raw, {"stated_choice_drivers": raw["stated_choice_drivers"]}, a))
    write(d / "model_features_and_gaps.json", topic("population", raw, {
        **rules,
        "candidate_model_features": raw["candidate_model_features"],
        "missing_measurements": raw["missing_measurements"],
        "recommended_next_data": raw["recommended_next_data"],
    }, a))
    write(OUT / "calendar" / "events_2026-11.json", topic("population", raw, {
        "timezone": raw["timezone"],
        "calendar_events": raw["calendar_events"],
        "multi_day_events": raw["multi_day_events"],
        "event_interpretation": raw["event_interpretation"],
        "recurring_or_historical_event_types": raw["recurring_or_historical_event_types"],
    }, a))


def split_waste(raw: dict) -> None:
    d = OUT / "waste"
    a = raw["as_of_date"]
    write(d / "annual_timeline.json", topic("waste", raw, {
        "coverage": raw["coverage"],
        "annual_series_complete": raw["annual_series_complete"],
        "metric_definitions": raw["metric_definitions"],
        "annual_timeline": raw["annual_timeline"],
    }, a))
    write(d / "comparisons.json", topic("waste", raw, {
        "valid_comparisons": raw["valid_comparisons"],
        "nonannual_or_undated_observations": raw["nonannual_or_undated_observations"],
    }, a))
    write(d / "tool_claims.json", topic("waste", raw, {
        "causal_tool_attribution_available": raw["causal_tool_attribution_available"],
        "tool_impact_claims": raw["tool_impact_claims"],
        "historical_instrument_context": raw["historical_instrument_context"],
    }, a))
    write(d / "destinations_and_targets.json", topic("waste", raw, {
        "destination_distribution_fy2026": raw["destination_distribution_fy2026"],
        "future_targets": raw["future_targets"],
    }, a))
    write(d / "limits_and_needed_data.json", topic("waste", raw, {
        "ten_year_total_reduction_pct": raw["ten_year_total_reduction_pct"],
        "comparability_and_attribution_limits": raw["comparability_and_attribution_limits"],
        "data_needed_for_full_history": raw["data_needed_for_full_history"],
        "null_policy": raw["null_policy"],
    }, a))
    # Handy flat series for charts: only the two reported intensity values.
    write(d / "oz_per_swipe.json", {
        "_from": str(FILES["waste"].relative_to(DATA)),
        "metric": "total food waste, oz per meal swipe, campus-wide",
        "source_ids": ["fy25", "fy26"],
        "series": [
            {"fiscal_year": r["fiscal_year"], "oz_per_swipe": r["food_waste_per_swipe_oz"]}
            for r in raw["annual_timeline"]
            if r["food_waste_per_swipe_oz"] is not None
        ],
    })


def split_ecosystem(raw: dict) -> None:
    d = OUT / "ecosystem"
    a = raw["as_of_date"]
    for t in raw["tools"]:
        write(d / "tools" / f"{t['tool_id']}.json", topic("ecosystem", raw, {"scope": raw["scope"], "tool": t}, a))
    write(d / "data_flow.json", topic("ecosystem", raw, {
        "inclusion_policy": raw["inclusion_policy"],
        "tool_ids": [t["tool_id"] for t in raw["tools"]],
        "proposed_data_flow": raw["proposed_data_flow"],
        "measurement_contract": raw["measurement_contract"],
    }, a))


def split_audit(raw: dict) -> None:
    """One file per audited assumption, with its facts and data requests inlined."""
    d = OUT / "audit"
    a = raw["research_as_of"]
    facts = {f["id"]: f for f in raw["verified_context"]}
    reqs = {r["id"]: r for r in raw["next_data_requests"]}
    for c in raw["categories"]:
        slug = "".join(ch if ch.isalnum() else "_" for ch in c["category"].lower()).strip("_")
        while "__" in slug:
            slug = slug.replace("__", "_")
        write(d / "assumptions" / f"{c['id']}_{slug}.json", topic("audit", raw, {
            "assumption": c,
            "facts": [facts[i] for i in c["evidence_fact_ids"]],
            "data_requests": [reqs[i] for i in c["data_request_ids"]],
        }, a))
    write(d / "verified_facts.json", topic("audit", raw, {"verified_context": raw["verified_context"]}, a))
    write(d / "corrections.json", topic("audit", raw, {
        "corrections_to_proposed_interpretations": raw["corrections_to_proposed_interpretations"],
        "excluded_substitutions": raw["excluded_substitutions"],
    }, a))
    write(d / "data_requests.json", topic("audit", raw, {"next_data_requests": raw["next_data_requests"]}, a))
    write(d / "method.json", topic("audit", raw, {
        "scope": raw["scope"],
        "interpretation": raw["interpretation"],
        "search_log": raw["search_log"],
    }, a))
    write(OUT / "calendar" / "target_dates_audit.json", topic("audit", raw, {"target_date_calendar": raw["target_date_calendar"]}, a))


def split_menus() -> list[str]:
    out = DATA / "menu"
    if out.exists():
        shutil.rmtree(out)
    index = []
    for snap in sorted((RAW / "menu").glob("worcester_menus_*.json")):
        raw = load(snap)
        for day in raw["dates"]:
            meals = {}
            for meal, m in day["meals"].items():
                stations = [
                    {
                        "name": s["name"],
                        "items": [{k: v for k, v in it.items() if k not in ("source_attributes", "icons")} for it in s["items"]],
                    }
                    for s in m.get("stations", [])
                ]
                meals[meal] = {"status": m["status"], "item_count": m.get("item_count"), "stations": [s["name"] for s in stations]}
                if m["status"] != "published":
                    continue
                write(out / day["date"] / f"{meal}.json", {
                    "_from": str(snap.relative_to(DATA)),
                    "location": raw["location"],
                    "date": day["date"],
                    "meal": meal,
                    "source_url": day["source_url"],
                    "retrieved_at": day["retrieved_at"],
                    "stations": stations,
                })
            index.append({"date": day["date"], "weekday": dt.date.fromisoformat(day["date"]).strftime("%A"), "meals": meals})
        write(out / "index.json", {
            "_from": str(snap.relative_to(DATA)),
            "notes": raw["notes"] + ["Split copies drop source_attributes and icons; see the raw snapshot for those."],
            "dates": index,
        })
    return [x["date"] for x in index]


def build_calendar(dates: list[str], pop: dict, audit: dict) -> None:
    hours = next(f for f in audit["verified_context"] if f["id"] == "F02")["value"]
    audit_days = {r["date"]: r for r in audit["target_date_calendar"]}
    touches = ("Worcester", "All dining commons", "Campus", "McGuirk Alumni Stadium")
    rows = []
    for date in dates:
        events = [
            {k: e[k] for k in ("name", "locations", "meal_period", "source_id", "measured_demand_lift")}
            for e in pop["calendar_events"]
            if e["date"] == date and any(loc in touches for loc in e["locations"])
        ]
        other = [e["name"] + " (" + ", ".join(e["locations"]) + ")" for e in pop["calendar_events"]
                 if e["date"] == date and not any(loc in touches for loc in e["locations"])]
        wd = dt.date.fromisoformat(date)
        rows.append({
            "date": date,
            "weekday": wd.strftime("%A"),
            "weekend": wd.weekday() >= 5,
            "events_at_or_affecting_worcester": events,
            "events_elsewhere_on_campus": other,
            "building_hours": hours.get(date),
            "football_kickoff": next((e.get("kickoff_time") for e in audit_days.get(date, {}).get("events", []) if "football" in e["name"].lower()), None),
            "crowd_modifier": None,
        })
    write(DATA / "worcester" / "calendar.json", {
        "_note": "Context for each simulated dinner. Not read by the engine yet. crowd_modifier stays null: no event has a measured demand lift. Events elsewhere may pull diners away from Worcester rather than add any. building_hours (07:00-24:00) is the building, not the dinner window; the reviewed table stops at Nov 15. An empty event list does not prove there are no other events.",
        "_from": ["sources/calendar/events_2026-11.json", "sources/calendar/target_dates_audit.json"],
        "dates": rows,
    })


def main() -> None:
    raws = {k: load(p) for k, p in FILES.items()}
    if OUT.exists():
        shutil.rmtree(OUT)
    write(OUT / "registry.json", build_registry(raws))
    split_attendance(raws["attendance"])
    split_population(raws["population"])
    split_waste(raws["waste"])
    split_ecosystem(raws["ecosystem"])
    split_audit(raws["audit"])
    dates = split_menus()
    build_calendar(dates, raws["population"], raws["audit"])
    for p in sorted(OUT.rglob("*.json")):
        print(p.relative_to(DATA))
    print(f"menu: {len(dates)} dates -> data/menu/<date>/<meal>.json; calendar -> data/worcester/calendar.json")


if __name__ == "__main__":
    main()
