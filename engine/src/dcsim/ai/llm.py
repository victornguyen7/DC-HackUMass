"""One client for every LLM call (ENGINE.md §7), with a response cache and a mock.

The LLM never runs inside the simulation loop. It prepares inputs before a run
(personas, new-dish affinities, plain-English -> levers) and explains after one
(click narration, why a scenario won). Every job here has a deterministic
fallback, so the app works with no key and no network.

    LLM_PROVIDER=gemini (default when GEMINI_API_KEY is set) Google Gemini, JSON-schema output
    LLM_PROVIDER=groq   (default when only GROQ_API_KEY is set) Groq chat completions, JSON-schema output
    LLM_PROVIDER=mock   (default otherwise; tests, CI) fixed personas, template narration, keyword parser
    DCSIM_LLM_MODEL     model id (default per provider: DEFAULT_MODELS)
    DCSIM_LLM_FALLBACK_MODELS  comma list tried in order when the model is busy (429/5xx/timeout); Gemini only
    DCSIM_LLM_MODEL_STRONG     model for one-off jobs that can wait a few seconds (dish prior); default per provider
    DCSIM_LLM_MAX_LIVE / DCSIM_LLM_RPM  live narration load limits; over them a click gets the template at once
    DCSIM_EMBED_MODEL   embedding model (Gemini only; default gemini-embedding-001)
    DCSIM_LLM_MAX_OUTPUT  output-token cap per call (Groq default 1000 = free tier's tokens-per-minute limit,
                          a request asking for more is rejected outright; Gemini default 8000)

Settings can live in engine/.env (never committed); real environment variables win.
Responses are cached in the `llm_cache` table keyed by
sha256(kind, prompt_version, model, inputs): the same request never hits the
network twice, and a demo replays the same words.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import deque
from contextlib import contextmanager
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from ..store import Store

# gemini-3.5-flash-lite: fast and available on the free tier; gemini-3.8-flash often returns 503
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite", "groq": "qwen/qwen3.8-27b"}
DEFAULT_MODEL = DEFAULT_MODELS["gemini"]
FALLBACK_MODELS = {"gemini": "gemini-3.1-flash-lite", "groq": ""}
STRONG_MODELS = {"gemini": "gemini-4-hydrogen", "groq": ""}  # "" = same as the main model
EMBED_MODEL = "gemini-embedding-001"
EMBED_VERSION = "embed-v1"
BUSY_CODES = {408, 429, 500, 502, 503, 504}
Provider = Literal["mock", "groq", "gemini"]
PROVIDERS = ("mock", "groq", "gemini")
GEMINI_RETRIES = 3  # a click should not hang on an overloaded model; the fallback answers
THINKING_HEADROOM = 4000  # Gemini counts thinking tokens against max_output_tokens
THINKING_HEADROOM_LOW = 1500  # even thinking_level LOW spends some
T = TypeVar("T", bound=BaseModel)
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"  # engine/.env
RETRIES = 6  # the SDK waits out 429s (retry-after) between attempts


def load_env_file(path: Path = ENV_FILE) -> None:
    """KEY=VALUE lines -> os.environ, without overriding variables that are already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


class LLMError(RuntimeError):
    """The model call failed or returned unusable output. Callers fall back."""


@dataclass
class Reply(Generic[T]):
    value: T
    source: Literal["cache", "llm", "mock", "fallback"]  # fallback = the call failed, `mock()` answered
    model: str
    problems: list[str] = field(default_factory=list)  # left over after one re-ask
    error: str | None = None
    fallback_model: bool = False  # answered by a backup model because the main one was busy


def provider_from_env() -> Provider:
    load_env_file()
    default = ("gemini" if os.environ.get("GEMINI_API_KEY")
               else "groq" if os.environ.get("GROQ_API_KEY") else "mock")
    p = os.environ.get("LLM_PROVIDER", default).strip().lower()
    if p not in PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of {', '.join(PROVIDERS)}, not {p!r}")
    return p  # type: ignore[return-value]


def request_hash(kind: str, prompt_version: str, model: str, payload: dict) -> str:
    body = json.dumps({"kind": kind, "v": prompt_version, "model": model, "in": payload},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def _cap(max_tokens: int, provider: str = "groq") -> int:
    default = "8000" if provider == "gemini" else "1000"
    return min(max_tokens, int(os.environ.get("DCSIM_LLM_MAX_OUTPUT", default)))


def _reasoning(effort: str) -> str:
    """Qwen on Groq thinks ("default") or not ("none"); low-effort jobs skip it for speed."""
    return "none" if effort == "low" else "default"


class LLM:
    def __init__(self, store: Store | None = None, provider: Provider | None = None, model: str | None = None,
                 client=None, async_client=None):
        self.provider: Provider = provider or provider_from_env()
        self.model = ("mock" if self.provider == "mock"
                      else model or os.environ.get("DCSIM_LLM_MODEL") or DEFAULT_MODELS[self.provider])
        self.store = store
        self._client, self._aclient = client, async_client
        env_list = os.environ.get("DCSIM_LLM_FALLBACK_MODELS")
        fb = FALLBACK_MODELS.get(self.provider, "") if env_list is None else env_list
        self.fallbacks = [m.strip() for m in fb.split(",") if m.strip() and m.strip() != self.model] \
            if self.provider == "gemini" else []
        self.strong_model = (os.environ.get("DCSIM_LLM_MODEL_STRONG") or STRONG_MODELS.get(self.provider, "")
                             or self.model) if not self.is_mock else "mock"
        self.embed_model = os.environ.get("DCSIM_EMBED_MODEL", EMBED_MODEL)
        self.max_live = int(os.environ.get("DCSIM_LLM_MAX_LIVE", "4"))
        self.rpm = int(os.environ.get("DCSIM_LLM_RPM", "15"))
        self.live = 0
        self._calls: deque[float] = deque()
        self.last_model = self.model
        self._aclient_owned, self._aclient_loop = False, None  # injected clients are never replaced
        self._client_injected = client is not None

    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"

    def info(self) -> dict:
        return {"provider": self.provider, "model": self.model}

    def status(self) -> dict:
        """Everything the app and docs need to know about the live LLM setup."""
        return self.info() | {
            "fallback_models": self.fallbacks, "strong_model": self.strong_model,
            "embed_model": None if self.provider != "gemini" else self.embed_model,
            "live_in_flight": self.live, "max_live": self.max_live,
            "calls_last_minute": self._recent(), "rpm_limit": self.rpm, "busy": self.busy(),
        }

    # ------------------------------------------------------------ load limits (free tier)

    def _recent(self) -> int:
        cut = time.monotonic() - 60
        while self._calls and self._calls[0] < cut:
            self._calls.popleft()
        return len(self._calls)

    def _count_call(self) -> None:
        self._calls.append(time.monotonic())

    def busy(self) -> bool:
        """True when a new live call would likely be slow or rate-limited: serve the template instead."""
        return self.live >= self.max_live or self._recent() >= self.rpm

    @contextmanager
    def _live_slot(self):
        self.live += 1
        try:
            yield
        finally:
            self.live -= 1

    # ------------------------------------------------------------ cache

    def key(self, kind: str, prompt_version: str, payload: dict) -> str:
        return request_hash(kind, prompt_version, self.model, payload)

    def cached(self, h: str) -> dict | None:
        return self.store.get_llm(h) if self.store and not self.is_mock else None

    def remember(self, h: str, kind: str, prompt_version: str, response: dict) -> None:
        if self.store and not self.is_mock:  # mock output is cheap and must never shadow a real response
            self.store.put_llm(h, kind, self.model, prompt_version, response)

    # ------------------------------------------------------------ clients

    def _sync(self):
        if self._client is None and self.provider == "gemini":
            self._client = self._gemini()
        if self._client is None:
            import groq

            self._client = groq.Groq(max_retries=RETRIES)
        return self._client

    def _gemini(self):
        from google import genai
        from google.genai import types

        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY is not set")
        # retries 408/429/5xx only; 402 (no credits) and 403 fail fast so the fallback answers quickly
        return genai.Client(api_key=key, http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=GEMINI_RETRIES, http_status_codes=[408, 429, 500, 502, 503, 504])))

    def _async(self):
        # An async client's pooled connections belong to the event loop that opened them.
        # Sync callers (bundle -> narrate_sync) start a fresh loop per call, so a client we
        # created on an earlier loop must be replaced, or closing it raises "Event loop is closed".
        import asyncio

        if self.provider == "gemini" and self._client_injected:
            return self._client.aio  # injected client (tests): use it as given
        loop = asyncio.get_running_loop()
        if self._aclient is None or (self._aclient_owned and self._aclient_loop is not loop):
            if self.provider == "gemini":
                self._aclient = self._gemini().aio  # own client: the sync one keeps its connections
            else:
                import groq

                self._aclient = groq.AsyncGroq(max_retries=RETRIES)
            self._aclient_owned, self._aclient_loop = True, loop
        return self._aclient

    # ------------------------------------------------------------ structured output

    def structured(
        self, kind: str, prompt_version: str, *, system: str, user: str, schema: type[T],
        mock: Callable[[], T], check: Callable[[T], list[str]] | None = None,
        effort: str = "medium", max_tokens: int = 16000, cache_inputs: dict | None = None,
        fallback: bool = True, model: str | None = None,
    ) -> Reply[T]:
        """JSON validated against `schema`. `check` returns problems; one re-ask, then the caller decides.

        `cache_inputs` is what identifies the request (default: the prompts themselves).
        `fallback`: on a failed call answer with `mock()` (source "fallback"); False raises LLMError.
        `model`: use this model instead of the main one (e.g. `self.strong_model`); it is part of the cache key.
        """
        if self.is_mock:
            value = mock()
            return Reply(value, "mock", self.model, check(value) if check else [])
        m = model or self.model
        h = request_hash(kind, prompt_version, m,
                         cache_inputs or {"system": system, "user": user, "schema": schema.__name__})
        hit = self.cached(h)
        if hit is not None:
            value = schema.model_validate(hit["value"])
            return Reply(value, "cache", hit.get("model", m), hit.get("problems", []))

        messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            value, raw = self._parse(messages, schema, effort, max_tokens, m)
            problems = check(value) if check else []
            if problems:  # re-ask once with the exact problems
                messages += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": "Your answer has these problems:\n- " + "\n- ".join(problems)
                     + "\nReturn the complete corrected JSON."},
                ]
                value, _ = self._parse(messages, schema, effort, max_tokens, m)
                problems = check(value) if check else []
        except LLMError as e:
            if not fallback:
                raise
            value = mock()  # not cached: the next request tries the model again
            return Reply(value, "fallback", m, check(value) if check else [], error=str(e))
        used = self.last_model
        self.remember(h, kind, prompt_version,
                      {"value": value.model_dump(mode="json"), "problems": problems, "model": used})
        return Reply(value, "llm", used, problems, fallback_model=used != m)

    def _parse(self, messages: list[dict], schema: type[T], effort: str, max_tokens: int,
               model: str | None = None) -> tuple[T, str]:
        self._count_call()
        if self.provider == "gemini":
            return self._parse_gemini(messages, schema, effort, max_tokens, model or self.model)
        self.last_model = self.model
        import groq

        try:
            resp = self._sync().chat.completions.create(
                model=self.model, messages=messages, temperature=0, max_completion_tokens=_cap(max_tokens),
                response_format={"type": "json_schema",
                                 "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()}},
                reasoning_effort=_reasoning(effort), reasoning_format="hidden",
            )
        except groq.GroqError as e:  # API errors, and a missing key at client creation
            raise LLMError(f"{type(e).__name__}: {e}") from e
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise LLMError("hit max_completion_tokens")
        raw = choice.message.content or ""
        try:
            return schema.model_validate_json(raw), raw
        except ValidationError as e:
            raise LLMError(f"response did not match {schema.__name__}: {e.error_count()} errors") from e

    # ------------------------------------------------------------ Gemini

    def _chain(self, model: str) -> list[str]:
        """The model to try first, then the backups (only for the main model; an explicit model has none)."""
        return [model] + (self.fallbacks if model == self.model else [])

    @staticmethod
    def _gemini_busy(e: Exception) -> bool:
        from google.genai import errors

        if isinstance(e, errors.APIError):
            return e.code in BUSY_CODES
        return "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower()

    def _gemini_config(self, system: str, effort: str, max_tokens: int, model: str | None = None, **extra):
        from google.genai import types

        model = model or self.model
        thinking = None
        if model.startswith("gemini-2"):  # 2.x: token budget; 0 turns thinking off (flash models)
            thinking = types.ThinkingConfig(thinking_budget=0 if effort == "low" and "flash" in model else -1)
        elif model.startswith("gemini-"):  # 3.x+: thinking level
            thinking = types.ThinkingConfig(thinking_level="LOW") if effort == "low" else None  # MINIMAL: not on 3.8
        headroom = THINKING_HEADROOM_LOW if effort == "low" else THINKING_HEADROOM
        return types.GenerateContentConfig(system_instruction=system, thinking_config=thinking,
                                           max_output_tokens=_cap(max_tokens + headroom, "gemini"), **extra)

    @staticmethod
    def _gemini_contents(messages: list[dict]) -> tuple[str, list[dict]]:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                    for m in messages if m["role"] != "system"]
        return system, contents

    def _parse_gemini(self, messages: list[dict], schema: type[T], effort: str, max_tokens: int,
                      model: str) -> tuple[T, str]:
        system, contents = self._gemini_contents(messages)
        chain, last = self._chain(model), None
        for m in chain:
            try:
                resp = self._sync().models.generate_content(
                    model=m, contents=contents,
                    config=self._gemini_config(system, effort, max_tokens, m, temperature=0,
                                               response_mime_type="application/json",
                                               response_json_schema=schema.model_json_schema()))
                self.last_model = m
                break
            except LLMError:
                raise
            except Exception as e:  # noqa: BLE001 - APIError, timeouts; anything else is re-raised below
                last = e
                if not (self._gemini_busy(e) and m != chain[-1]):
                    raise self._gemini_error(e) from e
        else:  # pragma: no cover - the loop always breaks or raises
            raise self._gemini_error(last)
        cand = (resp.candidates or [None])[0]
        finish = str(getattr(cand, "finish_reason", "") or "")
        if finish.endswith("MAX_TOKENS"):
            raise LLMError("hit max_output_tokens")
        raw = resp.text or ""
        if not raw:
            raise LLMError(f"empty response (finish_reason={finish or 'none'})")
        try:
            return schema.model_validate_json(raw), raw
        except ValidationError as e:
            raise LLMError(f"response did not match {schema.__name__}: {e.error_count()} errors") from e

    @staticmethod
    def _gemini_error(e: Exception) -> LLMError:
        from google.genai import errors

        if isinstance(e, errors.APIError):
            return LLMError(f"Gemini {e.code} {e.status}: {(e.message or '')[:200]}")
        if isinstance(e, LLMError):
            return e
        return LLMError(f"{type(e).__name__}: {str(e)[:200]}")

    async def _stream_gemini(self, system: str, user: str, effort: str, max_tokens: int) -> AsyncIterator[str]:
        # An injected client (tests) is used as is. Otherwise each stream gets its own async client, closed in
        # this event loop: a shared one would try to close itself later on a loop that asyncio.run already closed.
        own = None if self._client is not None else self._gemini().aio
        try:
            async for t in self._stream_gemini_on(own or self._async(), system, user, effort, max_tokens):
                yield t
        finally:
            if own is not None:
                await own.aclose()

    async def _stream_gemini_on(self, aio, system: str, user: str, effort: str, max_tokens: int) -> AsyncIterator[str]:
        chain = self._chain(self.model)
        for m in chain:
            finish, started = "", False
            try:
                self._count_call()
                stream = await aio.models.generate_content_stream(
                    model=m, contents=[{"role": "user", "parts": [{"text": user}]}],
                    config=self._gemini_config(system, effort, max_tokens, m, temperature=0.3))
                async for chunk in stream:
                    for c in chunk.candidates or []:
                        finish = str(c.finish_reason or "") or finish
                    if chunk.text:
                        started = True
                        self.last_model = m
                        yield chunk.text
            except LLMError:
                raise
            except Exception as e:  # noqa: BLE001
                if started or not (self._gemini_busy(e) and m != chain[-1]):  # can't switch models mid-sentence
                    raise self._gemini_error(e) from e
                continue
            if finish.endswith("MAX_TOKENS"):
                raise LLMError("hit max_output_tokens")
            return

    # ------------------------------------------------------------ embeddings (Gemini)

    def embed(self, texts: list[str], dim: int = 256, task: str = "SEMANTIC_SIMILARITY") -> list[list[float]]:
        """Vectors for `texts`, each cached in llm_cache (kind "embed"). Raises LLMError without Gemini."""
        if self.provider != "gemini":
            raise LLMError(f"embeddings need the gemini provider (now {self.provider})")
        from google.genai import types

        keys = [request_hash("embed", EMBED_VERSION, self.embed_model, {"text": t, "dim": dim, "task": task})
                for t in texts]
        out: dict[int, list[float]] = {}
        for i, k in enumerate(keys):
            if (hit := self.cached(k)) is not None:
                out[i] = hit["v"]
        todo = [i for i in range(len(texts)) if i not in out]
        for start in range(0, len(todo), 100):  # the API takes at most 100 texts per call
            part = todo[start:start + 100]
            try:
                self._count_call()
                r = self._sync().models.embed_content(
                    model=self.embed_model, contents=[texts[i] for i in part],
                    config=types.EmbedContentConfig(output_dimensionality=dim, task_type=task))
            except Exception as e:  # noqa: BLE001
                raise self._gemini_error(e) from e
            if len(r.embeddings or []) != len(part):
                raise LLMError(f"{self.embed_model} returned {len(r.embeddings or [])} vectors for {len(part)} texts")
            for i, emb in zip(part, r.embeddings):
                out[i] = [round(x, 6) for x in emb.values]
                if self.store:
                    self.store.put_llm(keys[i], "embed", self.embed_model, EMBED_VERSION, {"v": out[i]})
        return [out[i] for i in range(len(texts))]

    # ------------------------------------------------------------ streamed text

    async def stream_text(self, *, system: str, user: str, effort: str = "low", max_tokens: int = 1000) -> AsyncIterator[str]:
        """Text deltas from the model. Raises LLMError on failure (callers fall back)."""
        import groq

        if self.is_mock:
            raise LLMError("mock provider has no free text; use the template")
        if self.provider == "gemini":
            with self._live_slot():
                async for t in self._stream_gemini(system, user, effort, max_tokens):
                    yield t
            return
        self._count_call()
        finish = None
        try:
            stream = await self._async().chat.completions.create(
                model=self.model, stream=True, temperature=0.3, max_completion_tokens=_cap(max_tokens),
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                reasoning_effort=_reasoning(effort), reasoning_format="hidden",
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                c = chunk.choices[0]
                finish = c.finish_reason or finish
                if c.delta.content:
                    yield c.delta.content
        except groq.GroqError as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e
        if finish == "length":
            raise LLMError("hit max_completion_tokens")


__all__ = ["LLM", "LLMError", "Reply", "provider_from_env", "request_hash", "DEFAULT_MODEL", "DEFAULT_MODELS", "load_env_file", "EMBED_VERSION"]
