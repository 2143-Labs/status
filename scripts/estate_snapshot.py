#!/usr/bin/env python3
"""Build a real `estate` v2 snapshot for the design demos.

    python3 scripts/estate_snapshot.py > designs/estate-real.json

Reads the public status.json (components, history) and the home Mimir (LAN only:
node specs/utilisation, pod placement and requests). Topology (providers, sites,
stations, hops) is the `today` layout from designs/estate-sim.js; hop latency is
not measured yet, so hops are null/unknown. stdlib only.

Exit 1: sanitiser found a public IP. Exit 2: a source was unreachable.
Exit 3: output over 60,000 bytes even with 6 workloads per node.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request

STATUS = "https://status-origin.2143.me/status.json"
MIMIR = "http://192.168.6.23:8080/prometheus/api/v1/query"
HOUSE = ["closet", "arch", "nas", "big", "office", "pite"]
NODE_META = {  # site, role, arch, lan_ip
    "closet": ("server", "amd64", "192.168.5.36"),
    "arch": ("server", "amd64", "192.168.5.76"),
    "nas": ("server", "amd64", "192.168.5.175"),
    "big": ("agent", "amd64", "192.168.5.68"),
    "office": ("agent", "amd64", "192.168.5.209"),
    "pite": ("agent", "arm64", "192.168.5.9"),
}
GiB, MiB, GB = 1024 ** 3, 1024 ** 2, 10 ** 9
CLOUD = [
    dict(id="doks-1", name="doks-1", site="nyc1", role="doks", arch="amd64", lan_ip="10.108.0.2",
         cpu_cores=2, ram_bytes=4 * GiB, disk_bytes=80 * GB, public_ip_locked=True),
    dict(id="status-1", name="status-1", site="tor1", role="droplet", arch="amd64", lan_ip=None,
         cpu_cores=1, ram_bytes=512 * MiB, disk_bytes=10 * GB, public_ip_locked=True),
]
PROVIDERS = [dict(id="home", name="Home", planned=False), dict(id="do", name="DigitalOcean", planned=False),
             dict(id="hetzner", name="Hetzner", planned=True)]
SITES = [dict(id="house", name="House", provider="home", kind="home", planned=False),
         dict(id="nyc1", name="nyc1", provider="do", kind="cloud", planned=False),
         dict(id="tor1", name="tor1", provider="do", kind="cloud", planned=False),
         dict(id="ash", name="Ashburn", provider="hetzner", kind="cloud", planned=True)]
STATIONS = [
    ("internet", "Internet", None, "ingress", "internet", False),
    ("edge-s1", "edge slot s1", "ash", "ingress", "edge", True),
    ("edge-s2", "edge slot s2", "nyc1", "ingress", "edge", True),
    ("do-lb", "DO load balancer", "nyc1", "ingress", "lb", False),
    ("router", "MikroTik RB5009 · 15 port forwards", "house", "ingress", "router", False),
    ("doks", "DOKS · nginx-gateway-fabric", "nyc1", "compute", "gateway", False),
    ("traefik", "Traefik VIP 192.168.6.6", "house", "compute", "vip", False),
    ("wg", "WireGuard 10.99.0.2 ⇄ router", None, "compute", "tunnel", False),
    ("status-1", "status-1 · statusd", "tor1", "service", "probe", False),
    ("pite", "pite · home monitor", "house", "service", "reporter", False),
]
HOPS = [
    ("internet-lb", "internet", "do-lb", "public", "public HTTPS"),
    ("lb-doks", "do-lb", "doks", "public", "LB → cluster"),
    ("internet-router", "internet", "router", "public", "home WAN · 15 forwards"),
    ("router-traefik", "router", "traefik", "lan", "LAN"),
    ("doks-wg", "doks", "wg", "tunnel", "WireGuard"),
    ("wg-router", "wg", "router", "tunnel", "WireGuard"),
    ("probe-public", "status-1", "internet", "probe", "probes"),
    ("pite-report", "pite", "status-1", "report", "reports"),
]
VIA = {
    "john2143-com": ["internet-router", "router-traefik"],
    "files-john2143-com": ["internet-router", "router-traefik"],
    "2143-me": ["internet-lb", "lb-doks"],
    "2143-me-user": ["internet-lb", "lb-doks"],
    "i-2143-me": ["internet-lb", "lb-doks"],
    "temporal": ["doks-wg", "wg-router", "router-traefik"],
}
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


def build(per_node):
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
    for c in CLOUD:
        nodes.append(dict(c, cpu_pct=0, ram_pct=0, disk_pct=0, net_rx_bps=0, net_tx_bps=0,
                          load1=0, temp_c=None, state="unknown", age_s=None))

    pod_node = {}
    for s in q('kube_pod_info{node!=""}'):
        m = s["metric"]
        if m.get("node") in HOUSE:
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
    for h in HOUSE:
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
        components.append(dict(c, via=VIA.get(c["id"], ["router-traefik"])))

    keep = ["generated", "tz", "probe_interval_s", "home_age_s", "sample_age_s", "days_start",
            "hours_start", "latency_start", "groups", "outages"]
    e = {"v": 2, "source": "snapshot", "scenario": None}
    e.update({k: status[k] for k in keep})
    e["generated"] = now
    e.update(
        layout="today", providers=PROVIDERS, sites=SITES, nodes=nodes, workloads=workloads,
        components=components,
        hops=[dict(id=i, **{"from": f}, to=t, label=lbl, latency_ms=None, state="unknown", kind=k, planned=False)
              for i, f, t, k, lbl in HOPS],
        stations=[dict(id=i, name=n, site=s, tier=ti, kind=k, planned=p) for i, n, s, ti, k, p in STATIONS],
        stats={},
    )
    return e


def main():
    for per_node in (12, 6):
        try:
            e = build(per_node)
        except Exception as exc:  # unreachable source, bad response
            print(f"estate_snapshot: {exc}", file=sys.stderr)
            return 2
        text = json.dumps(e, ensure_ascii=False, separators=(",", ":"))
        bad = public_ips(text)
        if bad:
            print(f"estate_snapshot: refusing to write, public IP-like values: {sorted(set(bad))}", file=sys.stderr)
            return 1
        if len(text.encode()) <= 60000:
            sys.stdout.write(text + "\n")
            return 0
    print(f"estate_snapshot: output {len(text.encode())} bytes > 60000", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
