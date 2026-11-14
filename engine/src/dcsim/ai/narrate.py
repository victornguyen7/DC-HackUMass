"""Click narration (ENGINE.md §7.4): one student's recorded night in 2-3 first-person sentences.

1. `card()` returns the event timeline straight from the trace, with no LLM.
2. `narrate()` yields the card, then the narration sentence by sentence.
   The prompt holds only the persona description and that student's recorded
   events. Each sentence is checked before it is sent: it may not name a dish
   or station that isn't in the student's events. One failed sentence -> the
   whole narration is replaced by the template.
3. Cached by (config_hash, seed, student_id, prompt_version, model).

Narration never changes a number; it describes what the simulation recorded.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing

from ..config import ScenarioConfig
from ..reports import clock
from ..service import Service
from .llm import LLM, LLMError

PROMPT_VERSION = "narrate-v3"
MAX_CHARS = 600

SYSTEM = """You narrate one simulated student's dinner at a university dining hall, in their voice.

Rules:
- Write 2 or 3 short first-person sentences, plain and casual, like a student telling a friend. No more than 60 words.
- Use only the events you are given. Do not add dishes, stations, people, feelings about specific staff, or anything that is not in the events.
- When you name a dish or station, use the exact name from the events.
- If you give a time or a number of people, copy it exactly from the events. Otherwise leave numbers out.
- Never give the student a name. Never mention that this is a simulation.
- No lists, no headings, no quotation marks around the whole answer."""


# ---------------------------------------------------------------- trace -> card


class _Traces:
    """Small LRU of decompressed traces; a click on the same night doesn't re-read 5 MB."""

    def __init__(self, size: int = 4):
        self.size, self.items = size, OrderedDict()

    def get(self, svc: Service, h: str, seed: int) -> dict | None:
        k = (h, seed)
        if k in self.items:
            self.items.move_to_end(k)
            return self.items[k]
        data = svc.trace_bytes(h, seed)
        if data is None:
            return None
        trace = json.loads(gzip.decompress(data))
        trace["_by_id"] = {s["id"]: s for s in trace["students"]}
        self.items[k] = trace
        if len(self.items) > self.size:
            self.items.popitem(last=False)
        return trace


TRACES = _Traces()


def _people(n: int | None) -> str:
    return "1 person" if n == 1 else f"{n} people"


def _event_text(e: dict) -> str:
    d, st, n = e.get("dish_name"), e.get("station_name"), e.get("line")
    return {
        "enter": "arrived at the dining hall",
        "queue_join": f"joined the {st} line for {d} ({_people(n)} ahead)",
        "balk": f"saw {_people(n)} in the {st} line and chose not to join it (wanted {d})",
        "empty_pan": f"the {d} pan at {st} was empty",
        "served": f"got {d} at {st}",
        "sit": "sat down to eat",
        "seconds": f"went back for seconds: {d}",
        "retry": f"looked at the line for {d} again",
        "closed": f"was still in the {st} line for {d} when that service ended",
        "dish_return": "returned dishes",
        "exit": "left",
    }.get(e["type"], e["type"])


def card(cfg: ScenarioConfig, student: dict) -> dict:
    """Everything the student card shows before any LLM call."""
    persona = next(p for p in cfg.personas if p.id == student["persona"])
    dish = {d.id: d for d in cfg.menu}
    station = {s.id: s.name for s in cfg.stations}
    events = []
    for e in student["events"]:
        x = dict(e)
        if "dish" in x:
            x["dish_name"] = dish[x["dish"]].name
            x.setdefault("station", dish[x["dish"]].station)
        if "station" in x:
            x["station_name"] = station[x["station"]]
        x["clock"] = clock(x["t"])
        x["text"] = _event_text(x)
        events.append(x)
    first = next((e for e in events if e["type"] in ("empty_pan", "balk", "queue_join")), None)
    served = [e for e in events if e["type"] == "served"]
    got_hot = any(dish[e["dish"]].hot for e in served)
    first_dish = dish[first["dish"]] if first else None
    return {
        "student_id": student["id"],
        "title": f"Student #{student['id']} · {persona.label}",
        "persona": {"id": persona.id, "label": persona.label, "description": persona.description, "diet": persona.diet},
        "first_choice": first_dish.name if first_dish else None,
        "got": list(dict.fromkeys(e["dish_name"] for e in served)),
        "got_first_choice": bool(first_dish) and any(e["dish"] == first_dish.id for e in served),
        "missed_meal": not served or (bool(first_dish) and first_dish.hot and not got_hot),
        "events": events,
    }


def highlights(cfg: ScenarioConfig, trace: dict, k: int = 12) -> list[dict]:
    """The most telling students on a night, for pre-warming and the 'interesting students' list."""
    veg = {p.id for p in cfg.personas if p.diet in ("vegetarian", "vegan")}
    buckets: dict[str, list[int]] = {r: [] for r in
                                     ("missed_meal", "came_back", "vegetarian_gap", "switched", "empty_pan", "three_lines")}
    for s in trace["students"]:
        types = [e["type"] for e in s["events"]]
        served = types.count("served")
        if served == 0:
            buckets["missed_meal"].append(s["id"])
        if "retry" in types:
            buckets["came_back"].append(s["id"])
        if s["persona"] in veg and ("balk" in types or "empty_pan" in types):
            buckets["vegetarian_gap"].append(s["id"])
        if "balk" in types and served:
            buckets["switched"].append(s["id"])
        if "empty_pan" in types:
            buckets["empty_pan"].append(s["id"])
        if types.count("queue_join") >= 3:
            buckets["three_lines"].append(s["id"])
    out, seen = [], set()
    while len(out) < k and any(buckets.values()):
        for reason, ids in buckets.items():
            while ids and ids[0] in seen:
                ids.pop(0)
            if ids and len(out) < k:
                sid = ids.pop(len(ids) // 2)  # middle of the night, not the first arrival
                seen.add(sid)
                out.append({"student_id": sid, "reason": reason})
    by_id = trace.get("_by_id") or {s["id"]: s for s in trace["students"]}
    persona = {p.id: p.label for p in cfg.personas}
    for h in out:
        h["title"] = f"Student #{h['student_id']} · {persona[by_id[h['student_id']]['persona']]}"
    return out


# ---------------------------------------------------------------- template + check


def template(c: dict) -> str:
    ev = c["events"]
    sit = next((i for i, e in enumerate(ev) if e["type"] == "sit"), len(ev))
    before, after = ev[:sit], ev[sit:]
    first = c["first_choice"]
    arrive, leave = ev[0], ev[-1]
    served_before = [e for e in before if e["type"] == "served"]
    others = [e["dish_name"] for e in served_before if e["dish_name"] != first]
    sentences = []

    if first is None:
        sentences.append(f"I got here at {arrive['clock']} but couldn't find anything I could eat.")
    elif any(e["dish_name"] == first for e in served_before):
        join = next((e for e in before if e["type"] == "queue_join" and e["dish_name"] == first), None)
        wait = f", behind {_people(join['line'])}" if join and join.get("line") else ""
        sentences.append(f"I got here at {arrive['clock']} and went straight for the {first}{wait}.")
    else:
        balk = next((e for e in before if e["type"] == "balk" and e["dish_name"] == first), None)
        empty = next((e for e in before if e["type"] == "empty_pan" and e["dish_name"] == first), None)
        instead = f"grabbed the {others[0]} instead" if others else "skipped it"
        if balk:
            sentences.append(f"I wanted the {first}, but the {balk['station_name']} line had {_people(balk['line'])}, so I {instead}.")
        elif empty:
            sentences.append(f"I came for the {first}, but the pan was empty, so I {instead}.")
        else:
            sentences.append(f"I wanted the {first} but didn't get it, so I {instead}.")

    retry = next((e for e in after if e["type"] in ("retry", "seconds")), None)
    if retry:
        d = retry["dish_name"]
        got = any(e["type"] == "served" and e["dish_name"] == d for e in after)
        why = "the line was too long" if any(e["type"] == "balk" for e in after) else "it was gone"
        if retry["type"] == "retry":
            sentences.append(f"Later I tried for the {d} again" + (" and finally got it." if got else f", but {why}."))
        else:
            sentences.append(f"I went back for seconds of the {d}" + ("." if got else f", but {why}."))
    elif len(others) >= (1 if first in c["got"] else 2):
        rest = [x for x in c["got"] if x != first and (first in c["got"] or x != others[0])]
        if rest:
            sentences.append("I also got the " + " and the ".join(rest[:2]) + ".")

    mins = round(leave["t"] - arrive["t"])
    sentences.append("I left without eating." if not c["got"] else f"I left at {leave['clock']}, about {mins} minutes after I came in.")
    return " ".join(sentences[:3])


def _names(cfg: ScenarioConfig) -> set[str]:
    return {d.name for d in cfg.menu} | {s.name for s in cfg.stations}


_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_NUM = re.compile(r"(?<![\d:])\d+(?![\d:])")


def _minutes(h: str, m: str) -> int:
    return (int(h) % 12) * 60 + int(m)


def check_facts(text: str, c: dict) -> list[str]:
    """Clock times must match an event (within 1 minute); every other number must appear in the events."""
    clocks = {_minutes(*_TIME.search(e["clock"]).groups()) for e in c["events"] if _TIME.search(e.get("clock") or "")}
    bad = [f"time {h}:{m}" for h, m in _TIME.findall(text)
           if not any(abs(_minutes(h, m) - t) <= 1 for t in clocks)]
    known = {n for e in c["events"] for n in _NUM.findall(_TIME.sub(" ", e["text"]))}
    ts = [e["t"] for e in c["events"] if "t" in e]
    if ts:  # derived facts a student could state: how long they stayed, lines skipped, items got
        stay = round(max(ts) - min(ts))
        known |= {str(stay + d) for d in (-1, 0, 1)}
    known |= {str(sum(e["type"] == "balk" for e in c["events"])), str(len(c["got"]))}
    bad += [f"number {n}" for n in _NUM.findall(_TIME.sub(" ", text)) if n not in known]
    return bad


def check(text: str, c: dict, cfg: ScenarioConfig) -> list[str]:
    """Names, times and numbers in `text` that are not in the student's events (empty = OK)."""
    allowed = {e[k] for e in c["events"] for k in ("dish_name", "station_name") if k in e}
    low = text.lower()
    for n in sorted(allowed, key=len, reverse=True):  # "Fried Rice" allowed shouldn't flag "Rice"
        low = low.replace(n.lower(), " ")
    bad = sorted(n for n in _names(cfg) - allowed if re.search(rf"(?<!\w){re.escape(n.lower())}(?!\w)", low))
    if not c["got"] and re.search(r"\b(ate|eaten|grabbed|my (plate|tray|food))\b", text, re.I):
        bad.append("claims food, but the student got none")
    return bad + check_facts(text, c)


def prompt(c: dict) -> str:
    p = c["persona"]
    lines = [f"{e['clock']}  {e['text']}" for e in c["events"]]
    who = p["label"] + (f". {p['description']}" if p["description"] else "")
    got = ", ".join(c["got"]) if c["got"] else "NOTHING (left without any food)"
    first = c["first_choice"] or "none"
    outcome = f"Food they got: {got}\nFirst choice: {first} ({'got it' if c['got_first_choice'] else 'did not get it'})"
    return f"Student: {who}\nDiet: {p['diet']}\n{outcome}\n\nRecorded events:\n" + "\n".join(lines) + \
        "\n\nNarrate this dinner in the student's voice. Stay consistent with the food they got."


_END = re.compile(r"[.!?](\s+)")


class CheckFailed(LLMError):
    """The narration named something the student never saw."""


# ---------------------------------------------------------------- narrator


class Narrator:
    """`wait_when_busy`: batch jobs (the bundle) wait for the load limit to clear instead of using the template.
    `log`: progress lines for batch jobs (e.g. "waiting for the LLM rate limit"); None stays quiet."""

    def __init__(self, svc: Service, llm: LLM, wait_when_busy: bool = False, max_wait_s: float = 180,
                 log: Callable[[str], None] | None = None):
        self.svc, self.llm = svc, llm
        self.wait_when_busy, self.max_wait_s = wait_when_busy, max_wait_s
        self.log = log

    def load(self, h: str, seed: int, student_id: int) -> tuple[ScenarioConfig, dict]:
        cfg = self.svc.store.get_config(h)
        if cfg is None:
            raise KeyError(f"unknown config {h}")
        trace = TRACES.get(self.svc, h, seed)
        if trace is None or student_id not in trace["_by_id"]:
            raise KeyError(f"student {student_id} not in night {seed}")
        return cfg, card(cfg, trace["_by_id"][student_id])

    def key(self, h: str, seed: int, student_id: int) -> str:
        return self.llm.key("narration", PROMPT_VERSION, {"config_hash": h, "seed": seed, "student": student_id})

    async def narrate(self, h: str, seed: int, student_id: int) -> AsyncIterator[tuple[str, dict]]:
        """Yields ('card', card), ('sentence', {text}) ..., ('final', {text, source, ...})."""
        cfg, c = await asyncio.to_thread(self.load, h, seed, student_id)
        yield "card", c
        base = {"prompt_version": PROMPT_VERSION, **self.llm.info()}
        k = self.key(h, seed, student_id)
        hit = self.llm.cached(k)
        if hit and hit.get("source") == "llm" and check(hit["text"], c, cfg):
            hit = None  # cached under an older, looser check: ask again
        if hit:
            yield "sentence", {"text": hit["text"]}
            yield "final", base | hit | {"source": "cache"}
            return

        fallback = template(c)
        if self.llm.is_mock:
            yield "sentence", {"text": fallback}
            yield "final", base | {"text": fallback, "source": "template", "reason": "mock provider"}
            return

        waited = 0.0
        while self.wait_when_busy and self.llm.busy() and waited < self.max_wait_s:
            await asyncio.sleep(1)
            waited += 1
            if self.log and waited % 15 == 0:
                self.log(f"    waiting for the LLM load limit ({waited:.0f}s of {self.max_wait_s:.0f}s max)")
        if self.llm.busy():  # free-tier limits: answer now instead of queueing behind other clicks
            yield "sentence", {"text": fallback}
            yield "final", base | {"text": fallback, "source": "template", "reason": "LLM busy (load limit)"}
            return

        sent, buf, reason, cache_it = [], "", None, True
        deadline = time.monotonic() + self.llm.timeout_s  # the HTTP timeout catches a stall; this caps a trickle
        try:
            # aclosing: when we stop early (deadline, failed check) the stream's HTTP client closes on this loop
            async with aclosing(self.llm.stream_text(system=SYSTEM, user=prompt(c))) as stream:
                async for delta in stream:
                    if time.monotonic() > deadline:
                        raise LLMError(f"narration took longer than {self.llm.timeout_s:.0f}s")
                    buf += delta
                    while m := _END.search(buf):  # a sentence is done once whitespace follows its period
                        s, buf = buf[: m.start(1)].strip(), buf[m.end():]
                        self._check(s, c, cfg)
                        sent.append(s)
                        yield "sentence", {"text": s}
            if buf.strip():
                self._check(buf, c, cfg)
                sent.append(buf.strip())
                yield "sentence", {"text": buf.strip()}
            text = " ".join(sent)
            if not text or len(text) > MAX_CHARS:
                raise CheckFailed(f"length {len(text)} outside 1..{MAX_CHARS}")
        except CheckFailed as e:
            reason = str(e)
        except LLMError as e:  # network / refusal: don't cache, the next click tries again
            reason, cache_it = str(e), False
        if reason is None:
            out = {"text": text, "source": "llm", "replaced": False}
        else:  # anything already shown is replaced by the template
            out = {"text": fallback, "source": "template", "replaced": bool(sent), "reason": reason}
        if cache_it:
            self.llm.remember(k, "narration", PROMPT_VERSION, out)
        yield "final", base | out

    @staticmethod
    def _check(s: str, c: dict, cfg: ScenarioConfig) -> None:
        if bad := check(s, c, cfg):
            raise CheckFailed(f"mentioned {bad}, not in the student's events")

    def narrate_sync(self, h: str, seed: int, student_id: int) -> dict:
        async def run():
            final = None
            async for kind, data in self.narrate(h, seed, student_id):
                if kind == "final":
                    final = data
            return final

        return asyncio.run(run())
