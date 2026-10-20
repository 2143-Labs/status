# status

Status page designs, and later the status service itself.

| URL | What |
|---|---|
| `https://status.john2143.com/` | gallery of every merged design (tracks `main`) |
| `https://status.john2143.com/board.html` | the uptime board (also `/map.html`, `/doc.html`) |
| `https://status-{N}.john2143.com/` | preview of PR `{N}` (home cluster) |
| `https://status.2143.me/` | **production** — not deployed from this repo (served from pite today) |

## How a preview works

There is no image build. This repo is a Helm chart, and every `designs/*.html`
and `designs/*.json` becomes a key in a ConfigMap, flattened to its file name:

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
CDN), light and dark via `prefers-color-scheme`, status never colour-only, and
no IP addresses or internal hostnames. A page that needs JavaScript must say so
in a `<noscript>` and link its raw data.

## The board's data: `status.json`

`board.html` fetches `status.json` from its own origin every 30 s. When that file
is missing (as it is here) it falls back to `sample-normal.json` and shows a
scenario switch: `sample-{normal,incident,home-offline,stale}.json`. The samples
are real probe history (about 29 days, recorded 2026-10-20) with the incident
states edited in. Whatever produces the live file only has to emit this shape:

```jsonc
{
  "v": 1,
  "generated": 1792479000,          // unix seconds, when this file was written
  "tz": "America/New_York",         // day buckets and every displayed time use this
  "probe_interval_s": 30,
  "home_age_s": 12,                 // seconds since the home monitor last reported; null if never
  "groups": [{"id": "web", "name": "Websites", "note": "Public sites, checked every 30 seconds."}],
  "days_start": "2026-07-23",       // date of daily_pct[0]; arrays end today (partial day)
  "hours_start": 1791874800,        // unix start of hourly_pct[0]; arrays end with the current hour
  "latency_start": 1792392600,      // unix start of latency_10m[0]
  "components": [{
    "id": "files-john2143-com", "name": "files.john2143.com", "group": "web",
    "hosted": "home",               // "home" | "cloud"
    "url": "https://files.john2143.com",   // optional, https only
    "state": "up",                  // "up" | "degraded" | "down" | "unknown"
    "latency_ms": 40, "cert_days": 89,     // null when not applicable
    "uptime": {"24h": 99.63, "7d": 91.55, "30d": 95.82, "90d": 95.82},
    "since": 1790578200,            // first sample; ranges longer than this say "N days of data"
    "daily_pct": [null, "...", 99.8],      // 90 values
    "daily_down_min": [null, "...", 3],    // 90 values
    "hourly_pct": ["...168 values..."],
    "latency_10m": ["...144 values, ms..."]
  }],
  "outages": [{"id": "files-john2143-com", "start": 1792478580, "end": null}]
}
```

Rules the page relies on:

- `null` means no data and is drawn grey and short. It is never treated as up.
- An outage is two or more failed checks in a row; `end: null` means ongoing.
  The page merges outages that start within 5 minutes of each other into one
  incident and tucks incidents under 5 minutes behind a "blips" toggle.
- `degraded` is the producer's call (for example, latency far above normal).
- If `home_age_s` is over 120 s and every `hosted: "home"` component is down or
  unknown, the headline becomes "Home looks offline". If `generated` is over
  120 s old, it becomes "Status data is out of date".
- URL state is in the hash: `#range=7d&open=files-john2143-com&issues=1&scenario=incident`.
- `sample_age_s` exists only in the sample files. It pins the displayed data age
  so the Stale scenario looks stale; a live producer leaves it out.

## Layout

```
Chart.yaml            chart metadata
values.yaml           namespace, host, route name, nginx image
templates/            configmap · deployment · service · httproute · referencegrant
designs/              the pages (index.html is the gallery) and the board's sample data
files/nginx.conf      one worker; JSON served with Cache-Control: no-cache
```
