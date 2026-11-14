import numpy as np

from dcsim.nightsearch import DIM, embed


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
