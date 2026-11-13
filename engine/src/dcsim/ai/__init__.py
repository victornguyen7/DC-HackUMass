"""LLM layer (ENGINE.md §7). Outside the simulation loop: before a run (personas, new-dish
affinities, plain English -> levers) and after one (click narration, explanations).
Every job has a deterministic fallback; LLM_PROVIDER=mock (default) needs no key."""

from .llm import LLM, LLMError, provider_from_env

__all__ = ["LLM", "LLMError", "provider_from_env"]
