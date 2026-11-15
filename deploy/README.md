# Deploy to a Vultr server

The app and API run on one Ubuntu server. Caddy serves `web/dist` over HTTPS and passes `/api/*`, `/tiger`, `/docs` and `/openapi.json` to `dcsim serve` (systemd, user `dcsim`, `127.0.0.1:8000`). Sponsor context: [docs/SPONSORS.md](../docs/SPONSORS.md) §4–5.

## Live now (2026-11-14)

| | |
| --- | --- |
| **Link** | **https://155-138-211-252.sslip.io** (sslip.io turns the IP into a hostname until we have a domain, §5 of SPONSORS.md) |
| Server | Vultr, Ubuntu 24.04, IP `155.138.211.252`, app in `/srv/dc-whatif` |
| HTTPS | Let's Encrypt certificate from Caddy, valid until 2027-02-13 (Caddy renews it) |
| Checked from outside | the web app and direct links (`/sam`) load; the demo bundle is served; `/api/health` OK with the laptop's cache copied over (729 configs, 16,168 nights, 6 scenarios, 992 LLM responses); `/tiger` loads; Gemini answers; **Ask Sam's Tiger lookups work** ("Which dishes ran out on rainy Fridays?") |
| Server settings | `/etc/dcsim.env`: workers = core count, `DCSIM_CORS` = the link, admin token set, `DCSIM_AI_RATE=10`, **`DCSIM_LEDGER_WRITES=0`** (Adopt doesn't write to Tiger yet) |
| Deployed from | `main` (setup pulls GitHub; it never deploys uncommitted work) |

**Still to do:**
- [ ] Time the 1,000-world sweep on the server and keep the output and screenshots (`htop`, the Vultr dashboard) for Devpost (command below).
- [ ] Before the demo: `DCSIM_LEDGER_WRITES=1` (command below), so Adopt logs forecasts to Tiger on stage.
- [ ] Register the GoDaddy Registry domain, point its `A` record at `155.138.211.252`, re-run setup with `DOMAIN=` it (SPONSORS.md §5).
- [ ] Optional hardening: SSH with keys only (`PasswordAuthentication no` in `/etc/ssh/sshd_config`).

## Files

| File | Runs on | What it does |
| --- | --- | --- |
| `setup.sh` | server, as root | packages, Node 24, uv, clone or pull, build, settings, systemd, Caddy, firewall; with `TIGER_CLI=1` the Tiger CLI and its password. Safe to re-run: that's how you update |
| `push-secrets.sh` | your laptop | sends `engine/.env` (plus `DCSIM_TSDB_URL` from `tiger db uri`) and `cache/`, saves the Tiger password on the server, restarts the API |
| `tiger-password.sh` | server, as root | switches the server's Tiger CLI to `~/.pgpass` and saves the database password from `DCSIM_TSDB_URL` (the two scripts above call it) |
| `dcsim.service` | — | systemd unit, installed by `setup.sh` |
| `Caddyfile` | — | template, filled in by `setup.sh` |

## Set up a server from scratch

**1. Create it.** Vultr → Deploy → **CPU Optimized**, Ubuntu 24.04, as many vCPU as the credits allow (more cores = more parallel nights). Select your SSH key (`~/.ssh/id_ed25519.pub`); a key added after the server exists isn't installed on it.

**2. Set it up** (on the server, `ssh root@<server-ip>`):
```bash
curl -fsSL https://raw.githubusercontent.com/victornguyen7/DC-HackUMass/main/deploy/setup.sh | DOMAIN=<ip-with-dashes>.sslip.io TIGER_CLI=1 bash
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
| Update to the latest `main` | `DOMAIN=155-138-211-252.sslip.io TIGER_CLI=1 bash /srv/dc-whatif/deploy/setup.sh` |
| Change the domain | the same command with the new `DOMAIN` (it updates `DCSIM_CORS` and keeps the admin token and other settings) |
| Logs | `journalctl -u dcsim -f` (Caddy: `journalctl -u caddy -n 50 --no-pager`) |
| Restart | `systemctl restart dcsim` |
| Server settings | `nano /etc/dcsim.env`, then restart |
| The link | `grep DCSIM_CORS /etc/dcsim.env` |
| Admin token | `grep DCSIM_ADMIN_TOKEN /etc/dcsim.env` |
| Adopt logs to Tiger (demo) | `sed -i 's/^DCSIM_LEDGER_WRITES=.*/DCSIM_LEDGER_WRITES=1/' /etc/dcsim.env && systemctl restart dcsim` |
| Tiger password again | `bash /srv/dc-whatif/deploy/tiger-password.sh && systemctl restart dcsim` |
| 1,000-world sweep (Vultr evidence) | `sudo -iu dcsim bash -lc 'cd /srv/dc-whatif/engine && time uv run dcsim worlds --worlds 1000'` |

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
