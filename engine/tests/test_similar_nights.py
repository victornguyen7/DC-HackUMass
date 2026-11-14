"""Similar Nights (docs/SIMILAR_NIGHTS.md): feature vectors, ranking, validation, and the GET route.

Runs on the local synthetic files (DCSIM_SIMILAR_LOCAL=1), never on Tiger.
"""

from __future__ import annotations

import csv
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from dcsim import similar_nights as sn
from dcsim.data import available_dates

MENU = available_dates()[0]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("DCSIM_CACHE", str(tmp_path_factory.mktemp("cache")))
    mp.setenv("DCSIM_WORKERS", "1")
    mp.setenv("DCSIM_SIMILAR_LOCAL", "1")
    from dcsim.api import app

    with TestClient(app) as c:
        yield c
    mp.undo()


def _history_dir(tmp_path, nights):
    """Write a tiny history in the synthetic file layout: (date, students, weather, event, dishes)."""
    with (tmp_path / "nights.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "weekday", "menu_source_date", "students", "waste_lb", "stockout_min"])
        for d, students, *_ in nights:
            w.writerow([d, "", MENU, students, 100.0, 20.0])
    with (tmp_path / "consumption.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "dish_id"])
        for d, *_, dishes in nights:
            for dish in dishes:
                w.writerow([d, dish])
    (tmp_path / "_ground_truth.json").write_text(json.dumps(
        {"nights": [{"date": d, "weather": weather, "event_night": event} for d, _, weather, event, _ in nights]}))
    return tmp_path


# ---------------------------------------------------------------- vectors

def test_vector_is_unit_length_with_the_documented_layout():
    v = sn.feature_vector("2026-11-14", 4000, "rain", False, frozenset({"a.x", "b.y"}))  # a Saturday
    assert v.shape == (48,) and np.isclose(np.linalg.norm(v), 1.0)
    raw = v / v[5] * 1.7  # undo the unit scaling using the weekday weight
    assert np.isclose(raw[5], 1.7) and np.isclose(raw[7 + 4], 0.25) and np.isclose(raw[7 + 6], 0.25)
    assert np.isclose(raw[14 + sn.WEATHERS.index("rain")], 1.8) and np.isclose(raw[20], 0.9)
    assert np.isclose(raw[22], 1.7 * 2.5) and raw[23] == 0  # >= 3750 students saturate
    assert np.isclose(raw[24:].sum(), 2 / np.sqrt(2))


def test_unrecorded_weather_scores_as_other():
    a = sn.feature_vector("2026-11-14", 3000, "cold", False, None)
    b = sn.feature_vector("2026-11-14", 3000, "other", False, None)
    assert np.allclose(a, b)


def test_menu_overlap_is_exact_jaccard_and_null_without_a_menu():
    assert sn.menu_overlap(None, frozenset({"a"})) is None
    assert sn.menu_overlap(frozenset({"a", "b"}), frozenset({"b", "c", "d"})) == 0.25
    assert sn.menu_overlap(frozenset({"a"}), frozenset({"b"})) == 0.0


# ---------------------------------------------------------------- ranking

def test_only_earlier_nights_rank_by_similarity_then_date(tmp_path):
    hist = sn.load_local_history(_history_dir(tmp_path, [
        ("2026-11-07", 4000, "rain", False, ["a"]),   # Saturday, rain: best
        ("2026-10-31", 4000, "rain", False, ["a"]),   # same score, earlier: ranks first of the tie
        ("2026-11-05", 4000, "clear", False, ["a"]),  # Thursday, clear: worse
        ("2026-11-14", 4000, "rain", False, ["a"]),   # the target date itself: excluded
        ("2026-11-21", 4000, "rain", False, ["a"]),   # after the target: excluded
    ]))
    r = sn.search_local(sn.validate("2026-11-14", 4000, "rain"), hist)
    assert [m["date"] for m in r["matches"]] == ["2026-10-31", "2026-11-07", "2026-11-05"]
    assert r["matches"][0]["similarity"] == r["matches"][1]["similarity"] == 1.0
    assert r["matches"][0]["same_weekday"] and r["matches"][0]["same_weather"]
    assert not r["matches"][2]["same_weekday"] and not r["matches"][2]["same_weather"]
    assert r["history_count"] == 5 and r["source"] == "local-synthetic" and r["synthetic"] is True


def test_unknown_menu_ignores_candidate_menus(tmp_path):
    hist = sn.load_local_history(_history_dir(tmp_path, [
        ("2026-11-07", 4000, "rain", False, ["a"]),
        ("2026-10-31", 4000, "rain", False, [f"d{i}" for i in range(40)]),
    ]))
    r = sn.search_local(sn.validate("2026-11-14", 4000, "rain"), hist)
    assert [m["similarity"] for m in r["matches"]] == [1.0, 1.0]
    assert all(m["menu_overlap"] is None for m in r["matches"])


def test_selected_menu_scores_dishes_and_reports_overlap(tmp_path):
    menu = sorted(sn.menu_dishes(MENU))
    hist = sn.load_local_history(_history_dir(tmp_path, [
        ("2026-10-31", 4000, "rain", False, menu),       # same dishes
        ("2026-11-07", 4000, "rain", False, ["other.dish"]),
    ]))
    r = sn.search_local(sn.validate("2026-11-14", 4000, "rain", menu_date=MENU), hist)
    assert [m["date"] for m in r["matches"]] == ["2026-10-31", "2026-11-07"]
    assert r["matches"][0]["menu_overlap"] == 1.0 and r["matches"][1]["menu_overlap"] == 0.0
    assert r["matches"][0]["similarity"] > r["matches"][1]["similarity"]
    assert r["query"]["menu_date"] == MENU


def test_limit_caps_matches_on_the_real_history():
    r = sn.search_local(sn.validate("2026-11-14", 4000, "rain", limit=3))
    assert len(r["matches"]) == 3 and r["history_count"] >= 40
    sims = [m["similarity"] for m in r["matches"]]
    assert sims == sorted(sims, reverse=True)
    assert all(m["date"] < "2026-11-14" and m["waste_lb"] is not None for m in r["matches"])


# ---------------------------------------------------------------- API

def test_api_unknown_and_selected_menu(client):
    r = client.get("/api/similar-nights", params={"date": "2026-11-14", "students": 4000, "weather": " Rain "})
    assert r.status_code == 200
    j = r.json()
    assert j["query"] == {"date": "2026-11-14", "weekday": "Saturday", "students": 4000, "weather": "rain",
                          "event_night": False, "menu_date": None}
    assert len(j["matches"]) == 5 and all(m["menu_overlap"] is None for m in j["matches"])
    assert set(j) == {"query", "matches", "source", "synthetic", "history_count", "method", "note"}
    assert set(j["matches"][0]) == {"date", "similarity", "students", "weather", "event_night", "weekday",
                                    "waste_lb", "stockout_min", "menu_overlap", "same_weekday", "same_weather"}

    r = client.get("/api/similar-nights", params={"date": "2026-11-14", "students": 4000, "weather": "rain",
                                                  "event_night": "true", "menu_date": MENU, "limit": 2})
    assert r.status_code == 200
    j = r.json()
    assert len(j["matches"]) == 2 and all(isinstance(m["menu_overlap"], float) for m in j["matches"])
    assert j["query"]["event_night"] is True and j["query"]["menu_date"] == MENU


def test_api_empty_history_before_the_first_night(client):
    r = client.get("/api/similar-nights", params={"date": "2026-10-01", "students": 4000})
    assert r.status_code == 200 and r.json()["matches"] == []


@pytest.mark.parametrize("params", [
    {"students": 4000},                                            # missing date
    {"date": "Nov 14", "students": 4000},                          # not ISO
    {"date": "2026-11-14", "students": 0},
    {"date": "2026-11-14", "students": 100_001},
    {"date": "2026-11-14", "students": "many"},
    {"date": "2026-11-14", "students": 4000, "weather": "hail"},
    {"date": "2026-11-14", "students": 4000, "limit": 0},
    {"date": "2026-11-14", "students": 4000, "limit": 21},
    {"date": "2026-11-14", "students": 4000, "menu_date": ""},     # Unknown menu = omit it, not ""
    {"date": "2026-11-14", "students": 4000, "menu_date": "2025-01-01"},
    {"date": "2026-11-14", "students": 4000, "event_night": "maybe"},
])
def test_api_rejects_bad_input(client, params):
    r = client.get("/api/similar-nights", params=params)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]


def test_api_maps_tiger_failure_to_503(client, monkeypatch):
    class Broken:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            raise RuntimeError("relation dcsim.nights does not exist")

    monkeypatch.setattr(sn, "_connect", lambda: Broken())
    r = client.get("/api/similar-nights", params={"date": "2026-11-14", "students": 4000})
    assert r.status_code == 503 and r.json()["detail"] == sn.UNAVAILABLE
