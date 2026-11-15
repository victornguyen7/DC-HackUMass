# Deploy to a Vultr server

The app and API run on one Ubuntu server. Caddy serves `web/dist` over HTTPS and passes `/api/*`, `/tiger`, `/docs` and `/openapi.json` to `dcsim serve` (systemd, user `dcsim`, `127.0.0.1:8000`). Sponsor context: [docs/SPONSORS.md](../docs/SPONSORS.md) §4–5.

## Live now (2026-11-14)

| | |
| --- | --- |
| **Link** | **https://dineamics.compare** (GoDaddy Registry domain, `A @ → 155.138.211.252`; since 2026-11-15. The earlier https://155-138-211-252.sslip.io stopped working when Caddy switched to the domain) |
| Server | Vultr, Ubuntu 24.04, IP `155.138.211.252`, app in `/srv/dc-whatif` |
| HTTPS | Let's Encrypt certificate for `dineamics.compare` from Caddy, valid until 2027-02-13 (Caddy renews it) |
| Checked from outside | the web app and direct links (`/sam`) load; the demo bundle is served; `/api/health` OK with the laptop's cache copied over (729 configs, 16,168 nights, 6 scenarios, 992 LLM responses); `/tiger` loads; Gemini answers; **Ask Sam's Tiger lookups work** ("Which dishes ran out on rainy Fridays?") |
| Server settings | `/etc/dcsim.env`: workers = core count, `DCSIM_CORS` = the link, admin token set, `DCSIM_AI_RATE=10`, **`DCSIM_LEDGER_WRITES=0`** (Adopt doesn't write to Tiger yet) |
| Deployed from | `main` (setup pulls GitHub; it never deploys uncommitted work) |

**Still to do:**
- [ ] Time the 1,000-world sweep on the server and keep the output and screenshots (`htop`, the Vultr dashboard) for Devpost (command below).
- [ ] Before the demo: `DCSIM_LEDGER_WRITES=1` (command below), so Adopt logs forecasts to Tiger on stage.
- [x] Domain: `dineamics.compare` (GoDaddy Registry), `A @ → 155.138.211.252`, setup re-run with `DOMAIN=dineamics.compare` (2026-11-15). Let's Encrypt certificate valid until 2027-02-13. `www.dineamics.compare` isn't served (the Caddy template takes one hostname).
- [ ] Optional hardening: SSH with keys only (`PasswordAuthentication no` in `/etc/ssh/sshd_config`).

## Files

| File | Runs on | What it does |
| --- | --- | --- |
| `setup.sh` | server, as root | packages, Node 24, uv, clone or pull, build, settings, systemd, Caddy, firewall; with `TIGER_CLI=1` the Tiger CLI and its password. Safe to re-run: that's how you update |
| `push-secrets.sh` | your laptop | sends `engine/.env` (plus `DCSIM_TSDB_URL` from `tiger db uri`) and `cache/`, saves the Tiger password on the server, restarts the API |
| `tiger-password.sh` | server, as root | switches the server's Tiger CLI to `~/.pgpass` and saves the database password from `DCSIM_TSDB_URL` (the two scripts above call it) |
| `dcsim.service` | — | systemd unit, installed by `setup.sh` |
| `engine/src/dcsim/fleet.py` | your laptop | `dcsim fleet`: temporary worker servers for plan searches (section below) |
| `Caddyfile` | — | template, filled in by `setup.sh` |

## Set up a server from scratch

**1. Create it.** Vultr → Deploy → **CPU Optimized**, Ubuntu 24.04, as many vCPU as the credits allow (more cores = more parallel nights). Select your SSH key (`~/.ssh/id_ed25519.pub`); a key added after the server exists isn't installed on it.

**2. Set it up** (on the server, `ssh root@<server-ip>`):
```bash
curl -fsSL https://raw.githubusercontent.com/victornguyen7/Dineamics/main/deploy/setup.sh | DOMAIN=<ip-with-dashes>.sslip.io TIGER_CLI=1 bash
```
- `DOMAIN` is a real hostname, never the placeholder: the script refuses `your-domain`, `example.com` and anything with `https://` or `/`. No domain yet: `<ip-with-dashes>.sslip.io` (e.g. `155-138-211-252.sslip.io`). Own a domain: its `A` record must already point at the server, or Caddy can't get the certificate.
- Without `DOMAIN` the site is plain HTTP on the IP (for a first test only).
- Leave out `TIGER_CLI=1` and Sam plans without Tiger lookups (`DCSIM_TIGER_MCP=0`).
- Right after a push to `main`, raw.githubusercontent.com can serve the old script for a few minutes; use the commit hash instead of `main` in the URL.

**3. Log the server's Tiger CLI in** (once, only with `TIGER_CLI=1`):
```bash
sudo -iu dcsim tiger auth login --headless     # prints a code: open the link on your laptop and enter it
```

**4. Send secrets and cache** (on your laptop, from the repo):
```bash
deploy/push-secrets.sh root@<server-ip>          # add --no-cache to skip the 275 MB cache/
```
It must not print "no DCSIM_TSDB_URL … Tiger features will be off". It ends by saving the Tiger password on the server and restarting the API.

**5. Check:**
```bash
curl https://<your-link>/api/health              # from anywhere: ok, and cache counts above 0
sudo -iu dcsim bash -lc 'cd /srv/dc-whatif/engine && uv run dcsim agent "Which dishes ran out on rainy Fridays?"'
```
The Sam check names 3 rainy Fridays, Cumin Basmati Rice and Savory Baked Chicken Thigh (TIGER.md §6).

## Day to day (on the server, as root)

| Task | Command |
| --- | --- |
| Update to the latest `main` | `DOMAIN=dineamics.compare TIGER_CLI=1 bash /srv/dc-whatif/deploy/setup.sh` (always pass the domain: without it the site falls back to plain HTTP) |
| Change the domain | the same command with the new `DOMAIN` (it updates `DCSIM_CORS` and keeps the admin token and other settings) |
| Logs | `journalctl -u dcsim -f` (Caddy: `journalctl -u caddy -n 50 --no-pager`) |
| Restart | `systemctl restart dcsim` |
| Server settings | `nano /etc/dcsim.env`, then restart |
| The link | `grep DCSIM_CORS /etc/dcsim.env` |
| Admin token | `grep DCSIM_ADMIN_TOKEN /etc/dcsim.env` |
| Adopt logs to Tiger (demo) | `sed -i 's/^DCSIM_LEDGER_WRITES=.*/DCSIM_LEDGER_WRITES=1/' /etc/dcsim.env && systemctl restart dcsim` |
| Tiger password again | `bash /srv/dc-whatif/deploy/tiger-password.sh && systemctl restart dcsim` |
| 1,000-world sweep (Vultr evidence) | `sudo -iu dcsim bash -lc 'cd /srv/dc-whatif/engine && time uv run dcsim worlds --worlds 1000'` |

## Fleet: many plan searches at once on temporary servers (`dcsim fleet`)

One command runs the optimizer on every menu date at once, one temporary Vultr server per date, saves each search to Tiger and deletes the servers (`engine/src/dcsim/fleet.py`). Run it from your laptop, in `engine/`:

```bash
uv run dcsim fleet status                     # the key works, any fleet servers running now
uv run dcsim fleet optimize --dry-run         # plan, number of servers, cost estimate; creates nothing
uv run dcsim fleet optimize                   # every menu date, 8 x 16-vCPU servers; or --dates 2026-11-13..2026-11-16
uv run dcsim fleet cleanup                    # delete any leftover dcsim-fleet server (normally there are none) and old worker snapshots
```

- **Key:** `VULTR_API_KEY` in `engine/.env` (gitignored; Vultr → Account → API). If Vultr answers 401/403, allow your IP under API → Access Control.
- **Workers** are fresh Ubuntu 24.04 servers. Your laptop's public key (`~/.ssh/id_ed25519.pub`, or `VULTR_SSH_PUBKEY`) is added to their root login through cloud-init (the account has no registered SSH key; the live server is never changed). Over SSH each worker installs uv, clones `main` from GitHub and syncs the packages, receives the Tiger URL on stdin (so it never sits in Vultr's server metadata), then runs `dcsim optimize --date D --save --source fleet:<run>` for each date it takes from the queue.
- **Plan:** the cheapest CPU-optimized plan with at least `--vcpus` (default 16) in the live server's region; at most 8 servers (`--workers`). If Vultr refuses a server (account limit), the others take its dates.
- **Billing safety:** servers bill until deleted, even when stopped. Every server a run creates is deleted when it finishes, on an error, on Ctrl-C and after `--max-minutes` (default 30); `dcsim fleet cleanup` deletes anything tagged `dcsim-fleet`.
- **Live view:** the `/tiger` dashboard's **Fleet (Vultr)** card (from `dcsim.fleet_runs` and `dcsim.fleet_tasks` in Tiger: no secrets, no IP addresses) shows each date's server, status, run time and last progress line; the saved searches appear in the Lab's Optimize card and the Plan searches card.
- Code changes only need to be on `main` (workers clone it). Searches are reproducible on one machine but not across machines: Optuna's sampler picked different plans from trial 34 on the AMD servers than on the laptop, so a fleet run is a second independent search, not a copy.
- **Lessons from the first tries:** a snapshot of the live server (322 GB) took over 10 minutes to restore and kept the server "locked", which also blocks deleting it. Workers now boot fresh Ubuntu, and deletes retry until Vultr lets go.

**First run (2026-11-15, run `f72caee8`):** all 8 menu dates, 80 plans each, saved to Tiger in **3.3 min** on 3 × 16-vCPU CPU-optimized servers (`voc-c-16c-32gb-300s-amd`, Atlanta, $0.438/h); one after another on those servers they'd take 6.0 min, and on the 8-core laptop about 13 min. Each server was reachable and set up (uv, clone, `uv sync`) about 60 s after it was created; each date's search took 37–50 s (the laptop: 86–160 s). 9 server-minutes, about **$0.07**; every server deleted. 8 servers were asked for, but **the account's monthly-fee limit allowed only 3**: the other 5 creates were refused and the 3 servers shared the dates. Ask Vultr support for a limit increase to run all 8 at once (then about 2 min).

## Troubleshooting (all hit on the first deploy, 2026-11-14, and fixed in the scripts)

| Symptom | Cause | Fix |
| --- | --- | --- |
| `fatal: detected dubious ownership in repository at '/srv/dc-whatif'` | an earlier clone made as root; git runs as `dcsim` | fixed in `e3ffbc2`: setup now gives the whole folder to `dcsim` (`chown -R`). Re-run setup |
| The IP shows nothing, and `DCSIM_CORS=https://your-domain` | setup ran with the placeholder `DOMAIN=your-domain`, so Caddy only answers for that name | re-run setup with a real `DOMAIN`; setup now refuses placeholders and updates `DCSIM_CORS` on a re-run |
| `curl: (35) … tlsv1 alert internal error` on the link | Caddy has no certificate for that name yet: setup not re-run with it, or DNS doesn't point at the server | re-run setup with that `DOMAIN`; wait a minute; `journalctl -u caddy -n 40 --no-pager \| grep -iE "error\|certificate"` |
| Sam: `password authentication failed for user "tsdbadmin"` | push-secrets called `tiger db uri` without the service ID, so no `DCSIM_TSDB_URL` reached the server and an empty password was saved | fixed in `3ebc72c` (passes `TIGER_SERVICE_ID`). Run `deploy/push-secrets.sh` again |
| `failed to save password: exec: "dbus-launch": executable file not found` | the Tiger CLI's default password storage is the system keyring, which a server doesn't have | `tiger-password.sh` sets `password_storage pgpass` and saves to `~/.pgpass` |
| `password length: 0` from a multi-line `sudo -iu dcsim bash -lc '…'` | `sudo -i` joins the lines into one, so the variables were set only for the `echo` | one command per `sudo -i` call (as `tiger-password.sh` does) |
| `SyntaxWarning: "\/" is an invalid escape sequence` from `google/genai/types.py` | the Gemini SDK on Python 3.14 | harmless |
| "Run as root" | | `ssh root@<server-ip>` (the scripts use `sudo -iu dcsim` themselves) |
