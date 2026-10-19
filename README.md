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
3. Review at `https://status-<PR number>.john2143.com/<name>.html`.
4. Merge. It is live at `https://status.john2143.com/<name>.html`.

Design rules: self-contained single file (no external requests, no fonts, no
CDN), works without JavaScript, light and dark via `prefers-color-scheme`,
status never colour-only, and no IP addresses or internal hostnames.

## Layout

```
Chart.yaml            chart metadata
values.yaml           namespace, host, route name, nginx image
templates/            configmap · deployment · service · httproute · referencegrant
designs/              the pages (index.html is the gallery)
```
