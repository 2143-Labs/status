# Testing a change

Every change goes through a pull request. Nothing is tested by editing a live host.

## The loop

1. Branch from `main` in a worktree (`git worktree add .git/omp-worktrees/<name> -b <branch> origin/main`).
2. Run the local checks below. They are the same commands CI runs.
3. Push and open a PR against `main`.
4. CI (`.github/workflows/ci.yml`, on every `pull_request`) runs six jobs:
   `test`, `render-drift`, `caddy`, `scripts`, `tofu-validate`, `designs-js`. All must pass.
5. Within ~2 minutes Argo deploys the PR's own commit to `https://status-<N>.john2143.com/`
   (see "Previews" below). Review the change there, on real live data.
6. Push fixes to the same branch. The preview follows the PR's head commit
   (the generator re-checks every 120 s).
7. Merge (squash). Closing or merging the PR deletes the preview.

## Local checks

Run from the repo root:

    env PYTHONPATH=src python3 -m unittest discover -s tests
    nix run nixpkgs#nodejs_24 -- --test "tests/js/*.test.mjs"
    nix run nixpkgs#kubernetes-helm -- template x . > /tmp/chart.yaml   # chart changes only

To view a page locally, run `python3 -m http.server 8102` and open
`http://127.0.0.1:8102/designs/<page>.html`. Without `designs/estate-live.json` the demos
fall back to the snapshot and say so. On the LAN,
`python3 scripts/estate_snapshot.py --live > designs/estate-live.json` gives them live
data. Never commit that file.

## Previews

- Mechanism: ApplicationSet `status-design-preview` in the argo repo
  (`apps/status-design-preview.yaml`). A pull-request generator on `2143-Labs/status`,
  `requeueAfterSeconds: 120`, renders this repo's Helm chart at the PR's head commit.
- Each PR gets namespace `status-pr-<N>`, route `status-pr-<N>`, host `status-<N>.john2143.com`
  (the shared-gateway `*.john2143.com` wildcard, so no certificate work).
- What it serves: every `designs/*.{html,json,js,css}` from the PR, plus the `live` sidecar's
  `/estate-live.json`. It is the same chart as the gallery, so a preview is exactly what `main` will serve.
- Debugging: `kubectl -n status-pr-<N> get pods`, `kubectl -n status-pr-<N> logs deploy/status-design -c live`
  (the live feed), and `-c nginx` (requests).
- Which commit a preview runs: `kubectl -n argocd get application status-pr-<N> -o jsonpath='{.spec.source.targetRevision}'`
  must equal `git rev-parse origin/<branch>`.

## What merging deploys

- `designs/`, the chart and `scripts/estate_snapshot.py` → the gallery at `https://status.john2143.com/`
  (Argo app `status-design`, tracks `main`, ~2–3 min).
- `src/`, `config/`, `deploy/`, `infra/` → production on the droplet. The `deploy`
  workflow runs on every push to `main`, and the droplet pulls within 60 s. See [runbook.md](runbook.md).
  A preview does **not** exercise these; only CI covers them (`test`, `caddy`, `scripts`, `tofu-validate`).
- Check the gallery: `curl -s https://status.john2143.com/estate-live.json | jq .source` → `"live"`.
- Check production: `curl -s https://status-origin.2143.me/healthz | jq -r .build_sha` → the merge commit.
