# Deploy to a Vultr server

The app and API run on one Ubuntu server: Caddy serves `web/dist` and HTTPS, and proxies `/api/*`, `/tiger` and `/docs` to `dcsim serve` (systemd, user `dcsim`, `127.0.0.1:8000`). Sponsor context: [docs/SPONSORS.md](../docs/SPONSORS.md) §4–5.

| File | Runs on | What it does |
| --- | --- | --- |
| `setup.sh` | server, as root | packages, Node 24, uv, clone/pull, build, settings, systemd, Caddy, firewall. Safe to re-run (that's how you update) |
| `push-secrets.sh` | your laptop | sends `engine/.env` (+ a Tiger URL) and `cache/`, restarts the API |
| `dcsim.service` | — | systemd unit, installed by `setup.sh` |
| `Caddyfile` | — | template, filled in by `setup.sh` |

## 1. Create the server
Vultr → Deploy → **CPU Optimized**, Ubuntu 24.04, 8+ vCPU if the credits allow. Select your SSH key (`~/.ssh/id_ed25519.pub`). Point the domain's `A` record (`@` and `www`) at the server's IP.

## 2. Set it up (on the server)
```bash
ssh root@<server-ip>
curl -fsSL https://raw.githubusercontent.com/victornguyen7/DC-HackUMass/main/deploy/setup.sh | DOMAIN=your-domain bash
```
- No domain yet? Leave out `DOMAIN=...`: the site is served on `http://<server-ip>`. Re-run with `DOMAIN` once DNS points at the server (delete `/etc/dcsim.env` first if you want `DCSIM_CORS` rewritten, or edit it).
- Want Ask Sam's Tiger lookups? Add `TIGER_CLI=1`, then once: `sudo -iu dcsim tiger auth login`, and `systemctl restart dcsim`. Without it, Sam works without Tiger lookups (`DCSIM_TIGER_MCP=0`).
- The server deploys what is **pushed to GitHub `main`**, not your uncommitted work.

## 3. Send secrets and cache (on your laptop)
```bash
deploy/push-secrets.sh root@<server-ip>
```

## Day to day
| Task | Command (on the server) |
| --- | --- |
| Update to the latest `main` | `bash /srv/dc-whatif/deploy/setup.sh` (add `DOMAIN=...` again) |
| Logs | `journalctl -u dcsim -f` |
| Restart | `systemctl restart dcsim` |
| Server settings (workers, CORS, rate limit, ledger) | `nano /etc/dcsim.env`, then restart |
| Admin token | `grep DCSIM_ADMIN_TOKEN /etc/dcsim.env` |
| Run the 1,000-world sweep for the demo | `sudo -iu dcsim bash -lc 'cd /srv/dc-whatif/engine && time uv run dcsim worlds --worlds 1000'` |

**Before the demo:** `DCSIM_LEDGER_WRITES=0` is the default, so visitors pressing Adopt don't write undeletable forecasts to Tiger. Set it to `1` in `/etc/dcsim.env` and restart when you want Adopt to log to Tiger on stage.
