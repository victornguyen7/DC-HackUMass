import numpy as np

from dcsim.nightsearch import DIM, FULL_NORM, embed, known_similarity


def cos(a, b):
    return 1 - a @ b / (np.linalg.norm(a) * np.linalg.norm(b))


def test_embedding_is_stable_and_order_free():
    a = embed("Friday", "rain", False, ["x.a", "x.b", "x.c"])
    assert a.shape == (DIM,)
    assert np.allclose(a, embed("Friday", "rain", False, ["x.c", "x.a", "x.b", "x.a"]))


def test_same_menu_and_weekday_is_nearer_than_a_different_weekday():
    menu = [f"s.d{i}" for i in range(40)]
    q = embed("Saturday", None, False, menu)
    same = embed("Saturday", "clear", False, menu)
    other_day = embed("Tuesday", "clear", False, menu)
    other_menu = embed("Saturday", "clear", False, [f"t.e{i}" for i in range(40)])
    assert cos(q, same) < cos(q, other_day)
    assert cos(q, same) < cos(q, other_menu)
    # unknown weather contributes nothing, so it can't favour one past night over another
    assert cos(q, embed("Saturday", "rain", False, menu)) == cos(q, same)


def test_every_recorded_night_has_the_same_length():
    a = embed("Monday", "clear", False, ["x.a"])
    b = embed("Friday", "snow", True, [f"y.{i}" for i in range(30)])
    assert np.isclose(np.linalg.norm(a), FULL_NORM) and np.isclose(np.linalg.norm(b), FULL_NORM)


def test_event_flag_counts_both_ways():
    q = embed("Friday", "rain", False, ["x.a"])
    assert cos(q, embed("Friday", "rain", False, ["x.a"])) < cos(q, embed("Friday", "rain", True, ["x.a"]))


def test_known_similarity_reads_one_for_an_exact_match_on_what_the_query_knows():
    night = embed("Saturday", "rain", False, ["x.a", "x.b"])
    q = embed("Saturday", None, False, ())  # weather and menu not known
    assert np.isclose(known_similarity(q, cos(q, night)), 1.0)
    full = embed("Saturday", "rain", False, ["x.a", "x.b"])
    assert np.isclose(known_similarity(full, cos(full, night)), 1.0)
    assert known_similarity(q, cos(q, embed("Tuesday", "rain", False, ["x.a"]))) < 1.0
