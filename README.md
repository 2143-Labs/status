# status

Status page designs, and later the status service itself.

| URL | What |
|---|---|
| `https://status.john2143.com/` | gallery of every merged design (tracks `main`) |
| `https://status.john2143.com/map.html` · `/doc.html` | individual designs |
| `https://status-{N}.john2143.com/` | preview of PR `{N}` (home cluster) |
| `https://status.2143.me/` | **production** — separate host, its own droplet |

## How a preview works

There is no image build. This repo is a Helm chart, and every `designs/*.html`
becomes a key in a ConfigMap, flattened to its file name:

```yaml
data:
  map.html: | ...
  doc.html: | ...
```

An ApplicationSet in `argo` (`apps/status-design-preview.yaml`) watches this repo
with the pull-request generator and, per open PR, renders the chart at
`targetRevision: {head_sha}` into namespace `status-pr-{N}` behind
`https://status-{N}.john2143.com`. Closing the PR prunes the namespace, the
route and the page.

A second Application (`apps/status-design.yaml`) renders the same chart from
`main` into `status-design`, which is the gallery.

Both routes live in the `default` namespace so they can use the shared
`public-rate-limit`, `public-inflight-limit` and `crowdsec-bouncer` filters;
a ReferenceGrant lets them reach the Service in the release namespace.

## Adding a design

1. Drop `designs/<name>.html` in a PR. Link it from `designs/index.html`.
2. Open the PR; the bot-applied Application appears within ~2 minutes.
3. Review at `https://status-<PR number>.john2143.com/<name>.html` (how previews work and how to test any change: [docs/testing.md](docs/testing.md)).
4. Merge. It is live at `https://status.john2143.com/<name>.html`.

Design rules: self-contained single file (no external requests, no fonts, no
CDN), works without JavaScript, light and dark via `prefers-color-scheme`,
status never colour-only, and no IP addresses or internal hostnames.

## Live data

The gallery pod runs a `live` sidecar that executes `scripts/estate_snapshot.py --live`
every 30 s and writes `/estate-live.json` (components from the droplet's public
`status.json`, machines and pods from the home Mimir at `192.168.6.23:8080`). Demo pages
load it by default and re-fetch it every 30 s; if it is missing they fall back to
`estate-real.json`, then the simulator, and say so. `?source=sim` forces the simulator.
Only the `today` layout exists in real data, so Target needs `?source=sim`.
Check it: `curl -s https://status.john2143.com/estate-live.json | jq .generated`.

## Games feed

`games.html` shows one card per 2143-Labs game: live state, each environment's
services with ready/desired replicas (ROTS per microservice), and every open PR
with its preview environment. A second sidecar, `games`, runs
`scripts/games_snapshot.py` every 3 s and writes `/games-live.json` from three
sources:

- the home Mimir: replicas (kube-state-metrics), request and 5xx rates (Beyla),
  preview namespaces, and ArgoCD sync/health (`argocd_app_info`, scraped by Alloy
  in the `argo` repo);
- the ROTS fleet summary, in-cluster at
  `http://rots-authserver.rots-prod.svc/api/v1/fleet` (optional: the card falls
  back to StatefulSet counts);
- GitHub's open pull requests, with a read-only token mounted from Secret
  `status-github`.

The page re-fetches on the feed's `interval_s` (3 s today). If the feed is missing it shows
`games-sample.json` and says so; `?source=sample` forces the sample.

Feed shape (v1): `{v, source, generated, interval_s, github: {ok, error}, games: [...]}`.
Each game has `id, name, kind, url, repo, state, servers, envs, prs`; each env
has `services` (`ready`, `desired`, `state`, `err_ratio`, `req_rate`), optional
`argo` and `fleet`; each PR has `number, title, draft, url, updated, preview`.
States are `up` (ready ≥ desired), `degraded` (some replicas missing, a 5xx ratio
over 5 % with real traffic, or ArgoCD not Healthy), `down` (wanted but none
ready), `asleep` (scaled to zero on purpose), `off` (0 replicas, not a
scale-to-zero service) and `unknown` (no data).

Add a game: an entry in `GAMES` in `scripts/games_snapshot.py`, plus the same id
in `designs/games-sample.json` (a test checks they match). Private-repo PR titles
are shown on purpose.

The token is a fine-grained PAT (2143-Labs; Metadata and Pull requests read) at
OpenBao `consumers` → `john2143-com/status-design/github-pr-reader`, key
`token`. The chart renders the ExternalSecret only when `games.githubKey` is set,
which the `argo` repo does for the gallery only; previews get no token and show
PRs as unavailable. Creating and rotating it: `docs/games-tracker.md` in the
`argo` repo. To force a refresh after rotating:
`kubectl -n status-design annotate externalsecret status-github force-sync=$(date +%s) --overwrite`.
The sidecar re-reads the token file on every run.

Check it: `curl -s https://status.john2143.com/games-live.json | jq '.generated, .github'`.
Failures only: `kubectl -n status-design logs deploy/status-design -c games`.

## Layout

```
Chart.yaml            chart metadata
values.yaml           namespace, host, route name, nginx image, sidecar image, games feed
templates/            configmap · deployment · service · httproute · referencegrant · github-token
designs/              the pages (index.html is the gallery; games.html + games-core.js + games-sample.json)
scripts/              estate_snapshot.py (live sidecar), games_snapshot.py (games sidecar)
```

How production runs (endpoints, deploys, rollback, where the history lives):
[`docs/runbook.md`](docs/runbook.md).
