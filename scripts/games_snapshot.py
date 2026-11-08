#!/usr/bin/env python3
"""Build the `games` v1 feed for the design gallery's games tracker.

    MIMIR_URL=... python3 scripts/games_snapshot.py > /tmp/games-live.json

One line of JSON on stdout: ten instant Mimir queries (replica counts, Beyla
request/5xx rates, namespace phases, ArgoCD application health), the in-cluster
ROTS fleet summary and GitHub's open pull requests per game repo. The `games`
container in the status chart runs this every 60 s and mirrors the file to
/games-live.json; designs/games.html renders it. stdlib only.

Importing this module does no I/O: the registry is data, every network call is
behind a function, and `build` takes already-fetched results so tests can feed
fixtures.

Exit 1: the sanitiser found a public IP-like value.
Exit 2: Mimir was unreachable (the sidecar keeps the last good file).
Exit 3: output over 80,000 bytes.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

MIMIR = os.environ.get("MIMIR_URL", "http://192.168.6.23:8080/prometheus/api/v1/query")
GITHUB = os.environ.get("GITHUB_API", "https://api.github.com")
TOKEN_FILE = os.environ.get("GITHUB_TOKEN_FILE")
FLEET_URL = os.environ.get("ROTS_FLEET_URL", "http://rots-authserver.rots-prod.svc/api/v1/fleet")
INTERVAL_S = 60
QUAD = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.-])")
# Always-on edge pods in every hosted-game namespace; counting them would make
# every preview look healthy.
EDGE_DEPLOYMENTS = ("anubis", "anubis-ws")

# Ten instant queries, each across all namespaces at once. A missing series is
# null, never 0: a destroyed instance must not look like a scaled-to-zero one.
QUERIES = {
    "dep_ready": "kube_deployment_status_replicas_available",
    "dep_desired": "kube_deployment_spec_replicas",
    "dep_unavail": "kube_deployment_status_replicas_unavailable",
    "sts_ready": "kube_statefulset_status_replicas_ready",
    "pod_ready": 'kube_pod_status_ready{condition="true"}',
    "pod_running": 'kube_pod_status_phase{phase="Running"} == 1',
    "ns_active": 'kube_namespace_status_phase{phase="Active"} == 1',
    "http_5xx": 'sum by (service_namespace, service_name) '
                '(rate(http_server_request_duration_count{http_response_status_code=~"5.."}[30m]))',
    "http_total": "sum by (service_namespace, service_name) (rate(http_server_request_duration_count[30m]))",
    "argo": "argocd_app_info",
}


def deploy(label, target, role, min_=None, max_=None, scale_to_zero=False):
    """`label` is what the card shows; `target` is the Deployment k8s name."""
    return {"name": label, "role": role, "probe": {"kind": "deployment", "name": target},
            "scale_to_zero": scale_to_zero, "min": min_, "max": max_}


def sts(name, prefix, role):
    return {"name": name, "role": role, "probe": {"kind": "statefulsets", "prefix": prefix},
            "scale_to_zero": False, "min": None, "max": None}


def pods(name, prefix, role):
    return {"name": name, "role": role, "probe": {"kind": "pods", "prefix": prefix},
            "scale_to_zero": False, "min": None, "max": None}


# Data-as-code registry: adding a game is an entry here plus the same id in
# designs/games-sample.json. `argo` names the ArgoCD Application per env.
GAMES = [
    {"id": "rots", "name": "Revenge of the Sunfish", "kind": "game",
     "url": "https://rots.john2143.com", "repo": "2143-Labs/rots4",
     "preview": {"ns": "rots-pr-{n}", "url": "https://rots-{n}.john2143.com"},
     "envs": [{"id": "prod", "name": "Production", "ns": "rots-prod", "url": "https://rots.john2143.com",
               "argo": "rots-web", "fleet": {"url": FLEET_URL},
               "services": [deploy("web", "rots-web", "web", 1, 1),
                            deploy("authserver", "rots-authserver", "api", 1, 3),
                            sts("game servers", "rots-gs-", "game"),
                            pods("postgres", "rots-db-", "db")]}]},
    {"id": "dino", "name": "Dino Dash (PRIMAL)", "kind": "game",
     "url": "https://dino.dsstar.us", "repo": "2143-Labs/Dino-Dash",
     "preview": {"ns": "dino-pr-{n}", "url": "https://dino-{n}.dsstar.us"},
     "envs": [{"id": "prod", "name": "Production", "ns": "dino-prod", "url": "https://dino.dsstar.us",
               "argo": "dino-prod",
               "services": [deploy("world server", "dino-dash-server", "game", scale_to_zero=True),
                            deploy("web", "dino-dash-web", "web", scale_to_zero=True)]},
              {"id": "dev", "name": "Dev", "ns": "dino-dev", "url": "https://dino-dev.dsstar.us",
               "argo": "dino-dev",
               "services": [deploy("world server", "dino-dash-server", "game", scale_to_zero=True),
                            deploy("web", "dino-dash-web", "web", scale_to_zero=True)]}]},
    {"id": "moss", "name": "Moss & Wool", "kind": "game",
     "url": "https://moss.dsstar.us", "repo": "2143-Labs/Moss-And-Wool",
     "preview": {"ns": "moss-pr-{n}", "url": "https://moss-{n}.dsstar.us"},
     "envs": [{"id": "prod", "name": "Production", "ns": "moss-prod", "url": "https://moss.dsstar.us",
               "argo": "moss-prod",
               "services": [deploy("web", "moss-and-wool", "web", scale_to_zero=True)]},
              {"id": "dev", "name": "Dev", "ns": "moss-dev", "url": "https://moss-dev.dsstar.us",
               "argo": "moss-dev",
               "services": [deploy("web", "moss-and-wool", "web", scale_to_zero=True)]}]},
    {"id": "steam-lobby", "name": "Steam Lobby (UMVC3)", "kind": "game",
     "url": "https://pvp.john2143.com", "repo": "2143-Labs/steam-lobby",
     "preview": {"ns": "steam-lobby-pr-{n}", "url": "https://pvp-{n}.john2143.com"},
     "envs": [{"id": "prod", "name": "Production", "ns": "steam-lobby", "url": "https://pvp.john2143.com",
               "argo": "steam-lobby",
               "services": [deploy("lobby", "steam-lobby", "web"),
                            deploy("coturn", "coturn", "relay"),
                            pods("db", "steam-lobby-db-", "db")]}]},
    {"id": "how-poe-sale", "name": "how.poe.sale", "kind": "tool",
     "url": "https://how.john2143.com", "repo": "2143-Labs/how.poe.sale",
     "preview": {"ns": "how-poe-sale-pr-{n}", "url": "https://how-{n}.john2143.com"},
     "envs": [{"id": "prod", "name": "Production", "ns": "how-poe-sale", "url": "https://how.john2143.com",
               "argo": "how-poe-sale",
               "services": [deploy("web", "how-poe-sale", "web")]}]},
    {"id": "factorio", "name": "Factorio", "kind": "server",
     "url": "https://factorio.john2143.com", "repo": None,
     "envs": [{"id": "prod", "name": "Production", "ns": "default", "url": "https://factorio.john2143.com",
               "argo": "factorio", "services": [deploy("game", "factorio", "game")]}]},
    {"id": "minecraft", "name": "Minecraft", "kind": "server",
     "url": None, "repo": None,
     "envs": [{"id": "prod", "name": "Production", "ns": "default", "url": None,
               "argo": "minecraft", "services": [deploy("game", "minecraft", "game")]}]},
    {"id": "teamspeak", "name": "TeamSpeak", "kind": "server",
     "url": None, "repo": None,
     "envs": [{"id": "prod", "name": "Production", "ns": "default", "url": None,
               "argo": "teamspeak", "services": [deploy("voice", "teamspeak", "voice")]}]},
    {"id": "openrct2", "name": "OpenRCT2", "kind": "server",
     "url": None, "repo": None,
     "envs": [{"id": "prod", "name": "Production", "ns": "default", "url": None,
               "argo": "openrct2", "services": [deploy("game", "openrct2", "game")]}]},
    {"id": "pelican", "name": "Pelican", "kind": "server",
     "url": "https://pelican.john2143.com", "repo": None,
     "envs": [{"id": "prod", "name": "Production", "ns": "default", "url": "https://pelican.john2143.com",
               "argo": "pelican",
               "services": [deploy("panel", "pelican", "panel"),
                            pods("db", "pelican-db-", "db")]}]},
]


def get(url, timeout=10, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def q(expr):
    d = get(MIMIR + "?" + urllib.parse.urlencode({"query": expr}))
    if d.get("status") != "success":
        raise RuntimeError(f"mimir: {expr}: {d}")
    return d["data"]["result"]


def is_public(a, b, c, d):
    o = [int(x) for x in (a, b, c, d)]
    if any(x > 255 for x in o):
        return False  # not an address (a version string)
    return not (o[0] in (10, 127) or (o[0] == 192 and o[1] == 168))


def public_ips(text):
    return [m.group(0) for m in QUAD.finditer(text) if is_public(*m.groups())]


def redact_ips(text):
    """PR titles are third-party text: one `8.8.8.8` must not freeze the feed."""
    return QUAD.sub(lambda m: "x.x.x.x" if is_public(*m.groups()) else m.group(0), text)


# --- pure state functions ---------------------------------------------------

def service_state(ready, desired, scale_to_zero):
    if ready is None or desired is None:
        return "unknown"
    if desired > 0 and ready == 0:
        return "down"
    if 0 < ready < desired:
        return "degraded"
    if desired == 0 and scale_to_zero:
        return "asleep"
    if desired > 0 and ready >= desired:
        return "up"
    if desired == 0 and ready == 0:
        return "off"
    return "unknown"


ENV_ORDER = ("down", "degraded", "up", "asleep", "off")


def env_state(services, argo_health):
    states = {s.get("state") for s in services}
    state = next((k for k in ENV_ORDER if k in states), "unknown")
    if state == "up":
        # Below 0.05 req/s a single 5xx from a scale-to-zero wake-up would flip
        # the card, so a hot error ratio only counts with real traffic behind it.
        hot = any((s.get("err_ratio") or 0) > 0.05 and (s.get("req_rate") or 0) >= 0.05 for s in services)
        if hot or argo_health not in (None, "Healthy"):
            state = "degraded"
    return state


def game_state(envs):
    prod = next((e for e in envs if e.get("id") == "prod"), None)
    if prod is not None:
        return prod["state"]
    return next((k for k in ENV_ORDER + ("unknown",) if any(e["state"] == k for e in envs)), "unknown")


def preview_state(ns_active, deployments):
    """`deployments` are {name, available, unavailable} for one preview namespace."""
    if not ns_active:
        return None
    live = [d for d in deployments if d.get("name") not in EDGE_DEPLOYMENTS]
    if any((d.get("unavailable") or 0) > 0 for d in live):
        return "down"
    if any((d.get("available") or 0) > 0 for d in live):
        return "up"
    if live:
        return "asleep"
    return "unknown"


# --- indexing ---------------------------------------------------------------

def by_label(result, label):
    out = {}
    for s in result:
        m = s["metric"]
        ns, name = m.get("namespace"), m.get(label)
        if ns and name:
            out[(ns, name)] = float(s["value"][1])
    return out


def by_service(result):
    out = {}
    for s in result:
        m = s["metric"]
        ns, name = m.get("service_namespace"), m.get("service_name")
        if ns and name:
            out[(ns, name)] = float(s["value"][1])
    return out


def argo_index(result):
    out = {}
    for s in result:
        m = s["metric"]
        name = m.get("name")
        if name:
            out[name] = {"app": name, "sync": m.get("sync_status"), "health": m.get("health_status")}
    return out


def _int(v):
    return None if v is None else int(round(v))


def probe_counts(probe, ns, idx):
    kind = probe["kind"]
    if kind == "deployment":
        key = (ns, probe["name"])
        return _int(idx["dep_ready"].get(key)), _int(idx["dep_desired"].get(key))
    if kind == "statefulsets":
        vals = [v for (n, name), v in idx["sts"].items() if n == ns and name.startswith(probe["prefix"])]
        return (int(round(sum(vals))), len(vals)) if vals else (None, None)
    if kind == "pods":
        ready = [v for (n, name), v in idx["pod_ready"].items() if n == ns and name.startswith(probe["prefix"])]
        running = [1 for (n, name) in idx["pod_running"] if n == ns and name.startswith(probe["prefix"])]
        if not ready and not running:
            return None, None
        return int(round(sum(ready))), len(running)
    raise ValueError(f"unknown probe kind: {kind}")


def rates(key, idx):
    total = idx["http_total"].get(key)
    if total is None:
        return None, None
    bad = idx["http_5xx"].get(key, 0.0)
    return (round(bad / total, 4) if total > 0 else 0.0), round(total, 3)


def namespace_deployments(idx, ns):
    names = {name for (n, name) in idx["dep_ready"] if n == ns}
    names |= {name for (n, name) in idx["dep_unavail"] if n == ns}
    return [{"name": name,
             "available": idx["dep_ready"].get((ns, name)) or 0,
             "unavailable": idx["dep_unavail"].get((ns, name)) or 0}
            for name in sorted(names)]


def epoch(iso):
    if not iso:
        return 0
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def pr_entry(repo, raw):
    """Only the number, title, draft flag and timestamp; URL is rebuilt locally."""
    number = int(raw["number"])
    return {"number": number,
            "title": redact_ips(str(raw.get("title") or "")),
            "draft": bool(raw.get("draft")),
            "url": f"https://github.com/{repo}/pull/{number}",
            "updated": epoch(raw.get("updated_at"))}


# --- assembly ---------------------------------------------------------------

def build(metrics, prs=None, now=0, source="live", github_error=None, fleets=None, registry=None):
    """Assemble the v1 document from already-fetched data. No I/O, no clock."""
    prs = prs or {}
    fleets = fleets or {}
    registry = GAMES if registry is None else registry
    metrics = metrics or {}
    idx = {
        "dep_ready": by_label(metrics.get("dep_ready", []), "deployment"),
        "dep_desired": by_label(metrics.get("dep_desired", []), "deployment"),
        "dep_unavail": by_label(metrics.get("dep_unavail", []), "deployment"),
        "sts": by_label(metrics.get("sts_ready", []), "statefulset"),
        "pod_ready": by_label(metrics.get("pod_ready", []), "pod"),
        "pod_running": by_label(metrics.get("pod_running", []), "pod"),
        "http_5xx": by_service(metrics.get("http_5xx", [])),
        "http_total": by_service(metrics.get("http_total", [])),
    }
    active = {s["metric"].get("namespace") for s in metrics.get("ns_active", [])}
    argo = argo_index(metrics.get("argo", []))

    games = []
    for g in registry:
        envs = []
        for env in g["envs"]:
            services = []
            for svc in env["services"]:
                ready, desired = probe_counts(svc["probe"], env["ns"], idx)
                err_ratio = req_rate = None
                if svc["probe"]["kind"] == "deployment":
                    err_ratio, req_rate = rates((env["ns"], svc["probe"]["name"]), idx)
                services.append({"name": svc["name"], "role": svc["role"], "ready": ready,
                                 "desired": desired, "min": svc["min"], "max": svc["max"],
                                 "state": service_state(ready, desired, svc["scale_to_zero"]),
                                 "err_ratio": err_ratio, "req_rate": req_rate})
            app = env.get("argo")
            app_info = argo.get(app) if app else None
            fleet = fleets.get((env.get("fleet") or {}).get("url")) if env.get("fleet") else None
            envs.append({"id": env["id"], "name": env["name"], "ns": env["ns"], "url": env["url"],
                         "state": env_state(services, (app_info or {}).get("health")),
                         "argo": app_info, "services": services, "fleet": fleet})

        prod = next((e for e in envs if e["id"] == "prod"), None)
        counted = prod["services"] if prod is not None else [s for e in envs for s in e["services"]]
        servers = sum((s["ready"] or 0) for s in counted if s["role"] in ("game", "db"))

        game_prs = []
        for raw in (prs.get(g["repo"]) or []) if g.get("repo") else []:
            entry = pr_entry(g["repo"], raw)
            preview = g.get("preview")
            if preview:
                ns = preview["ns"].format(n=entry["number"])
                state = preview_state(ns in active, namespace_deployments(idx, ns))
                entry["preview"] = ({"url": preview["url"].format(n=entry["number"]), "ns": ns, "state": state}
                                    if state else None)
            else:
                entry["preview"] = None
            game_prs.append(entry)

        games.append({"id": g["id"], "name": g["name"], "kind": g["kind"], "url": g["url"],
                      "repo": g.get("repo"), "state": game_state(envs), "servers": servers,
                      "envs": envs, "prs": game_prs})

    return {"v": 1, "source": source, "generated": int(now), "interval_s": INTERVAL_S,
            "github": {"ok": github_error is None, "error": github_error}, "games": games}


# --- fetching ---------------------------------------------------------------

def fetch_metrics():
    return {name: q(expr) for name, expr in QUERIES.items()}


def read_token():
    """Re-read on every run: the rotation procedure needs no restart."""
    if not TOKEN_FILE:
        return None
    try:
        token = Path(TOKEN_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def github_prs(token, registry=None):
    """Open PRs per repo; a failure for one repo is recorded, never fatal."""
    registry = GAMES if registry is None else registry
    headers = {"Accept": "application/vnd.github+json",
               "Authorization": f"Bearer {token}",
               "User-Agent": "2143-status-tracker",
               "X-GitHub-Api-Version": "2026-03-10"}
    out, errors = {}, []
    for repo in sorted({g["repo"] for g in registry if g.get("repo")}):
        url = f"{GITHUB}/repos/{repo}/pulls?state=open&sort=updated&direction=desc&per_page=100"
        try:
            data = get(url, headers=headers)
        except urllib.error.HTTPError as exc:
            errors.append(f"{repo}: {exc.code}")
            continue
        except Exception as exc:  # DNS, timeout, bad JSON
            errors.append(f"{repo}: {type(exc).__name__}")
            continue
        out[repo] = data if isinstance(data, list) else []
    return out, ("; ".join(errors) or None)


def fetch_fleet(url):
    """Counts only; an unreachable authserver is advisory (sts count is the truth)."""
    try:
        d = get(url, timeout=5)
        return {k: int(d.get(k) or 0) for k in ("slots", "instances", "ready", "players", "warm")}
    except Exception:
        return None


def main():
    try:
        metrics = fetch_metrics()
    except Exception as exc:  # unreachable source, bad response
        print(f"games_snapshot: mimir: {exc}", file=sys.stderr)
        return 2

    token = read_token()
    if token is None:
        prs, github_error = {}, "no token"
    else:
        try:
            prs, github_error = github_prs(token)
        except Exception as exc:  # never abort the feed on GitHub
            prs, github_error = {}, f"github: {exc}"

    fleets = {}
    for g in GAMES:
        for env in g["envs"]:
            url = (env.get("fleet") or {}).get("url")
            if url and url not in fleets:
                fleets[url] = fetch_fleet(url)

    doc = build(metrics, prs, now=int(time.time()), github_error=github_error, fleets=fleets)
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    bad = public_ips(text)
    if bad:
        print(f"games_snapshot: refusing to write, public IP-like values: {sorted(set(bad))}", file=sys.stderr)
        return 1
    if len(text.encode()) > 80000:
        print(f"games_snapshot: output {len(text.encode())} bytes > 80000", file=sys.stderr)
        return 3
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
