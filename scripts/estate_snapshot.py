#!/usr/bin/env python3
"""Build a real `estate` v2 snapshot for the design demos.

    python3 scripts/estate_snapshot.py > designs/estate-real.json
    python3 scripts/estate_snapshot.py --live   # source "live", used by the gallery sidecar

Reads the public status.json (components, history) and the home Mimir (LAN only:
node specs/utilisation, pod placement and requests). Topology (providers, sites,
stations, hops, routes) is the `today` layout from designs/estate-topology.json; hop latency is
not measured yet, so hops are null/unknown. stdlib only.

Exit 1: sanitiser found a public IP. Exit 2: a source was unreachable.
Exit 3: output over 80,000 bytes even with 3 workloads per node.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

STATUS = os.environ.get("STATUS_URL", "https://status-origin.2143.me/status.json")
MIMIR = os.environ.get("MIMIR_URL", "http://192.168.6.23:8080/prometheus/api/v1/query")
HOUSE = ["closet", "arch", "nas", "big", "office", "pite"]
NODE_META = {  # site, role, arch, lan_ip
    "closet": ("server", "amd64", "192.168.5.36"),
    "arch": ("server", "amd64", "192.168.5.76"),
    "nas": ("server", "amd64", "192.168.5.175"),
    "big": ("agent", "amd64", "192.168.5.68"),
    "office": ("agent", "amd64", "192.168.5.209"),
    "pite": ("agent", "arm64", "192.168.5.9"),
}
CLOUD_NODES = {"doks-1": "doks", "status-1": "do"}  # topology node id -> Mimir cluster label
CLOUD_DEV = 'device!~"lo|veth.*|cni.*|flannel.*|lxc.*|cilium.*|anchor|cpbridge"'
TOPO_PATH = os.environ.get("ESTATE_TOPOLOGY") or Path(__file__).resolve().parent.parent / "designs" / "estate-topology.json"
TOPO = json.loads(Path(TOPO_PATH).read_text(encoding="utf-8"))
COMPONENT_ROUTE = {"john2143-com": "home-direct", "files-john2143-com": "home-direct", "2143-me": "do-only",
                   "2143-me-user": "do-only", "i-2143-me": "images", "temporal": "temporal-grpc"}
QUAD = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.-])")


def get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def q(expr):
    d = get(MIMIR + "?" + urllib.parse.urlencode({"query": expr}))
    if d.get("status") != "success":
        raise RuntimeError(f"mimir: {expr}: {d}")
    return d["data"]["result"]


def host(instance):
    h = instance.split(":")[0]
    return "pite" if h == "localhost" else h.removesuffix(".local")


def per_host(expr):
    out = {}
    for s in q(expr):
        h = host(s["metric"].get("instance", ""))
        if h in HOUSE:
            out[h] = float(s["value"][1])
    return out


def per_cloud(expr):
    out = {}
    for s in q(expr):
        n = s["metric"].get("node", "")
        if n in CLOUD_NODES:
            out[n] = (float(s["value"][1]), s["metric"])
    return out


def is_public(a, b, c, d):
    o = [int(x) for x in (a, b, c, d)]
    if any(x > 255 for x in o):
        return False  # not an address (a version string)
    return not (o[0] in (10, 127) or (o[0] == 192 and o[1] == 168))


def public_ips(text):
    return [m.group(0) for m in QUAD.finditer(text) if is_public(*m.groups())]


def base_name(pod):
    pod = re.sub(r"-[a-f0-9]{8,10}-[a-z0-9]{5}$", "", pod)
    pod = re.sub(r"-[a-z0-9]{5}$", "", pod)
    return re.sub(r"-\d+$", "", pod)


def build(per_node, live=False):
    status = get(STATUS)
    now = int(time.time())
    cores = {h: int(v) for h, v in per_host('count by (instance)(node_cpu_seconds_total{mode="idle",job="home-nodes"})').items()}
    mem = per_host('node_memory_MemTotal_bytes{job="home-nodes"}')
    disk = per_host('node_filesystem_size_bytes{mountpoint="/",job="home-nodes"}')
    cpu = per_host('100 - (avg without(cpu,mode)(rate(node_cpu_seconds_total{mode="idle",job="home-nodes"}[5m])) * 100)')
    ram = per_host('(1 - node_memory_MemAvailable_bytes{job="home-nodes"}/node_memory_MemTotal_bytes{job="home-nodes"}) * 100')
    dsk = per_host('(1 - node_filesystem_avail_bytes{mountpoint="/",job="home-nodes"}/node_filesystem_size_bytes{mountpoint="/",job="home-nodes"}) * 100')
    load = per_host('node_load1{job="home-nodes"}')
    rx = per_host('sum without(device)(rate(node_network_receive_bytes_total{job="home-nodes",device!~"lo|veth.*|cni.*|flannel.*"}[5m]))')
    tx = per_host('sum without(device)(rate(node_network_transmit_bytes_total{job="home-nodes",device!~"lo|veth.*|cni.*|flannel.*"}[5m]))')

    nodes = []
    for h in HOUSE:
        role, arch, ip = NODE_META[h]
        seen = h in load
        pct = [round(x.get(h, 0), 1) for x in (cpu, ram, dsk)]
        nodes.append(dict(
            id=h, name=h, site="house", role=role, arch=arch, lan_ip=ip,
            cpu_cores=cores.get(h, 0), ram_bytes=int(mem.get(h, 0)), disk_bytes=int(disk.get(h, 0)),
            cpu_pct=pct[0], ram_pct=pct[1], disk_pct=pct[2],
            net_rx_bps=round(rx.get(h, 0)), net_tx_bps=round(tx.get(h, 0)),
            load1=round(load.get(h, 0), 2), temp_c=None,
            state=("degraded" if max(pct) >= 90 else "up") if seen else "unknown",
            age_s=0 if seen else None, public_ip_locked=False))
    for t in TOPO["nodes"]:
        if t[0] == "home-pi":  # standalone on the Verizon LAN; not scraped, so unmeasured
            nodes.insert(0, dict(id=t[0], name=t[0], site=t[1], role=t[2], arch=t[3], lan_ip=t[4], cpu_cores=t[5],
                                 ram_bytes=t[6], disk_bytes=t[7], cpu_pct=0, ram_pct=0, disk_pct=0, net_rx_bps=0,
                                 net_tx_bps=0, load1=0, temp_c=None, state="unknown", age_s=None, public_ip_locked=t[8]))
    sel = 'cluster=~"do|doks"'
    c_cores = per_cloud(f'count by (node)(node_cpu_seconds_total{{mode="idle",{sel}}})')
    c_mem = per_cloud(f'node_memory_MemTotal_bytes{{{sel}}}')
    c_disk = per_cloud(f'node_filesystem_size_bytes{{mountpoint="/",{sel}}}')
    c_cpu = per_cloud(f'100 - (avg by (node)(rate(node_cpu_seconds_total{{mode="idle",{sel}}}[5m])) * 100)')
    c_ram = per_cloud(f'(1 - node_memory_MemAvailable_bytes{{{sel}}}/node_memory_MemTotal_bytes{{{sel}}}) * 100')
    c_dsk = per_cloud(f'(1 - node_filesystem_avail_bytes{{mountpoint="/",{sel}}}/node_filesystem_size_bytes{{mountpoint="/",{sel}}}) * 100')
    c_load = per_cloud(f'node_load1{{{sel}}}')
    c_rx = per_cloud(f'sum by (node)(rate(node_network_receive_bytes_total{{{sel},{CLOUD_DEV}}}[5m]))')
    c_tx = per_cloud(f'sum by (node)(rate(node_network_transmit_bytes_total{{{sel},{CLOUD_DEV}}}[5m]))')
    c_name = per_cloud(f'node_uname_info{{{sel}}}')
    for t in TOPO["nodes"]:
        if t[0] not in CLOUD_NODES:
            continue
        n = t[0]
        val = lambda d, default=0: d[n][0] if n in d else default  # noqa: E731
        seen = n in c_load
        pct = [round(val(x), 1) for x in (c_cpu, c_ram, c_dsk)]
        nodes.append(dict(
            id=n, name=c_name[n][1].get("nodename", n) if n in c_name else n, site=t[1], role=t[2], arch=t[3],
            lan_ip=t[4], cpu_cores=int(val(c_cores, t[5])), ram_bytes=int(val(c_mem, t[6])),
            disk_bytes=int(val(c_disk, t[7])), cpu_pct=pct[0], ram_pct=pct[1], disk_pct=pct[2],
            net_rx_bps=round(val(c_rx)), net_tx_bps=round(val(c_tx)), load1=round(val(c_load), 2), temp_c=None,
            state=("degraded" if max(pct) >= 90 else "up") if seen else "unknown",
            age_s=0 if seen else None, public_ip_locked=t[8]))

    placed = set(HOUSE) | {"doks-1"}
    pod_node = {}
    for s in q('kube_pod_info{node!=""}'):
        m = s["metric"]
        if m.get("node") in placed:
            pod_node[(m["namespace"], m["pod"])] = m["node"]
    req = {}
    for s in q('sum by (namespace,pod,resource) (kube_pod_container_resource_requests)'):
        m = s["metric"]
        key = (m["namespace"], m["pod"])
        if key in pod_node:
            r = req.setdefault(key, {"cpu": 0.0, "memory": 0.0})
            if m.get("resource") in r:
                r[m["resource"]] += float(s["value"][1])

    workloads = []
    for h in HOUSE + ["doks-1"]:
        pods = sorted((k for k, n in pod_node.items() if n == h),
                      key=lambda k: (-req.get(k, {}).get("memory", 0), k))
        for i, (ns, pod) in enumerate(pods[:per_node]):
            r = req.get((ns, pod), {})
            name = base_name(pod)
            workloads.append(dict(id=f"{h}-{i}", name=name, namespace=ns, node=h, replica=0,
                                  cpu_req_m=round(r.get("cpu", 0) * 1000), ram_req_bytes=int(r.get("memory", 0)),
                                  cpu_m=None, ram_bytes=None, ready=True, restarts=0, version="", component=None))
        rest = pods[per_node:]
        if rest:
            workloads.append(dict(
                id=f"{h}-other", name=f"other ({len(rest)} pods)", namespace="*", node=h, replica=0,
                cpu_req_m=round(sum(req.get(k, {}).get("cpu", 0) for k in rest) * 1000),
                ram_req_bytes=int(sum(req.get(k, {}).get("memory", 0) for k in rest)),
                cpu_m=None, ram_bytes=None, ready=True, restarts=0, version="", component=None))

    components = []
    for c in status["components"]:
        via = TOPO["via"]["today"]
        components.append(dict(c, via=via.get(c["id"], via["*"]), route=COMPONENT_ROUTE.get(c["id"])))

    keep = ["generated", "tz", "probe_interval_s", "home_age_s", "sample_age_s", "days_start",
            "hours_start", "latency_start", "groups", "outages"]
    e = {"v": 2, "source": "live" if live else "snapshot", "scenario": None}
    e.update({k: status[k] for k in keep})
    e["generated"] = now
    e.update(
        layout="today",
        providers=[dict(id=i, name=n, planned=all(x[4] == "edge" for x in TOPO["sites"] if x[2] == i))
                   for i, n in TOPO["providers"]],
        sites=[dict(id=i, name=n, provider=p, kind=k, role=r, planned=r == "edge") for i, n, p, k, r in TOPO["sites"]],
        nodes=nodes, workloads=workloads, components=components,
        hops=[dict(id=i, **{"from": f}, to=t, label=lbl, latency_ms=None, state="unknown", kind=k, planned=False)
              for i, f, t, k, lbl in TOPO["hops"]["today"]],
        stations=[dict(id=i, name=n, site=s, tier=ti, kind=k, planned=p) for i, n, s, ti, k, p in TOPO["stations"]["today"]],
        routes=[dict(r, calls=r.get("calls", []), hosts=TOPO["hosts"].get(r["id"], []), latency_ms=None, state="unknown")
                for r in TOPO["routes"]["today"]],
        stats={},
    )
    return e


def main():
    live = "--live" in sys.argv[1:]
    for per_node in (12, 6, 3):
        try:
            e = build(per_node, live)
        except Exception as exc:  # unreachable source, bad response
            print(f"estate_snapshot: {exc}", file=sys.stderr)
            return 2
        text = json.dumps(e, ensure_ascii=False, separators=(",", ":"))
        bad = public_ips(text)
        if bad:
            print(f"estate_snapshot: refusing to write, public IP-like values: {sorted(set(bad))}", file=sys.stderr)
            return 1
        if len(text.encode()) <= 80000:
            sys.stdout.write(text + "\n")
            return 0
    print(f"estate_snapshot: output {len(text.encode())} bytes > 80000", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
