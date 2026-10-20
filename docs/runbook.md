# Runbook — status.2143.me

`status.2143.me` runs on its own DigitalOcean droplet (`tor1`, `s-1vcpu-512mb-10gb`,
reserved IP). The home cluster no longer serves it, and there is no SSH: incoming
port 22 is not in the firewall at all.

## What runs where

| Piece | What it is |
|---|---|
| Caddy | TLS (Let's Encrypt, HTTP/1.1 + HTTP/2 only — no UDP 443), serves `/var/lib/status/www`, proxies `/v1/*`, `/healthz`, `/home-status` to `statusd` |
| `statusd` | `python3 -m statusd` as user `statusd`, unit `statusd.service`, listens on `127.0.0.1:8080` only |
| SQLite | `/var/lib/status/history.db` (WAL). `hourly` rows are kept forever, `recent` 10-minute rows for 48 h, `outage` rows for incidents |
| Snapshots | `/var/lib/status/snapshots/history-<UTC>.db`, `VACUUM INTO` daily at 03:30 UTC, 7 days kept |
| `/opt/status/src` | a clone of this repo at the deployed commit. Configured files are copies in `/etc/status/`, `/etc/caddy/`, `/etc/systemd/system/`, `/usr/local/sbin/` |

## Endpoints

| Path | Auth | Notes |
|---|---|---|
| `/`, `/index.html` | none | the page, re-rendered every 30 s, `<meta refresh>` every 60 s |
| `/status.json` | none | the model the page is rendered from (`v: 1`, schema in `README.md`) |
| `/feed.xml` | none | Atom feed of the newest 20 incidents |
| `/healthz` | none | `build_sha`, `uptime_s`, `sample_age_s`, `home_age_s`, `last_deploy`, `deploy_error` |
| `/home-status` | none | `200` while pite reported in the last 900 s, else `503`. A DigitalOcean uptime check watches this |
| `/v1/report` | `Authorization: Bearer <token>` | pite's LAN checks, every 30 s |
| `/v1/backfill` | same | pite's one-shot 29-day backfill from Mimir |
| `/v1/snapshot` | same | newest snapshot, for pite's daily pull |

The token's SHA-256 is `[auth] token_sha256` in `config/statusd.toml`. The droplet
stores no plaintext secret.

## What a deploy is

Merging to `main` is the only way to deploy:

1. CI's `test` job runs the suite; `promote` writes the merge SHA into the one-file
   branch `deploy` (`DEPLOY_SHA`); `apply` runs OpenTofu; `verify` waits for
   `/healthz` to report the merge SHA.
2. The droplet's `status-deploy.timer` runs `deploy/pull.sh` every 60 s. It reads
   `DEPLOY_SHA`, requires that commit to exist, and calls `/usr/local/sbin/statusd-apply`.
3. `statusd-apply` validates the new Caddyfile, installs units/config/scripts,
   restarts `statusd` and reloads Caddy, then waits up to 30 s for `/healthz` to
   report the new SHA. On any failure it resets the tree, reinstalls the old
   everything, writes `/var/lib/status/deploy-failed` and exits 1.

A SHA in `deploy-failed` is never retried; the next merge is. `/healthz` reports
`"last_deploy": "failed"` and the reason in `deploy_error` until a good deploy clears it.

## Rolling back by hand

Revert the change on `main` and merge. That is a normal deploy back to the previous
code; the droplet's SQLite history is untouched, so no data is lost. Do not edit
files on the droplet — the next deploy overwrites them.

## When the page is red

`curl -sS https://status-origin.2143.me/healthz` first:

- `"ok": false`, `sample_age_s` null or large → probes are not running: check
  `deploy_error`, then the `probe` loop in the journal (below).
- `last_deploy: "failed"` → a deploy failed and rolled back. The page keeps serving
  the previous build. Read `deploy_error`, fix `main`, merge.
- `"home_age_s": null` or large → pite stopped reporting. The page says so in a
  banner; home components show `unknown`. `home-status` returns 503 and the
  DigitalOcean check alerts by email. Fix pite (`systemctl status status-report.timer`).
- Everything `unknown` → the door is closed, not the sites: check `statusd.service`.

There is no SSH. Break-glass is DigitalOcean's Recovery Console (host console +
`systemctl` via the panel). If the console is unusable, add a temporary inbound TCP 22
rule for the owner's current address in the DigitalOcean console; the droplet already
trusts the `k3s-cloud` key. The next `deploy` run reverts that rule, because the
firewall in `infra/main.tf` is authoritative.

Read logs through the console: `journalctl -u statusd -n 100 --no-pager`,
`journalctl -u status-deploy -n 50`, `journalctl -t statusd-apply -n 50`.

## History durability

Three copies, in order of freshness:

1. **The droplet** — `hourly` kept forever (~10 MB/year).
2. **pite** — pulls `/v1/snapshot` daily at 03:50 UTC into
   `/var/lib/status-snapshots/`, 30 days kept, `pragma quick_check` before it is
   accepted (`status-snapshot.timer` in `dotfiles/nixos/pite-canary.nix`).
3. **DigitalOcean's weekly droplet image** (Sundays 08:00) — the coarse fallback.

Restoring from a snapshot: copy `history-<UTC>.db` to a scratch directory, point a
`statusd` at it (`[paths] db` in a copy of `config/statusd.toml`), start it, and the
page renders that history. The verification step "Restore drill" in the plan is
exactly this procedure.

## Design contract

`designs/live-normal.html` and `designs/live-incident.html` are the production page
rendered from the fixtures in `tests/fixtures/`; the gallery serves them at
`https://status.john2143.com/`. `tests/fixtures/model-*.json` are the schema: if you
change a key, regenerate them with `PYTHONPATH=src python3 tests/make_fixtures.py`
(CI's `render-drift` job fails otherwise).

## Cost

$4 droplet + $0.80 weekly backups + $0 reserved IP (while assigned) + $1 DigitalOcean
uptime check = **$5.80/mo**. Deleting the droplet means deleting `infra/` resources in
one apply; leaving the reserved IP unassigned costs $5/mo.
