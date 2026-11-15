"""Vultr fleet (fleet.py) against a fake Vultr API and a fake SSH runner: nothing real is created."""

from __future__ import annotations

import json
import threading

import httpx
import pytest

from dcsim import fleet
from dcsim.tiger_sql import script

PLANS = [
    {"id": "voc-c-8c-16gb-150s-amd", "vcpu_count": 8, "disk": 150, "hourly_cost": 0.219, "locations": ["atl", "ewr"]},
    {"id": "voc-c-16c-32gb-500s-amd", "vcpu_count": 16, "disk": 500, "hourly_cost": 0.493, "locations": ["atl"]},
    {"id": "voc-c-16c-32gb-300s-amd", "vcpu_count": 16, "disk": 300, "hourly_cost": 0.438, "locations": ["atl"]},
    {"id": "voc-c-16c-32gb-200s-amd", "vcpu_count": 16, "disk": 200, "hourly_cost": 0.40, "locations": ["ewr"]},
    {"id": "voc-g-16c-64gb-amd", "vcpu_count": 16, "disk": 300, "hourly_cost": 0.30, "locations": ["atl"]},
]


class FakeVultr:
    """In-memory Vultr API v2: instances, snapshots, plans. `max_servers` mimics an account limit."""

    def __init__(self, max_servers: int = 99, snapshot: bool = False, locked_deletes: int = 0):
        self.servers: dict[str, dict] = {
            "live": {"id": "live", "label": "", "main_ip": fleet.LIVE_IP, "region": "atl", "disk": 300,
                     "plan": "voc-c-16c-32gb-300s-amd", "status": "active", "power_status": "running", "tags": []}}
        self.snaps = [{"id": "snap1", "description": fleet.SNAPSHOT_DESC, "status": "complete",
                       "date_created": "2026-11-14T10:00:00+00:00", "size": 5e9}] if snapshot else []
        self.max_servers, self.created, self.deleted, self.n, self.lock = max_servers, [], [], 0, threading.Lock()
        self.locked_deletes, self.snaps_deleted = locked_deletes, []

    def handler(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path.removeprefix("/v2"), req.method
        with self.lock:
            if path == "/os":
                return httpx.Response(200, json={"os": [{"id": 1743, "name": "Ubuntu 22.04 LTS x64"},
                                                         {"id": 2284, "name": "Ubuntu 24.04 LTS x64"}],
                                                  "meta": {"links": {"next": ""}}})
            if path.startswith("/snapshots/") and m == "DELETE":
                self.snaps = [x for x in self.snaps if x["id"] != path.split("/")[-1]]
                self.snaps_deleted.append(path.split("/")[-1])
                return httpx.Response(204)
            if path == "/plans":
                return httpx.Response(200, json={"plans": PLANS, "meta": {"links": {"next": ""}}})
            if path == "/snapshots" and m == "GET":
                return httpx.Response(200, json={"snapshots": self.snaps, "meta": {"links": {"next": ""}}})
            if path == "/snapshots" and m == "POST":
                s = {"id": "snap2", "description": json.loads(req.content)["description"], "status": "complete",
                     "date_created": "2026-11-14T12:00:00+00:00", "size": 6e9}
                self.snaps.append(s)
                return httpx.Response(201, json={"snapshot": s})
            if path.startswith("/snapshots/"):
                return httpx.Response(200, json={"snapshot": next(s for s in self.snaps if s["id"] == path.split("/")[-1])})
            if path == "/instances" and m == "GET":
                tag = req.url.params.get("tag")
                xs = [i for i in self.servers.values() if not tag or tag in i["tags"]]
                return httpx.Response(200, json={"instances": xs, "meta": {"links": {"next": ""}}})
            if path == "/instances" and m == "POST":
                if sum(1 for i in self.servers if i != "live") >= self.max_servers:
                    return httpx.Response(400, json={"error": "server limit reached"})
                body = json.loads(req.content)
                self.n += 1
                i = {"id": f"w{self.n}", "label": body["label"], "main_ip": f"10.0.0.{self.n}", "region": body["region"],
                     "plan": body["plan"], "status": "active", "power_status": "running", "tags": body["tags"],
                     "date_created": "now", "os_id": body["os_id"], "user_data": body.get("user_data")}
                self.servers[i["id"]] = i
                self.created.append(i["id"])
                return httpx.Response(202, json={"instance": i})
            iid = path.split("/")[-1]
            if m == "GET":
                return httpx.Response(200, json={"instance": self.servers[iid]})
            if m == "DELETE":
                if self.locked_deletes > 0:
                    self.locked_deletes -= 1
                    return httpx.Response(409, json={"error": "Server is currently locked"})
                self.servers.pop(iid, None)
                self.deleted.append(iid)
                return httpx.Response(204)
        return httpx.Response(404)

    def api(self) -> fleet.Vultr:
        return fleet.Vultr("test-key", transport=httpx.MockTransport(self.handler), sleep=lambda s: None)


class FakeSSH:
    """Records commands per IP; `optimize` prints the CLI's 'saved to Tiger' line unless told to fail."""

    def __init__(self, fail_dates=(), hang=False):
        self.calls: list[tuple[str, str]] = []
        self.fail_dates, self.hang, self.lock = set(fail_dates), hang, threading.Lock()

    def __call__(self, ip, command, on_line=None, timeout_s=30, stop=None, stdin=None):
        with self.lock:
            self.calls.append((ip, command))
            if stdin is not None:
                self.stdin = stdin
        if command == "true":
            return 0, ""
        if "prep-ok" in command:
            return 0, "prep-ok"
        if "env-ok" in command:
            return 0, "env-ok"
        date = command.split("--date ")[1].split()[0]
        if self.hang:
            while stop is not None and not stop.is_set():
                stop.wait(0.05)
            return 255, "killed"
        threading.Event().wait(0.05)  # a run takes time, so every worker gets dates
        if on_line:
            on_line("trial 80/80: waste 2400  best 3300")
        if date in self.fail_dates:
            return 1, "Traceback: boom"
        return 0, f"searched 80 plans\nsaved to Tiger: {abs(hash(date)) % 16**8:08x}"


class FakeBoard(fleet.Board):
    def __init__(self):
        super().__init__(None, lambda s: None)
        self.runs, self.tasks = {}, {}

    def run(self, **f):
        self.runs.setdefault(f["run_id"], {}).update(f)

    def task(self, run_id, date, **f):
        self.tasks.setdefault((run_id, date), {}).update(f)


DATES = [f"2026-11-{d}" for d in range(13, 21)]
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest laptop"
ENV = "DCSIM_TSDB_URL=postgresql://u:secret@h/db\nLLM_PROVIDER=mock\n"


def test_pick_plan_cheapest_cpu_optimized_in_region():
    assert fleet.pick_plan(PLANS, "atl", 16)["id"] == "voc-c-16c-32gb-300s-amd"  # not the general-purpose one
    assert fleet.pick_plan(PLANS, "atl", 8)["id"] == "voc-c-8c-16gb-150s-amd"
    assert fleet.pick_plan(PLANS, "atl", 8, min_disk=300)["id"] == "voc-c-16c-32gb-300s-amd"  # image needs the disk
    with pytest.raises(fleet.VultrError):
        fleet.pick_plan(PLANS, "atl", 64)


def test_parse_dates():
    assert fleet.parse_dates("2026-11-13..2026-11-15") == ["2026-11-13", "2026-11-14", "2026-11-15"]
    assert fleet.parse_dates("2026-11-13, 2026-11-20") == ["2026-11-13", "2026-11-20"]


def test_dry_run_creates_nothing():
    v = FakeVultr()
    r = fleet.run_fleet(v.api(), DATES, dry_run=True, ssh=FakeSSH(), log=lambda s: None)
    assert r["dry_run"] and r["workers"] == 8 and r["plan"]["id"] == "voc-c-16c-32gb-300s-amd"
    assert r["est_cost_usd"] > 0 and r["max_cost_usd"] >= r["est_cost_usd"] and not v.created


def test_run_spreads_dates_saves_each_and_deletes_every_server():
    v, ssh, board = FakeVultr(), FakeSSH(), FakeBoard()
    r = fleet.run_fleet(v.api(), DATES, workers=3, ssh=ssh, board=board, pubkey=KEY, env_text=ENV, log=lambda s: None, sleep=lambda s: None)
    assert r["status"] == "done" and r["saved"] == DATES and not r["failed"]
    assert len(v.created) == 3 and sorted(v.deleted) == sorted(v.created)  # 3 servers took 8 dates
    assert set(v.servers) == {"live"}  # the live server is never touched
    runs = [c for _, c in ssh.calls if "dcsim optimize" in c]
    assert len(runs) == 8 and all(f"--save --source {r['source']}" in c for c in runs)
    assert all(board.tasks[(r["run_id"], d)]["status"] == "saved" for d in DATES)
    assert all(board.tasks[(r["run_id"], d)].get("deleted_at") for d in DATES)
    assert board.runs[r["run_id"]]["status"] == "done" and board.runs[r["run_id"]]["sequential_s"] >= 0
    assert ssh.stdin == ENV.encode()  # the Tiger URL goes over SSH stdin, never in Vultr metadata


def test_a_failed_date_still_deletes_and_is_reported():
    v, board = FakeVultr(), FakeBoard()
    r = fleet.run_fleet(v.api(), DATES[:4], workers=2, pubkey=KEY, env_text=ENV, ssh=FakeSSH(fail_dates={DATES[1]}), board=board,
                        log=lambda s: None, sleep=lambda s: None)
    assert r["failed"] == [DATES[1]] and len(r["saved"]) == 3
    assert board.tasks[(r["run_id"], DATES[1])]["status"] == "failed"
    assert set(v.servers) == {"live"}


def test_account_limit_shrinks_the_fleet_without_failing():
    v = FakeVultr(max_servers=2)
    r = fleet.run_fleet(v.api(), DATES, workers=8, pubkey=KEY, env_text=ENV, ssh=FakeSSH(), board=FakeBoard(), log=lambda s: None,
                        sleep=lambda s: None)
    assert r["saved"] == DATES and r["servers"] == 2 and set(v.servers) == {"live"}


def test_time_limit_deletes_everything():
    v = FakeVultr()
    r = fleet.run_fleet(v.api(), DATES[:2], workers=2, pubkey=KEY, env_text=ENV, ssh=FakeSSH(hang=True), board=FakeBoard(),
                        max_minutes=0.02, log=lambda s: None, sleep=lambda s: None)
    assert r["status"] == "stopped" and not r["saved"] and set(v.servers) == {"live"} and len(v.deleted) == 2


def test_cleanup_only_deletes_fleet_servers():
    v = FakeVultr()
    v.servers["other"] = {"id": "other", "label": "x", "main_ip": "1.2.3.4", "tags": ["dcsim-fleet-not"]}
    v.servers["stray"] = {"id": "stray", "label": "dcsim-fleet-old-1", "main_ip": "1.2.3.5", "tags": ["dcsim-fleet"]}
    v.snaps = [{"id": "old", "description": fleet.SNAPSHOT_DESC, "status": "complete"}, {"id": "keep", "description": "x"}]
    assert fleet.cleanup(v.api(), log=lambda s: None, sleep=lambda s: None) == 1
    assert set(v.servers) == {"live", "other"} and v.snaps_deleted == ["old"]


def test_locked_servers_are_deleted_once_vultr_lets_go():
    v = FakeVultr(locked_deletes=3)
    r = fleet.run_fleet(v.api(), DATES[:1], workers=1, pubkey=KEY, env_text=ENV, ssh=FakeSSH(), board=FakeBoard(),
                        log=lambda s: None, sleep=lambda s: None)
    assert r["saved"] == DATES[:1] and set(v.servers) == {"live"}


def test_bad_key_says_what_to_do():
    api = fleet.Vultr("k", transport=httpx.MockTransport(lambda r: httpx.Response(401)), sleep=lambda s: None)
    with pytest.raises(fleet.VultrError, match="Access Control"):
        api.instances()
    with pytest.raises(fleet.VultrError, match="VULTR_API_KEY"):
        fleet.Vultr("")


def test_commands_and_schema():
    assert "--save --source fleet:abc" in fleet.optimize_command("2026-11-13", 80, "fleet:abc")
    assert "git clone -q --depth 1 --branch main" in fleet.prep_command() and "uv sync" in fleet.prep_command()
    assert "umask 077" in fleet.env_command()
    s = script()
    assert "CREATE TABLE IF NOT EXISTS dcsim.fleet_runs" in s and "dcsim.fleet_tasks" in s
    assert "VULTR" not in s and "main_ip" not in fleet.TABLES  # no secrets, no IPs in Tiger


def test_service_batches_fill_the_pool():
    from concurrent.futures import ThreadPoolExecutor

    from dcsim.data import load_baseline
    from dcsim.service import BATCH, Service
    from dcsim.store import Store

    cfg = load_baseline("2026-11-13")
    import tempfile

    with tempfile.TemporaryDirectory() as d, ThreadPoolExecutor(16) as pool:
        assert [len(b) for b in Service(Store(d)).batches(cfg, 10)] == [BATCH, 2]
        assert [len(b) for b in Service(Store(d), pool).batches(cfg, 10)] == [10]  # one trial = one map on 16 cores


def test_workers_get_the_laptop_key_and_nothing_secret():
    import base64

    v = FakeVultr()
    seen = []
    orig = v.handler

    def spy(req):
        if req.method == "POST" and req.url.path.endswith("/instances"):
            seen.append(json.loads(req.content))
        return orig(req)

    api = fleet.Vultr("test-key", transport=httpx.MockTransport(spy), sleep=lambda s: None)
    fleet.run_fleet(api, DATES[:1], workers=1, pubkey=KEY, env_text=ENV, ssh=FakeSSH(), board=FakeBoard(), log=lambda s: None,
                    sleep=lambda s: None)
    ud = base64.b64decode(seen[0]["user_data"]).decode()
    assert ud.startswith("#cloud-config") and KEY in ud and "/root/.ssh/authorized_keys" in ud
    assert "test-key" not in ud and "VULTR" not in ud and "secret" not in ud and "secret" not in json.dumps(seen[0])
    with pytest.raises(fleet.VultrError):
        import os
        os.environ["VULTR_SSH_PUBKEY"] = "/nonexistent.pub"
        try:
            fleet.public_key()
        finally:
            del os.environ["VULTR_SSH_PUBKEY"]
