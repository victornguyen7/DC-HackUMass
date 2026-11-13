"""On-disk result store (ENGINE.md §5.2): one SQLite file plus gzipped trace files.

Results are kept per (config_hash, seed), so 30 -> 60 nights only runs seeds
31-60, and a baseline is computed once and reused by every comparison. The
hash already includes engine_version and data_version, so stale rows can never
match a current config; `prune()` deletes them.

Default location: <repo>/cache (override with DCSIM_CACHE).
"""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .config import ENGINE_VERSION, ScenarioConfig, config_hash
from .data import repo_root
from .metrics import NightMetrics

SCHEMA = """
CREATE TABLE IF NOT EXISTS configs (
  config_hash TEXT PRIMARY KEY, engine_version TEXT, data_version TEXT,
  date TEXT, label TEXT, config_json TEXT,
  created_at TEXT, last_hit_at TEXT, hits INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS nights (
  config_hash TEXT, seed INTEGER, metrics_json TEXT,
  PRIMARY KEY (config_hash, seed)
);
CREATE TABLE IF NOT EXISTS scenarios (
  scenario_id TEXT PRIMARY KEY, name TEXT, config_hash TEXT,
  status TEXT CHECK (status IN ('saved', 'adopted')), for_date TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS llm_cache (
  request_hash TEXT PRIMARY KEY,   -- sha256(kind + prompt_version + model + normalized inputs)
  kind TEXT, model TEXT, prompt_version TEXT, response_json TEXT, created_at TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def default_cache_dir() -> Path:
    env = os.environ.get("DCSIM_CACHE")
    return Path(env) if env else repo_root() / "cache"


class Store:
    def __init__(self, cache_dir: str | Path | None = None):
        self.dir = Path(cache_dir) if cache_dir else default_cache_dir()
        (self.dir / "traces").mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.dir / "results.sqlite", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.execute("PRAGMA journal_mode=WAL")

    # ------------------------------------------------------------ configs

    def put_config(self, cfg: ScenarioConfig) -> str:
        h = config_hash(cfg)
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO configs (config_hash, engine_version, data_version, date, label, config_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (h, cfg.engine_version, cfg.data_version, cfg.date, cfg.label, cfg.model_dump_json(), _now()),
            )
        return h

    def get_config(self, h: str) -> ScenarioConfig | None:
        with self._lock:
            row = self._db.execute("SELECT config_json FROM configs WHERE config_hash = ?", (h,)).fetchone()
        return ScenarioConfig.model_validate_json(row["config_json"]) if row else None

    def touch(self, h: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE configs SET hits = hits + 1, last_hit_at = ? WHERE config_hash = ?", (_now(), h))

    # ------------------------------------------------------------ nights

    def put_nights(self, h: str, nights: list[NightMetrics]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO nights (config_hash, seed, metrics_json) VALUES (?, ?, ?)",
                [(h, m.seed, json.dumps(m.to_dict(), separators=(",", ":"))) for m in nights],
            )

    def get_nights(self, h: str, seeds: list[int] | None = None) -> list[NightMetrics]:
        with self._lock:
            rows = self._db.execute(
                "SELECT seed, metrics_json FROM nights WHERE config_hash = ? ORDER BY seed", (h,)
            ).fetchall()
        wanted = set(seeds) if seeds is not None else None
        return [NightMetrics.from_dict(json.loads(r["metrics_json"])) for r in rows if wanted is None or r["seed"] in wanted]

    def missing_seeds(self, h: str, seeds: list[int]) -> list[int]:
        with self._lock:
            have = {r[0] for r in self._db.execute("SELECT seed FROM nights WHERE config_hash = ?", (h,))}
        return [s for s in seeds if s not in have]

    # ------------------------------------------------------------ traces

    def trace_path(self, h: str, seed: int) -> Path:
        return self.dir / "traces" / f"{h}_s{seed}.json.gz"

    def put_trace(self, h: str, seed: int, trace: dict) -> None:
        self.trace_path(h, seed).write_bytes(gzip.compress(json.dumps(trace, separators=(",", ":")).encode()))

    def get_trace_bytes(self, h: str, seed: int) -> bytes | None:
        p = self.trace_path(h, seed)
        return p.read_bytes() if p.exists() else None

    # ------------------------------------------------------------ scenarios (saved / adopted what-ifs)

    def save_scenario(self, name: str, h: str, status: str = "saved", for_date: str | None = None) -> dict:
        sid = uuid.uuid4().hex[:12]
        with self._lock, self._db:
            if status == "adopted":  # one adopted scenario per date
                self._db.execute("UPDATE scenarios SET status = 'saved' WHERE status = 'adopted' AND for_date = ?", (for_date,))
            self._db.execute(
                "INSERT INTO scenarios VALUES (?, ?, ?, ?, ?, ?)", (sid, name, h, status, for_date, _now())
            )
        return self.get_scenario(sid)

    def adopt_scenario(self, sid: str) -> dict | None:
        s = self.get_scenario(sid)
        if not s:
            return None
        with self._lock, self._db:
            self._db.execute("UPDATE scenarios SET status = 'saved' WHERE status = 'adopted' AND for_date = ?", (s["for_date"],))
            self._db.execute("UPDATE scenarios SET status = 'adopted' WHERE scenario_id = ?", (sid,))
        return self.get_scenario(sid)

    def get_scenario(self, sid: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM scenarios WHERE scenario_id = ?", (sid,)).fetchone()
        return dict(row) if row else None

    def list_scenarios(self, for_date: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM scenarios", ()
        if for_date:
            q, args = q + " WHERE for_date = ?", (for_date,)
        with self._lock:
            return [dict(r) for r in self._db.execute(q + " ORDER BY created_at DESC", args)]

    def delete_scenario(self, sid: str) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM scenarios WHERE scenario_id = ?", (sid,)).rowcount > 0

    # ------------------------------------------------------------ LLM responses (ENGINE.md §5.2, §7.5)

    def get_llm(self, request_hash: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT response_json FROM llm_cache WHERE request_hash = ?", (request_hash,)).fetchone()
        return json.loads(row["response_json"]) if row else None

    def put_llm(self, request_hash: str, kind: str, model: str, prompt_version: str, response: dict) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?, ?, ?, ?)",
                (request_hash, kind, model, prompt_version, json.dumps(response, separators=(",", ":")), _now()),
            )

    def prune_llm(self, current: dict[str, str], drop_kinds: tuple[str, ...] = ()) -> int:
        """Delete cached LLM responses whose prompt version is no longer current, plus every row of `drop_kinds`."""
        with self._lock, self._db:
            n = 0
            for kind in drop_kinds:
                n += self._db.execute("DELETE FROM llm_cache WHERE kind = ?", (kind,)).rowcount
            for kind, version in current.items():
                n += self._db.execute("DELETE FROM llm_cache WHERE kind = ? AND prompt_version != ?", (kind, version)).rowcount
        return n

    # ------------------------------------------------------------ housekeeping

    def prune(self) -> int:
        """Delete results from older engine versions (their hashes can never match again)."""
        with self._lock, self._db:
            old = [r[0] for r in self._db.execute("SELECT config_hash FROM configs WHERE engine_version != ?", (ENGINE_VERSION,))]
            for h in old:
                self._db.execute("DELETE FROM nights WHERE config_hash = ?", (h,))
                self._db.execute("DELETE FROM configs WHERE config_hash = ?", (h,))
        for h in old:
            for p in (self.dir / "traces").glob(f"{h}_s*.json.gz"):
                p.unlink()
        return len(old)

    def stats(self) -> dict:
        with self._lock:
            return {
                "configs": self._db.execute("SELECT COUNT(*) FROM configs").fetchone()[0],
                "nights": self._db.execute("SELECT COUNT(*) FROM nights").fetchone()[0],
                "scenarios": self._db.execute("SELECT COUNT(*) FROM scenarios").fetchone()[0],
                "llm_responses": self._db.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0],
                "path": str(self.dir),
            }


__all__ = ["Store", "default_cache_dir"]
