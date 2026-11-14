"""Core guarantees of the engine (ENGINE.md §9), on the real Worcester menus."""

from __future__ import annotations

import json

import pytest

from dcsim import compare, config_hash, load_baseline, run_night, run_scenario
from dcsim.arrivals import sample_arrivals
from dcsim.data import available_dates, default_data_dir
from dcsim.menu_import import classify, diet_tags, serving_oz
from dcsim.population import build_students, eligible
from dcsim.presets import PRESETS

SEEDS = [1, 2]
DATE = "2026-11-13"


@pytest.fixture(scope="module")
def cfg():
    return load_baseline(DATE)


def test_every_prepared_date_loads():
    dates = available_dates()
    assert len(dates) == 8
    for date in dates:
        c = load_baseline(date)
        assert c.date == date and len(c.menu) > 30


def test_determinism(cfg):
    assert run_night(cfg, 7).metrics.to_dict() == run_night(cfg, 7).metrics.to_dict()


@pytest.mark.parametrize("preset", [None, *sorted(PRESETS)])
def test_conservation(cfg, preset):
    """Every cooked portion is served, expired or left over."""
    c = PRESETS[preset](cfg) if preset else cfg
    for seed in SEEDS:
        for dish_id, d in run_night(c, seed).metrics.dishes.items():
            assert d.cooked == d.served + d.expired + d.leftover, (preset, seed, dish_id)


def test_to_order_dishes_never_waste_or_run_out(cfg):
    m = run_night(cfg, 3).metrics
    to_order = [d for d in m.dishes.values() if d.to_order]
    assert to_order, "expected Street Food / Sushi / Display dishes"
    for d in to_order:
        assert d.waste == 0 and d.stockout_min == 0 and d.runout_min is None and d.cooked == d.served


def test_hash_ignores_label_and_order(cfg):
    assert config_hash(cfg.updated(label="x")) == config_hash(cfg)
    assert config_hash(cfg.updated(menu=[d.model_dump() for d in reversed(cfg.menu)])) == config_hash(cfg)


def test_hash_changes_with_levers(cfg):
    assert config_hash(PRESETS["smaller_batches"](cfg)) != config_hash(cfg)


def test_noop_change_gives_zero_diff(cfg):
    base = run_scenario(cfg, n_nights=2, workers=1)
    same = run_scenario(cfg.updated(label="copy"), n_nights=2, workers=1)
    for m in compare(base, same)["metrics"].values():
        assert m["diff"] == {"p10": 0.0, "p50": 0.0, "p90": 0.0}


def test_bigger_crowd_keeps_baseline_students(cfg):
    base_ids, _ = sample_arrivals(cfg, 5)
    more_ids, _ = sample_arrivals(cfg.updated(crowd=cfg.crowd.model_dump() | {"multiplier": 1.1}), 5)
    assert set(base_ids.tolist()) <= set(more_ids.tolist()) and len(more_ids) > len(base_ids)


def test_removing_a_dish_keeps_existing_tastes(cfg):
    ids, times = sample_arrivals(cfg, 3)
    dropped = next(d.id for d in cfg.menu if d.id in cfg.kitchen)
    smaller = cfg.updated(
        menu=[d.model_dump() for d in cfg.menu if d.id != dropped],
        kitchen={k: v.model_dump() for k, v in cfg.kitchen.items() if k != dropped},
    )
    full_ids, small_ids = [d.id for d in cfg.menu], [d.id for d in smaller.menu]
    for a, b in zip(build_students(cfg, 3, ids, times), build_students(smaller, 3, ids, times), strict=True):
        assert [small_ids[i] for i in b.prefs] == [full_ids[i] for i in a.prefs if full_ids[i] != dropped]


def test_diet_rules_respected(cfg):
    trace = run_night(cfg, 2, trace=True).trace
    dishes = {d.id: d for d in cfg.menu}
    served = 0
    for s in trace["students"]:
        for e in s["events"]:
            if e["type"] == "served":
                served += 1
                assert eligible(s["diet"], dishes[e["dish"]]), (s["diet"], e["dish"])
    assert served > 0


def test_one_line_per_station_visit(cfg):
    """Items from the same station are taken in a single pass through its line."""
    trace = run_night(cfg, 4, trace=True).trace
    multi_item_visits = 0
    for s in trace["students"]:
        served_this_visit = 0
        for e in s["events"]:
            if e["type"] == "queue_join":
                served_this_visit = 0
            elif e["type"] == "served":
                served_this_visit += 1
                if served_this_visit == 2:
                    multi_item_visits += 1
    assert multi_item_visits > 50  # e.g. tikka masala + rice in one pass through Tandoor


def test_trace_shape(cfg):
    trace = run_night(cfg, 1, trace=True).trace
    s = next(s for s in trace["students"] if any(e["type"] == "served" for e in s["events"]))
    assert s["events"][0]["type"] == "enter" and s["events"][-1]["type"] == "exit"
    assert next(e for e in s["events"] if e["type"] == "served")["vessel"] in {"plate", "bowl", "cup"}


def test_seating_zones_split_by_seats(cfg):
    zones = cfg.layout.zones()
    assert {z.id for z in zones} == {"main", "back"}
    trace = run_night(cfg, 1, trace=True).trace
    seats = [e["seat"] for s in trace["students"] for e in s["events"] if e["type"] == "sit"]
    share_back = seats.count("back") / len(seats)
    expected = next(z.seats for z in zones if z.id == "back") / sum(z.seats for z in zones)
    assert abs(share_back - expected) < 0.03


def test_back_room_walk_goes_through_a_door(cfg):
    from dcsim.model import Night

    n = Night(cfg, 1)
    back = next(z for z in cfg.layout.zones() if z.id == "back")
    desk = cfg.layout.entrance
    straight = ((back.pos[0] - desk[0]) ** 2 + (back.pos[1] - desk[1]) ** 2) ** 0.5 / cfg.layout.walk_speed_m_per_min
    assert n.walk(desk, back.pos) > straight  # can't walk through the stair core / partition
    assert n.walk(desk, back.pos) == n.walk(back.pos, desk)


# ---------------------------------------------------------------- menu import


def test_serving_size_parsing():
    assert serving_oz("5 2/3 OZL") == pytest.approx(5.667, abs=1e-3)
    assert serving_oz("1/4 OZ") == 0.25
    assert serving_oz("1 each") is None


@pytest.mark.parametrize(
    ("station", "name", "serving", "role"),
    [
        ("tandoor", "Chicken Tikka Masala Rasoi", "4 oz", "entree"),
        ("tandoor", "Coriander Chutney", "1 oz", "addon"),
        ("latino", "Chicken Taco Filling", "1 oz", "entree"),
        ("latino", "Sour Cream", "1 OZ", "addon"),
        ("mediterranean", "Linguini Pasta", "4 OZ", "starch"),
        ("noodle_bowl", "Birria Ramen (Beef)", "1 each", "entree"),
        ("noodle_bowl", "Hard Boiled Egg", "1 each", "addon"),
        ("sushi", "Purple Sweet Potato Tempura Sushi Roll", "1 roll", "entree"),
        ("grill", "Roasted Cauliflower", "3 oz", "side"),
        ("soups", "Tomato Basil Soup", "6 oz", "soup"),
    ],
)
def test_classify(station, name, serving, role):
    assert classify(station, name, serving) == role


def test_diet_label_mapping():
    assert diet_tags(["Halal", "Plant Based"]) == ["halal", "vegan", "vegetarian"]
    assert diet_tags(["Local", "Vegetarian"]) == ["vegetarian"]


def test_imported_menu_matches_source():
    menu = json.loads((default_data_dir() / "menus" / f"{DATE}_dinner.json").read_text())
    names = {d["name"] for d in menu["dishes"]}
    assert {"Chicken Tikka Masala Rasoi", "Eggplant Parmesan", "California Roll"} <= names
    assert "Sour Cream" in {a["name"] for a in menu["addons"]}


# ---------------------------------------------------------------- demo story


def test_staff_favorite_runs_out(cfg):
    """With a popularity override from experience, that dish should run out under the neutral plan."""
    overrides = json.loads((default_data_dir() / "overrides.json").read_text()).get("popularity", {})
    favorites = [n for n, mult in overrides.items() if mult > 1.3]
    on_menu = [d for d in cfg.menu if d.name in favorites and d.id in cfg.kitchen]
    if not on_menu:
        pytest.skip("no staff favorites in overrides.json for this date yet")
    res = run_scenario(cfg, n_nights=6, workers=1)
    assert any(res.summary["dishes"][d.id]["runout_share"] >= 0.5 for d in on_menu)


def test_outcome_metrics_match_definitions(cfg):
    """missed_meal = got nothing, or wanted hot and left without hot food; missed first = never got it."""
    from dcsim.model import Night

    night = Night(cfg, 1).run()
    oc = night.rec.outcomes
    m = run_night(cfg, 1).metrics
    hot = {i: d.hot for i, d in enumerate(cfg.menu)}
    assert all(o.wanted_hot == (o.first_choice >= 0 and hot[o.first_choice]) for o in oc)
    assert m.missed_meal == sum(1 for o in oc if o.items == 0 or (o.wanted_hot and not o.got_hot))
    # A cold first choice with some food on the plate is never a missed meal.
    assert m.missed_meal < sum(1 for o in oc if not o.got_hot)


def test_return_trip_can_clear_missed_first(cfg):
    """With the come-back behavior on, fewer students end the night without their favorite."""
    off = cfg.model_copy(update={"retry_balked_favorite": False})
    on = cfg.model_copy(update={"retry_balked_favorite": True})
    assert run_night(on, 1).metrics.missed_first_choice_rate <= run_night(off, 1).metrics.missed_first_choice_rate


# ---------------------------------------------------------------- E1: deciding from a distance


def test_students_never_walk_away_from_a_line(cfg):
    """Students look at lines from where they stand and walk only to the one they join: every
    walk to a station ends in its line (queue_join), and a skipped line is never joined on the
    same glance. Nobody tours the hall (old engine: a third skipped 10+ lines)."""
    trace = run_night(cfg, 3, trace=True).trace
    skips = []
    for s in trace["students"]:
        ev = s["events"]
        skips.append(sum(e["type"] == "balk" for e in ev))
        first_trip = ev[: next((i for i, e in enumerate(ev) if e["type"] == "sit"), len(ev))]
        joins = [e for e in first_trip if e["type"] == "queue_join"]
        # each walk ends in a line: at most one queue per plate item, plus a retry if a pan ran dry
        assert len(joins) <= 6, s["id"]
        # a skipped station is skipped once per trip, not re-announced each glance
        balked = [e["station"] for e in first_trip if e["type"] == "balk"]
        assert len(balked) == len(set(balked)), s["id"]
    assert max(skips) <= len(cfg.stations)
    assert sum(k >= 10 for k in skips) == 0


# ---------------------------------------------------------------- E2: late night 9 pm-12 am


def test_late_night_period(cfg):
    assert cfg.late_close_min == 24 * 60 and cfg.service_end == 24 * 60
    late = [d for d in cfg.menu if d.period == "late_night"]
    assert late and all(d.id.startswith("late.") for d in late)
    trace = run_night(cfg, 2, trace=True).trace
    period = {d.id: d.period for d in cfg.menu}
    late_students = 0
    for s in trace["students"]:
        arrive = s["events"][0]["t"]
        late_students += arrive >= cfg.close_min
        for e in s["events"]:
            if e["type"] != "served":
                continue
            # dinner food is served until the 9 pm lines clear (last call), late-night food only 9 pm-12 am
            if period[e["dish"]] == "late_night":
                assert cfg.close_min <= e["t"] <= cfg.late_close_min + 3, (s["id"], e)
            else:
                assert e["t"] <= cfg.close_min + 30 + 15, (s["id"], e)  # last call; a sushi order already started may finish
            # each student eats from the period they arrived in
            assert (period[e["dish"]] == "late_night") == (arrive >= cfg.close_min), (s["id"], e)
            # nobody is served after serving stops at midnight (a service already started may finish)
            assert e["t"] <= cfg.late_close_min + 3
    assert 800 <= late_students <= 1200


@pytest.mark.parametrize("date", available_dates())
def test_hall_nearly_empty_by_1130(date):
    """Late-night arrivals taper so that fewer than 30 students are inside at 11:30 pm."""
    c = load_baseline(date)
    inside = [run_night(c, seed).metrics.in_hall_before_close for seed in (1, 2, 3)]
    assert sum(inside) / len(inside) < 30, inside


def test_dinner_arrivals_unchanged_by_late_night(cfg):
    """Late night has its own random stream: the dinner crowd is the same with or without it."""
    no_late = cfg.updated(late_crowd=None)
    ids_a, t_a = sample_arrivals(cfg, 5)
    ids_b, t_b = sample_arrivals(no_late, 5)
    dinner = t_a < cfg.close_min
    assert (ids_a[dinner] == ids_b).all() and (t_a[dinner] == t_b).all()


def test_crowd_lever_scales_late_night(cfg):
    from dcsim.levers import Levers, apply_levers

    big = apply_levers(cfg, Levers(crowd_multiplier=1.2))
    assert big.late_crowd.multiplier == pytest.approx(cfg.late_crowd.multiplier * 1.2)
    assert big.crowd.multiplier == pytest.approx(cfg.crowd.multiplier * 1.2)


# ---------------------------------------------------------------- E5: crowd by date


def test_crowd_by_date():
    from dcsim.data import crowd_factors

    fri, wed_event = load_baseline("2026-11-13"), load_baseline("2026-11-18")
    assert fri.crowd.multiplier == pytest.approx(crowd_factors("2026-11-13")[0])
    assert crowd_factors("2026-11-13")[0] < 1.0  # Friday dinner guess
    # Friendsgiving lifts only its own date; the synthetic history (events=False) keeps the weekday factor
    assert wed_event.crowd.multiplier > load_baseline("2026-11-18", events=False).crowd.multiplier
    assert load_baseline("2026-11-17").crowd.multiplier == pytest.approx(1.0)


# ---------------------------------------------------------------- E7: Nov 18 carving line


def test_nov18_carving_line():
    c = load_baseline("2026-11-18")
    turkey = next(d for d in c.menu if "Turkey" in d.name and d.period == "dinner")
    assert turkey.station == "carving"
    st = c.station("carving")
    assert not st.self_serve and not st.to_order and turkey.id in c.kitchen  # staffed line, roasts in batches
    assert all(d.station != "carving" for d in load_baseline("2026-11-17").menu)


# ---------------------------------------------------------------- late-night waste taper


def test_late_night_taper_cuts_waste():
    c = load_baseline(DATE)
    late = [d.id for d in c.menu if d.period == "late_night" and d.id in c.kitchen]
    assert all(c.kitchen[k].taper_from_min is not None for k in late)
    assert all(c.kitchen[k].taper_from_min is None for k in c.kitchen if k not in late)  # dinner unchanged
    waste = cooked = 0
    for seed in (1, 2):
        m = run_night(c, seed).metrics
        waste += sum(m.dishes[k].waste for k in late)
        cooked += sum(m.dishes[k].cooked for k in late)
    assert waste / cooked < 0.3  # was ~41% before the taper
