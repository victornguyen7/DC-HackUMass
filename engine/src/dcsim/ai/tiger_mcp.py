"""Sam's line to Tiger Data, through Tiger's own MCP server (`tiger mcp start`, stdio).

The engine is the MCP client. Only two of the server's tools are ever called:
    db_schema  (read-only by the server)
    db_query   (the server would run ANY SQL as tsdbadmin on a DEV service, TIGER.md §6)
so every query Sam writes passes `guard_sql` first: one SELECT/WITH statement, no write or
session keywords, run inside `BEGIN READ ONLY` with a statement timeout. The service_* tools
(delete, resize, password) are never called.

    DCSIM_TIGER_MCP=0          turn the Tiger tools off
    TIGER_SERVICE_ID           which service (engine/.env); the CLI login does the rest
    DCSIM_TIGER_CMD            the CLI (default: `tiger` on PATH)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
from contextlib import AsyncExitStack

from .llm import load_env_file

MAX_ROWS = 40  # rows Sam sees per query (the database still aggregates everything)
TIMEOUT_S = 20
_WRITE = re.compile(r"\b(insert|update|delete|merge|upsert|drop|alter|create|truncate|grant|revoke|copy|comment|"
                    r"vacuum|analyze|cluster|reindex|refresh|call|do|execute|prepare|lock|listen|notify|set|reset|"
                    r"begin|commit|rollback|savepoint|release|start|abort|discard|security|pg_sleep|dblink|"
                    r"pg_terminate_backend|pg_cancel_backend|lo_import|lo_export|pg_read_file|pg_write_file|"
                    r"set_config|nextval|setval)\b", re.I)


class TigerError(RuntimeError):
    pass


def available() -> bool:
    load_env_file()
    return (os.environ.get("DCSIM_TIGER_MCP", "1") != "0" and bool(os.environ.get("TIGER_SERVICE_ID"))
            and shutil.which(os.environ.get("DCSIM_TIGER_CMD", "tiger")) is not None)


def _strip(sql: str) -> str:
    """SQL without comments and string/identifier literals (so keywords inside them don't count)."""
    sql = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"\$([A-Za-z_]*)\$.*?\$\1\$", "''", sql, flags=re.S)
    return re.sub(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", "''", sql)


def guard_sql(sql: str) -> str:
    """Return the single read-only statement, or raise TigerError saying why not."""
    s = sql.strip().rstrip(";").strip()
    bare = _strip(s)
    if not s:
        raise TigerError("empty query")
    if ";" in bare:
        raise TigerError("one statement only (no ';')")
    if not re.match(r"^\s*(select|with)\b", bare, re.I):
        raise TigerError("only SELECT (or WITH ... SELECT) queries")
    if m := _WRITE.search(bare):
        raise TigerError(f"read-only: {m.group(1).upper()} is not allowed")
    if re.search(r"\binto\b", bare, re.I):
        raise TigerError("read-only: SELECT ... INTO is not allowed")
    return s


class TigerMCP:
    """A live MCP session with `tiger mcp start`, on its own event loop thread (callers are sync)."""

    def __init__(self, service_id: str | None = None, cmd: str | None = None):
        load_env_file()
        self.service_id = service_id or os.environ.get("TIGER_SERVICE_ID", "")
        self.cmd = cmd or os.environ.get("DCSIM_TIGER_CMD", "tiger")
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="tiger-mcp")
        self._thread.start()
        self._stack: AsyncExitStack | None = None
        self._session = None
        self.calls = 0

    def _run(self, coro, timeout: float = TIMEOUT_S + 30):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _open(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        params = StdioServerParameters(command=self.cmd, args=["mcp", "start", "stdio", "--analytics=false"],
                                       env=dict(os.environ))
        read, write = await self._stack.enter_async_context(stdio_client(params, errlog=open(os.devnull, "w")))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()

    async def _call(self, name: str, args: dict) -> dict:
        if self._session is None:
            await self._open()
        r = await self._session.call_tool(name, args, read_timeout_seconds=TIMEOUT_S + 10)
        text = "".join(getattr(c, "text", "") for c in (getattr(r, "content", None) or []))
        if getattr(r, "isError", False) or getattr(r, "is_error", False):
            raise TigerError(text[:400] or f"{name} failed")
        data = getattr(r, "structuredContent", None) or getattr(r, "structured_content", None)
        if data is None:
            try:
                data = json.loads(text)
            except ValueError:
                data = {"text": text}
        return data

    def call(self, name: str, args: dict) -> dict:
        if name not in ("db_schema", "db_query"):  # never the service_* tools
            raise TigerError(f"tool {name!r} is not allowed")
        if not self.service_id:
            raise TigerError("TIGER_SERVICE_ID is not set (engine/.env)")
        self.calls += 1
        try:
            return self._run(self._call(name, {"service_id": self.service_id} | args))
        except TigerError:
            raise
        except Exception as e:  # noqa: BLE001 - process, protocol and timeout errors
            raise TigerError(f"{type(e).__name__}: {str(e)[:300]}") from e

    # ------------------------------------------------------------ what Sam uses

    def schema(self) -> str:
        d = self.call("db_schema", {"schema": "dcsim", "comments": True})
        return str(d.get("schema") or d.get("text") or d)

    def query(self, sql: str) -> dict:
        """Run one guarded read-only query; {columns, rows (≤ MAX_ROWS), row_count, truncated}."""
        s = guard_sql(sql)
        wrapped = (f"BEGIN TRANSACTION READ ONLY; SET LOCAL statement_timeout = '{TIMEOUT_S}s'; "
                   f"{s};\nCOMMIT;")
        d = self.call("db_query", {"query": wrapped, "timeout_seconds": TIMEOUT_S})
        sets = [rs for rs in (d.get("result_sets") or []) if rs.get("columns")]
        if not sets:
            raise TigerError(d.get("text") or "the query returned no result set")
        rs = sets[-1]
        rows = rs.get("rows") or []
        return {"columns": [c["name"] for c in rs["columns"]], "rows": rows[:MAX_ROWS],
                "row_count": rs.get("rows_affected", len(rows)),
                "truncated": len(rows) > MAX_ROWS or bool(rs.get("truncated"))}

    def close(self) -> None:
        async def _close():
            if self._stack is not None:
                await self._stack.aclose()
        try:
            self._run(_close(), timeout=10)
        except Exception:  # noqa: BLE001 - closing a dead server
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)


SCHEMA_DIGEST = """Tiger Data (TimescaleDB), schema dcsim, Worcester Commons history. Observed rows are SYNTHETIC history.
- nights(ts, night date, source 'observed'|'simulated', scenario, run_id, weekday, weather 'clear|rain|cold|snow',
  event_night bool, split 'train|test|live', students, cooked_portions, waste_portions, waste_lb, stockout_min,
  missed_meal, wait_p90_min, dinner_stockout_min, late_night_stockout_min, ...). For recorded history use
  source = 'observed'. weekday is text like 'Friday'.
- pan_windows(ts, night, source, scenario, station, dish_id, dish, portions_out, portions_eaten, portions_left, period)
  per dish per 30-min window. A dish ran out in a window when portions_left = 0, EXCEPT its last window of the
  night (close-out, pans pulled).
- waste_log(ts, night, station, dish_id, dish, portions, weight_lb, reason 'expired|close|plate', period): weigh-ins.
- dish_nights(night, source, scenario, seed, dish_id, station, period, to_order, cooked, served, waste, stockout_min,
  runout_min): SIMULATED only (source = 'simulated'); for what really happened use pan_windows / waste_log.
  scenario 'sweep' = hundreds of simulated nights (one row per seed) of one menu date's plan, night = that menu date:
  "how long is <dish> out of stock across 500 nights" = avg(stockout_min) over scenario = 'sweep' for that night,
  joined to dishes ON d.menu_date = x.night. runout_min = minute of the day it first ran out (1080 = 6:00 pm).
- dishes(menu_date, dish_id, name, station, period, role, diet_tags text[], popularity, batch_cooked): one menu per
  menu_date. Past nights reuse a menu: join dishes ON d.dish_id = x.dish_id AND d.menu_date = nights.menu_date
  (NOT the night's own date). Diet test: 'vegetarian' = ANY(d.diet_tags).
- forecast_results(night, origin, method 'naive|calibrated', students, waste_p10/p50/p90, waste_inside, split, weather)
- views: waste_daily_by_dish, pan_hourly, dish_nightly, forecast_weekly, forecast_scorecard, v_nights
Times are timestamptz; use AT TIME ZONE 'America/New_York' for clock times. Aggregate in SQL; keep results small."""


__all__ = ["TigerMCP", "TigerError", "guard_sql", "available", "SCHEMA_DIGEST", "MAX_ROWS"]
