"""Counter-based random numbers for common random numbers (CRN).

Every random quantity that should be identical across scenarios is a pure
function of (seed, stream, keys...). For example, a student's taste for a dish
is `uniform(seed, TASTE, student_id, dish_key)`, so adding a dish or changing
a batch size never reshuffles anyone else's draws.
"""

from __future__ import annotations

import hashlib
from enum import IntEnum
from functools import lru_cache

import numpy as np

_MASK = (1 << 64) - 1
_GOLDEN = np.uint64(0x9E3779B97F4A7C15)
_M1 = np.uint64(0xBF58476D1CE4E5B9)
_M2 = np.uint64(0x94D049BB133111EB)
_S30, _S27, _S31, _S11 = (np.uint64(s) for s in (30, 27, 31, 11))


class Stream(IntEnum):
    ARRIVAL = 1
    PERSONA = 2
    PATIENCE = 3
    PLATE = 4
    SECONDS = 5
    DWELL = 6
    TASTE = 7
    SERVICE = 8


@lru_cache(maxsize=4096)
def stable_key(s: str) -> int:
    """Stable 64-bit key for a string id (Python's hash() is salted per process)."""
    return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "little")


def _mix(x: np.ndarray | np.uint64) -> np.ndarray | np.uint64:
    """splitmix64 finalizer, vectorized; uint64 overflow wraps by design."""
    z = x + _GOLDEN
    z = (z ^ (z >> _S30)) * _M1
    z = (z ^ (z >> _S27)) * _M2
    return z ^ (z >> _S31)


def _as_u64(k: object) -> np.ndarray | np.uint64:
    if isinstance(k, str):
        return np.uint64(stable_key(k))
    if isinstance(k, (int, np.integer)):
        return np.uint64(int(k) & _MASK)
    return np.asarray(k).astype(np.uint64)


def hash_u64(*keys: object) -> np.ndarray | np.uint64:
    with np.errstate(over="ignore"):
        h = np.uint64(0)
        for k in keys:
            h = _mix(h ^ _as_u64(k))
        return h


def uniform(*keys: object) -> np.ndarray:
    """Uniform(0, 1), never exactly 0 or 1. Broadcasts over array keys."""
    return ((hash_u64(*keys) >> _S11).astype(np.float64) + 0.5) * 2.0**-53


def std_normal(*keys: object) -> np.ndarray:
    """Standard normal via Box-Muller from two keyed uniforms."""
    u1 = uniform(*keys, 1)
    u2 = uniform(*keys, 2)
    return np.sqrt(-2.0 * np.log(u1)) * np.cos(2.0 * np.pi * u2)
