"""Similar menu dishes for a new dish, and near-duplicate detection (Gemini embeddings).

Used by the new-dish wizard: the manager types a name, we suggest the menu
dishes it is most like, and the manager confirms them as `similar_to`. The
suggestion only picks *which* dishes to borrow from; the numbers are still the
geometric mean of the chosen dishes (levers.add_dishes), so a run stays
deterministic. Without Gemini (mock, Groq, or an API error) the ranking falls
back to shared words + same role + same station.

`near_duplicates` flags pairs of texts that mean nearly the same thing (e.g.
persona labels + descriptions). It is a standalone check: the persona pipeline
is paused (LLM.md §4) and does not call it yet.
"""

from __future__ import annotations

import math
import re

from ..config import ScenarioConfig
from ..data import load_baseline
from .llm import LLM, LLMError

_WORD = re.compile(r"[a-z]+")
_STOP = {"w", "with", "and", "the", "of", "a", "in", "on", "style", "fresh"}


def _vec_cos(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def _words(s: str) -> set[str]:
    return {w for w in _WORD.findall(s.lower()) if w not in _STOP and len(w) > 1}


def _describe(name: str, role: str | None, station: str | None) -> str:
    return " | ".join(x for x in (name, role, station) if x)


def similar_dishes(llm: LLM, date: str, name: str, station: str | None = None, role: str | None = None,
                   k: int = 3, cfg: ScenarioConfig | None = None) -> dict:
    """Top-k menu dishes most like `name`. Same role is preferred when `role` is given."""
    cfg = cfg or load_baseline(date)
    if station is not None and station not in {s.id for s in cfg.stations}:
        raise ValueError(f"unknown station {station!r}")
    st_name = cfg.station(station).name if station else None
    menu = list(cfg.menu)
    texts = [_describe(d.name, d.role, cfg.station(d.station).name) for d in menu]
    query = _describe(name, role, st_name)

    source, error = "embedding", None
    try:
        vecs = llm.embed(texts + [query])
        q = vecs[-1]
        sims = [_vec_cos(v, q) for v in vecs[:-1]]
    except LLMError as e:
        source, error = "keywords", str(e)
        qw = _words(name)
        sims = [len(qw & _words(d.name)) / max(1, len(qw | _words(d.name))) for d in menu]

    def score(i: int) -> float:  # role and station are tie-breakers, not overrides
        d = menu[i]
        return sims[i] + (0.05 if role and d.role == role else 0) + (0.02 if station and d.station == station else 0)

    order = sorted(range(len(menu)), key=lambda i: (-score(i), menu[i].id))
    if role:
        same = [i for i in order if menu[i].role == role]
        order = same + [i for i in order if menu[i].role != role] if same else order
    picks = order[:k]
    return {
        "query": {"name": name, "station": station, "role": role},
        "suggestions": [{"dish_id": menu[i].id, "name": menu[i].name, "station": menu[i].station,
                         "role": menu[i].role, "similarity": round(sims[i], 3)} for i in picks],
        "similar_to": [menu[i].id for i in picks],
        "source": source, "model": llm.embed_model if source == "embedding" else None, "error": error,
        "note": "Suggestions only choose which dishes to borrow popularity from; confirm them before running.",
    }


def near_duplicates(llm: LLM, items: dict[str, str], threshold: float = 0.92) -> list[dict]:
    """Pairs of `items` (id -> text) whose embeddings are closer than `threshold`. Raises LLMError without Gemini."""
    ids = list(items)
    vecs = llm.embed([items[i] for i in ids])
    out = []
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            s = _vec_cos(vecs[a], vecs[b])
            if s >= threshold:
                out.append({"a": ids[a], "b": ids[b], "similarity": round(s, 3)})
    return sorted(out, key=lambda x: -x["similarity"])


__all__ = ["similar_dishes", "near_duplicates"]
