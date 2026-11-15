"""A temporary fleet of Vultr servers for plan searches (docs/SPONSORS.md §4, deploy/README.md "Fleet").

One command runs the optimizer on many menu dates at once, one Vultr server per date:

    dcsim fleet optimize --dates 2026-11-13..2026-11-20 --dry-run   # plan, workers, cost estimate; creates nothing
    dcsim fleet optimize --dates 2026-11-13..2026-11-20             # create, run, save to Tiger, delete
    dcsim fleet status | dcsim fleet cleanup                        # list / delete leftover fleet servers

Each worker is a fresh Ubuntu 24.04 server (a snapshot of the live server was tried first: restoring its
322 GB took over 10 minutes). cloud-init lets this laptop's public key log in as root; over SSH the
coordinator installs uv, clones the public repo (`main`), syncs the packages, sends the Tiger URL on stdin
(so no secret sits in Vultr's server metadata), runs `dcsim optimize --date D --save --source fleet:<run>`,
and takes the next date from the queue until none are left. Then the server is deleted.

Servers bill until deleted (stopped ones too), so every server a run creates is deleted in a `finally`:
on success, on error, on Ctrl-C and after `max_minutes`. `cleanup` deletes anything tagged `dcsim-fleet`.

Progress goes to Tiger (dcsim.fleet_runs, dcsim.fleet_tasks: no secrets, no IP addresses), which the
/tiger dashboard's Fleet card reads every 5 s. The Vultr key (VULTR_API_KEY) only ever lives in engine/.env.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait

import httpx

API = "https://api.vultr.com/v2"
TAG = "dcsim-fleet"
SNAPSHOT_DESC = "dcsim-worker"
REPO = "https://github.com/victornguyen7/dineamics"
OS_NAME = "Ubuntu 24.04 LTS x64"
LIVE_IP = "155.138.211.252"  # the live server (deploy/README.md); VULTR_LIVE_IP overrides
MAX_WORKERS = 8
APP_DIR = "/srv/dc-whatif"
EXPECTED_MIN = 12  # boot + one date, for the cost estimate

TABLES = """
-- Vultr fleet runs (fleet.py): one row per run and one per menu date in it. No secrets, no IP addresses.
CREATE TABLE IF NOT EXISTS dcsim.fleet_runs (
  run_id text PRIMARY KEY, kind text NOT NULL, dates date[], trials int,
  plan text, vcpus int, region text, workers int, hourly_usd double precision,
  servers int,                      -- servers actually created (the account's limits can allow fewer than `workers`)
  started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  status text NOT NULL,             -- running | done | failed | stopped
  wall_s double precision,          -- first server created -> last one deleted
  sequential_s double precision,    -- sum of every date's optimizer run: one machine, one date after another
  server_minutes double precision, est_cost_usd double precision, note text
);
CREATE TABLE IF NOT EXISTS dcsim.fleet_tasks (
  run_id text NOT NULL, menu_date date NOT NULL, worker int, instance_id text,
  status text NOT NULL,             -- queued | creating | booting | running | saved | failed
  created_at timestamptz, ready_at timestamptz, started_at timestamptz, finished_at timestamptz,
  deleted_at timestamptz, run_s double precision, search_id text, progress text, error text,
  PRIMARY KEY (run_id, menu_date)
);
ALTER TABLE dcsim.fleet_runs ADD COLUMN IF NOT EXISTS servers int;
COMMENT ON TABLE dcsim.fleet_runs IS 'Vultr fleet runs: many optimizer searches at once, one temporary Vultr server per menu date. wall_s = real time for the whole run; sequential_s = the same searches one after another on one machine.';
COMMENT ON TABLE dcsim.fleet_tasks IS 'One menu date in a Vultr fleet run: which worker ran it, when its server was created / ready / deleted, the optimizer run time and the saved search (plan_searches.search_id).';
"""

LATEST_SQL = """
SELECT r.*, (SELECT count(*) FROM dcsim.fleet_runs) AS runs
FROM dcsim.fleet_runs r ORDER BY started_at DESC LIMIT 1
"""
TASKS_SQL = "SELECT * FROM dcsim.fleet_tasks WHERE run_id = %(run_id)s ORDER BY menu_date"


class VultrError(RuntimeError):
    pass


# ---------------------------------------------------------------- Vultr API

class Vultr:
    """The few Vultr API v2 calls the fleet needs. `transport` is for tests (httpx.MockTransport)."""

    def __init__(self, key: str | None = None, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        if key is None:
            from .ai.llm import load_env_file

            load_env_file()
            key = os.environ.get("VULTR_API_KEY", "")
        if not key:
            raise VultrError("set VULTR_API_KEY in engine/.env (Vultr -> Account -> API)")
        self.http = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {key}"}, timeout=30,
                                 transport=transport)
        self.sleep = sleep

    def _req(self, method: str, path: str, **kw) -> dict:
        for attempt in range(5):
            r = self.http.request(method, path, **kw)
            if r.status_code in (429, 500, 502, 503) and attempt < 4:  # Vultr: ~30 requests/s, retry politely
                self.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 401 or r.status_code == 403:
                raise VultrError(f"Vultr refused the key ({r.status_code}). Is this machine's IP allowed under "
                                 "Account -> API -> Access Control?")
            if r.status_code >= 400:
                raise VultrError(f"Vultr {method} {path}: {r.status_code} {r.text[:300]}")
            return r.json() if r.content else {}
        raise VultrError(f"Vultr {method} {path}: still busy after retries")

    def _all(self, path: str, key: str, params: dict | None = None) -> list[dict]:
        out, cursor = [], None
        while True:
            q = dict(params or {}, per_page=500) | ({"cursor": cursor} if cursor else {})
            j = self._req("GET", path, params=q)
            out += j.get(key, [])
            cursor = (j.get("meta") or {}).get("links", {}).get("next")
            if not cursor:
                return out

    def instances(self, tag: str | None = None) -> list[dict]:
        return self._all("/instances", "instances", {"tag": tag} if tag else None)

    def instance(self, iid: str) -> dict:
        return self._req("GET", f"/instances/{iid}")["instance"]

    def create_instance(self, **body) -> dict:
        return self._req("POST", "/instances", json=body)["instance"]

    def delete_instance(self, iid: str) -> None:
        self._req("DELETE", f"/instances/{iid}")

    def os_list(self) -> list[dict]:
        return self._all("/os", "os")

    def delete_snapshot(self, sid: str) -> None:
        self._req("DELETE", f"/snapshots/{sid}")

    def snapshots(self) -> list[dict]:
        return self._all("/snapshots", "snapshots")

    def snapshot(self, sid: str) -> dict:
        return self._req("GET", f"/snapshots/{sid}")["snapshot"]

    def create_snapshot(self, instance_id: str, description: str) -> dict:
        return self._req("POST", "/snapshots", json={"instance_id": instance_id, "description": description})["snapshot"]

    def plans(self, kind: str = "voc") -> list[dict]:
        return self._all("/plans", "plans", {"type": kind})


def fleet_servers(api: Vultr) -> list[dict]:
    """Servers tagged dcsim-fleet (filtered again here: never trust a filter with deletes)."""
    return [i for i in api.instances(TAG) if TAG in (i.get("tags") or [])]


def delete_until_gone(api: Vultr, iid: str, sleep: Callable[[float], None] = time.sleep, timeout_s: float = 1800,
                      log: Callable[[str], None] = print) -> bool:
    """Delete a server, retrying while Vultr says it is locked (409: still being built or restored).
    Returns True once it is gone."""
    end, said = time.time() + timeout_s, False
    while True:
        try:
            api.delete_instance(iid)
            return True
        except VultrError as e:
            if "404" in str(e):
                return True
            if time.time() >= end:
                log(f"!! could not delete {iid} after {timeout_s / 60:.0f} min: {e} (run `dcsim fleet cleanup`)")
                return False
            if not said:
                log(f"server {iid} is locked by Vultr; retrying the delete until it goes through")
                said = True
            sleep(20)


def ubuntu_os_id(api: Vultr) -> int:
    for o in api.os_list():
        if o.get("name") == OS_NAME:
            return int(o["id"])
    raise VultrError(f"Vultr offers no {OS_NAME!r}")


def live_server(api: Vultr) -> dict:
    ip = os.environ.get("VULTR_LIVE_IP", LIVE_IP)
    for i in api.instances():
        if i.get("main_ip") == ip:
            return i
    raise VultrError(f"no Vultr server with IP {ip} (set VULTR_LIVE_IP)")


def pick_plan(plans: list[dict], region: str, vcpus: int, min_disk: int = 0) -> dict:
    """Cheapest CPU-optimized plan with at least `vcpus` vCPUs offered in `region` (and `min_disk` GB)."""
    ok = [p for p in plans if p.get("id", "").startswith("voc-c") and p["vcpu_count"] >= vcpus
          and region in p.get("locations", []) and p.get("disk", 0) >= min_disk]
    if not ok:
        raise VultrError(f"no CPU-optimized plan with {vcpus}+ vCPUs in {region}")
    return min(ok, key=lambda p: (p["hourly_cost"], p["vcpu_count"], p["id"]))


def parse_dates(spec: str) -> list[str]:
    """'2026-11-13..2026-11-20' or '2026-11-13,2026-11-15'."""
    if ".." in spec:
        a, b = (dt.date.fromisoformat(x.strip()) for x in spec.split(".."))
        return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]
    return [dt.date.fromisoformat(x.strip()).isoformat() for x in spec.split(",") if x.strip()]


# ---------------------------------------------------------------- SSH

def ssh_run(ip: str, command: str, on_line: Callable[[str], None] | None = None, timeout_s: float = 30,
            stop: threading.Event | None = None, stdin: bytes | None = None) -> tuple[int, str]:
    """Run `command` as root on `ip` and stream its output line by line ("\\r" counts as a line end,
    so the optimizer's progress line comes through). Returns (exit code, all output)."""
    known = os.path.expanduser("~/.ssh/dcsim_fleet_known_hosts")  # fresh IPs: keep them out of known_hosts
    args = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={int(min(timeout_s, 15))}",
            "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={known}",
            "-o", "ServerAliveInterval=15"]
    if os.environ.get("VULTR_SSH_KEY"):
        args += ["-i", os.path.expanduser(os.environ["VULTR_SSH_KEY"])]
    p = subprocess.Popen([*args, f"root@{ip}", command], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL)
    if stdin is not None:
        assert p.stdin is not None
        p.stdin.write(stdin)
        p.stdin.close()
    out, buf = [], b""
    assert p.stdout is not None
    while True:
        if stop is not None and stop.is_set():
            p.kill()
            break
        ch = p.stdout.read1(4096) if hasattr(p.stdout, "read1") else p.stdout.read(1)
        if not ch:
            break
        buf += ch
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for line in parts:
            t = line.decode(errors="replace").strip()
            if t:
                out.append(t)
                if on_line:
                    on_line(t)
    if buf.strip():
        out.append(buf.decode(errors="replace").strip())
    return p.wait(), "\n".join(out)


def public_key() -> str:
    """The laptop's public SSH key (VULTR_SSH_PUBKEY or ~/.ssh/id_ed25519.pub) for the workers' root login."""
    path = os.path.expanduser(os.environ.get("VULTR_SSH_PUBKEY", "~/.ssh/id_ed25519.pub"))
    try:
        key = open(path).read().strip()
    except OSError as e:
        raise VultrError(f"no public SSH key at {path} (set VULTR_SSH_PUBKEY)") from e
    if not key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-")) or "PRIVATE" in key:
        raise VultrError(f"{path} doesn't look like a public SSH key")
    return key


def user_data(pubkey: str) -> str:
    """cloud-init for a worker (base64, as Vultr wants it): lets this laptop's key log in as root (the account
    has no registered SSH key). Nothing secret: a public key only."""
    import base64

    script = ("#cloud-config\ndisable_root: false\nruncmd:\n"
              "  - mkdir -p /root/.ssh && chmod 700 /root/.ssh\n"
              f"  - echo '{pubkey}' >> /root/.ssh/authorized_keys\n"
              "  - chmod 600 /root/.ssh/authorized_keys\n")
    return base64.b64encode(script.encode()).decode()


def prep_command() -> str:
    """On a fresh Ubuntu worker, as root: uv, the public repo's `main`, the engine's packages."""
    return ("set -e; export HOME=/root PATH=/root/.local/bin:$PATH; "
            "command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1; "
            f"if [ -d {APP_DIR}/.git ]; then cd {APP_DIR} && git fetch -q --depth 1 origin main && git reset -q --hard origin/main; "
            f"else git clone -q --depth 1 --branch main {REPO} {APP_DIR}; fi; "
            f"cd {APP_DIR}/engine && uv sync -q && echo prep-ok")


def env_command() -> str:
    """Writes engine/.env from stdin (the Tiger URL), readable by root only."""
    return f"umask 077; cat > {APP_DIR}/engine/.env && echo env-ok"


def optimize_command(date: str, trials: int, source: str) -> str:
    return (f"export HOME=/root PATH=/root/.local/bin:$PATH; cd {APP_DIR}/engine && uv run dcsim optimize "
            f"--date {date} --trials {int(trials)} --save --source {source}")


SAVED = re.compile(r"saved to Tiger: ([0-9a-f]+)")


# ---------------------------------------------------------------- status board (Tiger)

class Board:
    """Writes run and task status to Tiger for the /tiger Fleet card. Never fails the run: a Tiger
    error is printed once and the board goes quiet."""

    def __init__(self, conn=None, log: Callable[[str], None] = print):
        self.conn, self.log, self.lock = conn, log, threading.Lock()

    @classmethod
    def connect(cls, log: Callable[[str], None] = print) -> Board:
        from . import tsdb

        try:
            conn = tsdb.connect(autocommit=True)
            conn.execute(TABLES)
            return cls(conn, log)
        except Exception as e:  # noqa: BLE001
            log(f"(Tiger not reachable, live status off: {type(e).__name__})")
            return cls(None, log)

    def _exec(self, sql: str, args: dict) -> None:
        if self.conn is None:
            return
        with self.lock:
            try:
                self.conn.execute(sql, args)
            except Exception as e:  # noqa: BLE001
                self.log(f"(Tiger status write failed, live status off: {type(e).__name__}: {str(e)[:120]})")
                self.conn = None

    def run(self, **f) -> None:
        if "status" not in f or "kind" not in f:
            sets = ", ".join(f"{c} = %({c})s" for c in f if c != "run_id")
            self._exec(f"UPDATE dcsim.fleet_runs SET {sets} WHERE run_id = %(run_id)s", f)
            return
        cols = list(f)
        upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "run_id")
        self._exec(f"INSERT INTO dcsim.fleet_runs ({', '.join(cols)}) VALUES ({', '.join(f'%({c})s' for c in cols)}) "
                   f"ON CONFLICT (run_id) DO UPDATE SET {upd}", f)

    def task(self, run_id: str, date: str, **f) -> None:
        if "status" not in f:  # a partial change: the row exists (queued first), so a plain UPDATE
            sets = ", ".join(f"{c} = %({c})s" for c in f)
            self._exec(f"UPDATE dcsim.fleet_tasks SET {sets} WHERE run_id = %(run_id)s AND menu_date = %(menu_date)s",
                       f | {"run_id": run_id, "menu_date": date})
            return
        f = {"run_id": run_id, "menu_date": date} | f
        cols = list(f)
        upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in ("run_id", "menu_date"))
        self._exec(f"INSERT INTO dcsim.fleet_tasks ({', '.join(cols)}) VALUES ({', '.join(f'%({c})s' for c in cols)}) "
                   f"ON CONFLICT (run_id, menu_date) DO UPDATE SET {upd}", f)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------- the run

def plan_run(api: Vultr, dates: list[str], workers: int, vcpus: int) -> dict:
    live = live_server(api)
    plan = pick_plan(api.plans("voc"), live["region"], vcpus)
    n = max(1, min(workers, len(dates), MAX_WORKERS))
    rounds = -(-len(dates) // n)
    return {
        "region": live["region"], "os_id": ubuntu_os_id(api), "plan": plan, "workers": n, "dates": dates,
        "hourly_usd": plan["hourly_cost"],
        "est_cost_usd": round(plan["hourly_cost"] * n * EXPECTED_MIN * rounds / 60, 2),
        "max_cost_usd": round(plan["hourly_cost"] * n * max(1, -(-EXPECTED_MIN * rounds // 60)), 2),
    }


def run_fleet(api: Vultr, dates: list[str], trials: int = 80, workers: int = MAX_WORKERS, vcpus: int = 16,
              max_minutes: float = 30, dry_run: bool = False, ssh=ssh_run, board: Board | None = None,
              log: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep,
              boot_timeout_s: float = 600, pubkey: str | None = None, env_text: str | None = None) -> dict:
    """Run `dcsim optimize --save` for every date on temporary Vultr servers. Returns a summary."""
    p = plan_run(api, dates, workers, vcpus)
    plan, n = p["plan"], p["workers"]
    log(f"{len(dates)} dates on {n} x {plan['id']} ({plan['vcpu_count']} vCPU, ${plan['hourly_cost']}/h) in {p['region']}, "
        f"fresh {OS_NAME}; "
        f"estimated ${p['est_cost_usd']} (at most ${p['max_cost_usd']} if billed by whole hours)")
    if dry_run:
        return p | {"dry_run": True}
    env = env_text or _worker_env()
    board = board or Board(None, log)
    boot = user_data(pubkey or public_key())
    run_id = "f" + uuid.uuid4().hex[:7]
    source = f"fleet:{run_id}"
    queue, qlock = deque(dates), threading.Lock()
    created: dict[str, dict] = {}  # instance id -> {"n", "created", "deleted"}
    stop = threading.Event()
    run_times: dict[str, float] = {}
    started = time.time()
    board.run(run_id=run_id, kind="optimize", dates=[dt.date.fromisoformat(d) for d in dates], trials=trials, plan=plan["id"], vcpus=plan["vcpu_count"],
              region=p["region"], workers=n, hourly_usd=plan["hourly_cost"], status="running",
              started_at=_now(), est_cost_usd=p["est_cost_usd"])
    for d in dates:
        board.task(run_id, d, status="queued")

    def delete(iid: str) -> None:
        info = created.get(iid)
        if info is None or info.get("deleted"):
            return
        if not delete_until_gone(api, iid, sleep, log=log):
            return
        info["deleted"] = time.time()
        for d in info["dates"]:
            board.task(run_id, d, deleted_at=_now())

    def next_date() -> str | None:
        with qlock:
            return queue.popleft() if queue and not stop.is_set() else None

    def worker(k: int) -> None:
        first = next_date()
        if first is None:
            return
        board.task(run_id, first, worker=k, status="creating", created_at=_now())
        try:
            inst = api.create_instance(region=p["region"], plan=plan["id"], os_id=p["os_id"],
                                       label=f"{TAG}-{run_id}-{k}", hostname=f"{TAG}-{k}",
                                       tags=[TAG, f"run:{run_id}"], backups="disabled", user_data=boot)
        except VultrError as e:  # e.g. the account's server limit: give the date back to the others
            log(f"worker {k}: not created ({e}); the other workers take its dates")
            board.task(run_id, first, worker=None, status="queued", created_at=None)
            with qlock:
                queue.appendleft(first)
            return
        iid = inst["id"]
        created[iid] = {"n": k, "created": time.time(), "dates": [first]}
        board.task(run_id, first, instance_id=iid, status="booting")
        try:
            ip = _wait_ready(api, iid, ssh, stop, sleep, boot_timeout_s)
            board.task(run_id, first, progress="installing uv and the engine")
            rc, out = ssh(ip, prep_command(), None, 900, stop)
            if rc != 0 or "prep-ok" not in out:
                raise VultrError(f"worker prep failed: {out[-300:]}")
            rc, out = ssh(ip, env_command(), None, 60, stop, stdin=env.encode())
            if rc != 0 or "env-ok" not in out:
                raise VultrError(f"could not send the Tiger settings: {out[-200:]}")
            date: str | None = first
            while date is not None:
                if date not in created[iid]["dates"]:
                    created[iid]["dates"].append(date)
                board.task(run_id, date, worker=k, instance_id=iid, status="running", ready_at=_now(),
                           started_at=_now())
                t0 = time.time()
                last = [0.0]

                def progress(line: str, date=date) -> None:
                    if time.time() - last[0] > 3 or "saved to Tiger" in line:  # throttle Tiger writes
                        last[0] = time.time()
                        board.task(run_id, date, progress=line[:200])

                rc, out = ssh(ip, optimize_command(date, trials, source), progress, 1800, stop)
                m = SAVED.search(out)
                secs = time.time() - t0
                if rc == 0 and m:
                    run_times[date] = secs
                    board.task(run_id, date, status="saved", finished_at=_now(), run_s=round(secs, 1),
                               search_id=m.group(1))
                    log(f"worker {k}: {date} saved ({secs / 60:.1f} min, search {m.group(1)})")
                else:
                    board.task(run_id, date, status="failed", finished_at=_now(), error=out[-300:])
                    log(f"worker {k}: {date} failed (exit {rc}): {out[-200:]}")
                date = next_date()
        except Exception as e:  # noqa: BLE001
            for d in created[iid]["dates"]:
                if d not in run_times:
                    board.task(run_id, d, status="failed", error=f"{type(e).__name__}: {str(e)[:250]}")
            log(f"worker {k}: {type(e).__name__}: {e}")
        finally:
            delete(iid)

    status = "done"
    try:
        with ThreadPoolExecutor(n) as ex:
            futs = [ex.submit(worker, k) for k in range(1, n + 1)]
            done, pending = wait(futs, timeout=max_minutes * 60)
            if pending:
                status = "stopped"
                log(f"!! {max_minutes:.0f} min limit reached: stopping and deleting every server")
                stop.set()
                for iid in list(created):
                    delete(iid)
                wait(futs, timeout=120)
    except KeyboardInterrupt:
        status = "stopped"
        stop.set()
        raise
    finally:
        stop.set()
        for iid in list(created):
            delete(iid)
        try:  # anything created whose id we never got back
            for i in api.instances(f"run:{run_id}"):
                if f"run:{run_id}" in (i.get("tags") or []) and TAG in (i.get("tags") or []):
                    delete_until_gone(api, i["id"], sleep, log=log)
        except VultrError as e:
            log(f"!! leftover check failed: {e} (run `dcsim fleet cleanup`)")
        wall = time.time() - started
        server_min = sum(((c.get("deleted") or time.time()) - c["created"]) / 60 for c in created.values())
        if status == "done" and len(run_times) < len(dates):
            status = "failed" if not run_times else "done"
        board.run(run_id=run_id, kind="optimize", status=status, finished_at=_now(), wall_s=round(wall, 1),
                  servers=len(created),
                  sequential_s=round(sum(run_times.values()), 1), server_minutes=round(server_min, 1),
                  est_cost_usd=round(server_min / 60 * plan["hourly_cost"], 2))
    summary = {
        "run_id": run_id, "source": source, "status": status, "plan": plan["id"], "workers": n,
        "servers": len(created), "dates": dates, "saved": sorted(run_times),
        "failed": [d for d in dates if d not in run_times],
        "wall_s": round(wall, 1), "sequential_s": round(sum(run_times.values()), 1),
        "server_minutes": round(server_min, 1), "cost_usd": round(server_min / 60 * plan["hourly_cost"], 2),
    }
    log(f"run {run_id}: {len(run_times)}/{len(dates)} dates saved in {wall / 60:.1f} min on {len(created)} servers; "
        f"the same runs one after another: {summary['sequential_s'] / 60:.1f} min; "
        f"{summary['server_minutes']:.0f} server-minutes, about ${summary['cost_usd']}")
    return summary


def _wait_ready(api: Vultr, iid: str, ssh, stop: threading.Event, sleep, timeout_s: float) -> str:
    end = time.time() + timeout_s
    ip = None
    while time.time() < end and not stop.is_set():
        i = api.instance(iid)
        if i.get("status") == "active" and i.get("power_status") == "running" and i.get("main_ip") not in (None, "", "0.0.0.0"):
            ip = i["main_ip"]
            rc, _ = ssh(ip, "true", None, 10, stop)
            if rc == 0:
                return ip
        sleep(10)
    raise VultrError(f"server {iid} not reachable over SSH after {timeout_s / 60:.0f} min")


def _worker_env() -> str:
    """engine/.env for a worker: only what `dcsim optimize --save` needs (the Tiger URL)."""
    from . import tsdb

    return f"DCSIM_TSDB_URL={tsdb.dsn()}\nLLM_PROVIDER=mock\n"


def cleanup(api: Vultr, log: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep) -> int:
    """Delete every dcsim-fleet server (retrying while locked) and any old `dcsim-worker` snapshot."""
    servers = fleet_servers(api)
    for i in servers:
        if delete_until_gone(api, i["id"], sleep, log=log):
            log(f"deleted {i['label']} ({i['id']})")
    for sn in api.snapshots():
        if sn.get("description") == SNAPSHOT_DESC:
            api.delete_snapshot(sn["id"])
            log(f"deleted old worker snapshot {sn['id']} (snapshots bill for storage)")
    return len(servers)


# ---------------------------------------------------------------- dashboard

def dashboard_block(conn, read) -> dict | None:
    """The /tiger Fleet card: the latest run and its dates. None until a fleet has run."""
    if conn.execute("SELECT to_regclass('dcsim.fleet_runs')").fetchone()[0] is None:
        return None
    run = read("fleet_run", LATEST_SQL)
    if not run:
        return None
    return {"run": run[0], "tasks": read("fleet_tasks", TASKS_SQL, {"run_id": run[0]["run_id"]})}


__all__ = ["Vultr", "VultrError", "TABLES", "pick_plan", "parse_dates", "plan_run", "run_fleet", "cleanup",
           "delete_until_gone", "fleet_servers", "live_server", "Board", "dashboard_block"]
