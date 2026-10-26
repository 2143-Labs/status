/*
 * estate-sim.js — deterministic simulator for the `estate` v2 contract.
 *
 * This header is the contract of record. Every demo page (designs/demo-*.html)
 * consumes exactly this shape, whether it comes from simulate() or from
 * designs/estate-real.json (written by scripts/estate_snapshot.py).
 * Times are unix seconds, sizes bytes, latencies ms.
 *
 * {
 *   v: 2, source: "sim" | "snapshot", scenario: <name> | null (sim only),
 *   generated, tz, probe_interval_s, home_age_s (int|null), sample_age_s,
 *   days_start "YYYY-MM-DD", hours_start, latency_start,
 *   groups: [{id, name, note}]                         identical to status.json v1
 *   layout: "today" | "target"                         as built | the ip-hiding north star
 *   providers: [{id, name, planned}]                   one building each: home, do, hetzner
 *   sites: [{id, name, provider, kind: home|cloud, role: origin|edge, planned}]
 *          one room each: isp-lan (Verizon LAN, outside the MikroTik), house, nyc1, tor1,
 *          and the edge rooms ash (Hetzner) and do-edge (DO) — the planned CDN home proxies
 *   nodes: [{id, name, site, role: server|agent|droplet|doks|edge, arch, lan_ip (private|null),
 *            cpu_cores, ram_bytes, disk_bytes, cpu_pct, ram_pct, disk_pct, net_rx_bps, net_tx_bps,
 *            load1, temp_c|null, state, age_s, public_ip_locked}]
 *   workloads: [{id, name, namespace, node, replica, cpu_req_m, ram_req_bytes, cpu_m, ram_bytes,
 *                ready, restarts, version, component|null}]      fungible tenants of nodes
 *   components: [status.json v1 component fields unchanged, plus via: [hop id, ...],
 *                route: <route id>|null]                    the traffic family it belongs to
 *   routes: [{id, kind: request|call, name, from, to, path: [hop id], calls: [route id],
 *             note, hosts: [hostname], latency_ms|null, state}]
 *           request = how outside traffic arrives (home-direct, do-only, images, lan-only);
 *           call = backend-to-backend traffic (images-s3, temporal-grpc, mongo)
 *   hops: [{id, from, to, label, latency_ms|null, state, kind: public|tunnel|lan|probe|report, planned}]
 *   stations: [{id, name, site|null, tier: ingress|compute|service,
 *               kind: internet|isp|lb|gateway|tunnel|tailnet|router|vip|store|host|probe|reporter|edge|connector,
 *               planned}]
 *   stats: {<component id>: {visitors_7d, requests_24h, ...}}  ({} in a snapshot)
 *   outages: [{id, start, end|null}]                    identical to v1, newest first
 * }
 * state is always one of up | degraded | down | unknown.
 *
 * Topology (sites, stations, hops, via, routes, hostnames) is data in estate-topology.json.
 * Station/hop ids are load-bearing and differ by layout (`do-lb` and `internet-router`
 * exist only in "today"; edge nodes, connectors and their hops only in "target").
 * No public IP address may ever appear in output: LAN 192.168.* and private 10.* only;
 * anything public is `public_ip_locked: true` and rendered as "Sign in to reveal".
 *
 * Pure: simulate(args) with equal args returns deep-equal output. Never reads the clock.
 */

import TOPO from "./estate-topology.json" with { type: "json" };

export const SCENARIOS = ["normal", "incident", "home-offline", "stale", "node-pressure", "tunnel-slow", "cert-expiring", "deploy-storm"];
export const BASE_T = 1792800000;
export const LAYOUTS = ["today", "target"];

export function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hash(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

// A PRNG private to one entity (and optionally one 30 s window), so adding an
// entity never reshuffles another's numbers.
function rng(seed, ...parts) {
  return mulberry32(hash(seed + ":" + parts.join(":")));
}
const between = (r, lo, hi) => lo + r() * (hi - lo);
const ibetween = (r, lo, hi) => Math.floor(between(r, lo, hi + 1));
const round1 = (n) => Math.round(n * 10) / 10;
const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

const KiB = 1024, MiB = KiB * 1024, GiB = MiB * 1024, GB = 1e9, TB = 1e12;
const DAY = 86400, HOUR = 3600;

const GROUPS = [
  { id: "web", name: "Websites", note: "Checked from outside the house." },
  { id: "home", name: "Home services", note: "Reported by the home monitor every 30 s." },
  { id: "monitoring", name: "Monitoring", note: "Reported by the home monitor every 30 s." },
];

const COMPONENTS = [
  ["john2143-com", "john2143.com", "web", "home", "https://john2143.com"],
  ["files-john2143-com", "files.john2143.com", "web", "home", "https://files.john2143.com"],
  ["2143-me", "2143.me", "web", "cloud", "https://2143.me"],
  ["2143-me-user", "2143.me/user", "web", "cloud", "https://2143.me/user"],
  ["i-2143-me", "i.2143.me", "web", "cloud", "https://i.2143.me"],
  ["gateway", "Home gateway", "home", "home", null],
  ["mail", "Mail (SMTP)", "home", "home", null],
  ["teamspeak", "TeamSpeak files", "home", "home", null],
  ["mqtt", "MQTT broker", "home", "home", null],
  ["temporal", "Temporal", "home", "home", null],
  ["metrics", "Metrics (Mimir)", "monitoring", "home", null],
  ["logs", "Logs (Loki)", "monitoring", "home", null],
];

const ALIASES = TOPO.hosts.images.slice();

// Topology is data, shared with scripts/estate_snapshot.py: designs/estate-topology.json.
const PROVIDERS = TOPO.providers;            // [id, name]
const SITES = TOPO.sites;                    // [id, name, provider, kind, role]
const STATIONS = TOPO.stations;              // {layout: [[id, name, site, tier, kind, planned]]}
const HOPS = TOPO.hops;                      // {layout: [[id, from, to, kind, label]]}
const strip = (o) => Object.fromEntries(Object.entries(o).filter(([k]) => k !== "*"));
const VIA = { today: strip(TOPO.via.today), target: strip(TOPO.via.target) };
const VIA_DEFAULT = { today: TOPO.via.today["*"], target: TOPO.via.target["*"] };
const ROUTES = TOPO.routes;                  // {layout: [{id, kind, name, from, to, path, calls?, note}]}
const TUNNEL_HOP = { today: "doks-wg", target: "wg-doks" };
// Which traffic family each status component belongs to (null = checked on the LAN only).
const COMPONENT_ROUTE = {
  "john2143-com": "home-direct", "files-john2143-com": "home-direct",
  "2143-me": "do-only", "2143-me-user": "do-only", "i-2143-me": "images", temporal: "temporal-grpc",
};
const HOME_SITES = new Set(SITES.filter((s) => s[3] === "home").map((s) => s[0]));

// [id, site, role, arch, lan_ip, cores, ram, disk, public_ip_locked]
const NODES = TOPO.nodes;
const EDGE_NODES = TOPO.edge_nodes;

// [name, namespace, component, [nodes...]]
const WORKLOADS = [
  ["traefik", "kube-system", "gateway", ["closet", "closet", "nas"]],
  ["lan-dns", "lan-dns", null, ["closet", "nas"]],
  ["mimir", "monitoring", "metrics", ["arch", "nas"]],
  ["loki", "monitoring", "logs", ["arch"]],
  ["seaweedfs", "storage", null, ["nas"]],
  ["stalwart", "mail", "mail", ["nas"]],
  ["teamspeak", "teamspeak", "teamspeak", ["closet"]],
  ["mosquitto", "mqtt", "mqtt", ["arch"]],
  ["temporal", "temporal", "temporal", ["arch"]],
  ["frigate", "cameras", null, ["big"]],
  ["john2143-com", "web", "john2143-com", ["closet", "arch"]],
  ["files", "files", "files-john2143-com", ["nas"]],
  ["pocket-id", "pocket-id", null, ["arch"]],
  ["grafana", "monitoring", null, ["office"]],
  ["oauth2-proxy-cameras", "cameras", null, ["office"]],
  ["statusd", "status", null, ["status-1"]],
  ["caddy", "status", null, ["status-1"]],
  ["imagehost", "imagehost", "i-2143-me", ["doks-1"]],
  ["site-2143-me", "web", "2143-me", ["doks-1"]],
  ["nginx-gateway-fabric", "nginx-gateway", null, ["doks-1"]],
  ["wireguard-doks", "wireguard", null, ["doks-1"]],
  ["attic", "attic", null, ["home-pi"]],
];
const TARGET_WORKLOADS = [
  ["edge-connector", "edge", null, ["closet", "arch", "nas", "doks-1", "doks-1"]],
  ["nginx-stream", "edge", null, ["edge-1", "edge-2"]],
  ["coturn", "edge", null, ["edge-1", "edge-2"]],
  ["derper", "edge", null, ["edge-1", "edge-2"]],
];
const REQUESTS = {
  traefik: [100, 128 * MiB], mimir: [500, 2 * GiB], loki: [250, GiB], seaweedfs: [250, GiB],
  frigate: [1000, 4 * GiB], temporal: [500, GiB], attic: [250, 512 * MiB], "edge-connector": [50, 64 * MiB],
  "nginx-stream": [100, 128 * MiB], coturn: [100, 128 * MiB], derper: [100, 128 * MiB],
};
const DEFAULT_REQUEST = [50, 128 * MiB];

const HOP_BASE = { public: [8, 25], tunnel: [18, 35], lan: [0.4, 2], probe: [20, 40], report: [25, 45] };

function isoDate(t) {
  return new Date(t * 1000).toISOString().slice(0, 10);
}

// 0–3 historical outages per component over the 90 days before BASE_T.
function history(seed, id) {
  const r = rng(seed, "history", id);
  const n = ibetween(r, 0, 3);
  const out = [];
  for (let i = 0; i < n; i++) {
    const start = Math.floor(BASE_T - between(r, HOUR, 89 * DAY));
    const dur = ibetween(r, 2, 90) * 60;
    out.push({ id, start, end: start + dur });
  }
  return out;
}

function overlap(a0, a1, b0, b1) {
  return Math.max(0, Math.min(a1, b1) - Math.max(a0, b0));
}

function buildHistory(id, outs, daysStart, hoursStart, latencyStart, base, seed) {
  const daily_pct = [], daily_down_min = [], hourly_pct = [], latency_10m = [];
  for (let d = 0; d < 90; d++) {
    const d0 = daysStart + d * DAY;
    const down = outs.reduce((s, o) => s + overlap(o.start, o.end, d0, d0 + DAY), 0);
    daily_down_min.push(Math.round(down / 60));
    daily_pct.push(round1(100 - (down / DAY) * 100));
  }
  for (let h = 0; h < 168; h++) {
    const h0 = hoursStart + h * HOUR;
    const down = outs.reduce((s, o) => s + overlap(o.start, o.end, h0, h0 + HOUR), 0);
    hourly_pct.push(round1(100 - (down / HOUR) * 100));
  }
  const r = rng(seed, "lat10", id);
  for (let i = 0; i < 144; i++) latency_10m.push(round1(base * between(r, 0.8, 1.3)));
  const avg = (a) => round1(a.reduce((s, x) => s + x, 0) / a.length);
  const uptime = {
    "24h": avg(hourly_pct.slice(-24)),
    "7d": avg(hourly_pct),
    "30d": avg(daily_pct.slice(-30)),
    "90d": avg(daily_pct),
  };
  return { daily_pct, daily_down_min, hourly_pct, latency_10m, uptime };
}

export function simulate({ seed = 1, scenario = "normal", elapsed = 0, layout = "today" } = {}) {
  if (!LAYOUTS.includes(layout)) layout = "today";
  if (!SCENARIOS.includes(scenario)) scenario = "normal";
  elapsed = Math.max(0, Math.floor(Number(elapsed) || 0));
  seed = Math.floor(Number(seed) || 1);
  const sc = scenario;
  const target = layout === "target";
  // stale freezes the world at BASE_T; only the age grows.
  const e = sc === "stale" ? 0 : elapsed;
  const generated = BASE_T + e;
  const win = Math.floor(e / 30);
  const live = (...parts) => rng(seed, ...parts, win);

  const daysStart = Math.floor(BASE_T / DAY) * DAY - 89 * DAY;
  const hoursStart = Math.floor(BASE_T / HOUR) * HOUR - 167 * HOUR;
  const latencyStart = Math.floor(BASE_T / 600) * 600 - 143 * 600;

  const homeOffline = sc === "home-offline" && elapsed >= 10;
  const homeAge = homeOffline ? elapsed - 10 : ibetween(live("home-age"), 0, 29);

  // Providers and sites: Hetzner/Ashburn exist only as planned in "today".
  // Edge sites (the CDN home proxies) are planned until the target layout; a provider is
  // planned when every one of its sites is.
  const sites = SITES.map(([id, name, provider, kind, role]) => ({ id, name, provider, kind, role, planned: !target && role === "edge" }));
  const providers = PROVIDERS.map(([id, name]) => ({ id, name, planned: sites.filter((s) => s.provider === id).every((s) => s.planned) }));

  const stations = STATIONS[layout].map(([id, name, site, tier, kind, planned]) => ({ id, name, site, tier, kind, planned }));

  // Hops.
  const tunnelSlow = sc === "tunnel-slow" && elapsed >= 15;
  const hops = HOPS[layout].map(([id, from, to, kind, label]) => {
    const [lo, hi] = HOP_BASE[kind];
    const base = between(rng(seed, "hop", id), lo, hi);
    let latency_ms = round1(base * between(live("hop-j", id), 0.85, 1.2));
    let state = "up";
    if (tunnelSlow && id === TUNNEL_HOP[layout]) { latency_ms = 480; state = "degraded"; }
    if (homeOffline && id === "pite-report") { latency_ms = null; state = "down"; }
    return { id, from, to, label, latency_ms, state, kind, planned: false };
  });
  const hopById = Object.fromEntries(hops.map((h) => [h.id, h]));
  const RANK = { up: 0, unknown: 1, degraded: 2, down: 3 };
  const worst = (states) => states.reduce((a, b) => (RANK[b] > RANK[a] ? b : a), "up");
  const routes = ROUTES[layout].map((r) => {
    const hs = r.path.map((id) => hopById[id]);
    const lat = hs.every((h) => h.latency_ms != null) ? round1(hs.reduce((a, h) => a + h.latency_ms, 0)) : null;
    return { id: r.id, kind: r.kind, name: r.name, from: r.from, to: r.to, path: r.path.slice(), calls: (r.calls || []).slice(),
      note: r.note, hosts: (TOPO.hosts[r.id] || []).slice(), latency_ms: lat, state: worst(hs.map((h) => h.state)) };
  });

  // Workloads (placement fixed; fungible: deploy-storm moves grafana).
  const specs = target ? WORKLOADS.concat(TARGET_WORKLOADS) : WORKLOADS;
  const workloads = [];
  for (const [name, namespace, component, nodes] of specs) {
    nodes.forEach((node, replica) => {
      const [cpu_req_m, ram_req_bytes] = REQUESTS[name] || DEFAULT_REQUEST;
      const vr = rng(seed, "version", name);
      const id = `${name}-${replica}`;
      const u = live("usage", id);
      workloads.push({
        id, name, namespace, node, replica, cpu_req_m, ram_req_bytes,
        cpu_m: Math.round(cpu_req_m * between(u, 0.4, 1.3)),
        ram_bytes: Math.round(ram_req_bytes * between(u, 0.4, 1.3)),
        ready: true, restarts: 0,
        minor: ibetween(vr, 0, 12), patch: ibetween(vr, 0, 9),
        component,
      });
    });
  }
  if (sc === "deploy-storm") {
    const pool = workloads.filter((w) => w.node === "big" || w.node === "arch")
      .sort((a, b) => (a.id < b.id ? -1 : 1));
    const events = Math.floor(elapsed / 5);
    for (let i = 1; i <= events; i++) {
      const w = pool[(i - 1) % pool.length];
      w.restarts += 1;
      w.patch += 1;
      if (i === events && elapsed - 5 * i < 3) w.ready = false;
    }
    if (elapsed >= 30) workloads.find((w) => w.name === "grafana").node = "big";
  }
  for (const w of workloads) {
    w.version = `${w.name}:1.${w.minor}.${w.patch}`;
    delete w.minor;
    delete w.patch;
  }

  // Nodes.
  const nodeSpecs = target ? NODES.concat(EDGE_NODES) : NODES;
  const nodes = nodeSpecs.map(([id, site, role, arch, lan_ip, cpu_cores, ram_bytes, disk_bytes, public_ip_locked]) => {
    const r = rng(seed, "node", id);
    const j = live("node-j", id);
    const mine = workloads.filter((w) => w.node === id);
    const house = HOME_SITES.has(site);
    const cpuUsed = mine.reduce((s, w) => s + w.cpu_m, 0);
    const ramUsed = mine.reduce((s, w) => s + w.ram_bytes, 0);
    let cpu_pct = round1(clamp(between(j, 2, 9) + (cpuUsed / (cpu_cores * 1000)) * 100, 0, 100));
    let ram_pct = round1(clamp(between(r, 6, 14) + (ramUsed / ram_bytes) * 100, 0, 100));
    let disk_pct = round1(between(r, 18, 68));
    if (sc === "node-pressure") {
      const f = Math.min(elapsed, 60) / 60;
      if (id === "nas") disk_pct = round1(70 + 27 * f);
      if (id === "closet") ram_pct = round1(60 + 32 * f);
    }
    let state = cpu_pct >= 90 || ram_pct >= 90 || disk_pct >= 90 ? "degraded" : "up";
    let age_s = house ? ibetween(live("node-age", id), 0, 29) : 0;
    if (homeOffline && house) { state = "unknown"; age_s = elapsed - 10; }
    return {
      id, name: id, site, role, arch, lan_ip, cpu_cores, ram_bytes, disk_bytes,
      cpu_pct, ram_pct, disk_pct,
      net_rx_bps: Math.round(between(j, 2e4, 4e6)),
      net_tx_bps: Math.round(between(j, 1e4, 2e6)),
      load1: Math.round(cpu_cores * (cpu_pct / 100) * between(j, 0.8, 1.2) * 100) / 100,
      temp_c: house ? round1(between(r, 36, 58) + cpu_pct / 10) : null,
      state, age_s, public_ip_locked,
    };
  });

  // Components.
  const outages = [];
  const components = COMPONENTS.map(([id, name, group, hosted, url]) => {
    const via = (VIA[layout][id] || VIA_DEFAULT[layout]).slice();
    const r = rng(seed, "comp", id);
    const base = hosted === "home" ? between(r, 15, 40) : between(r, 40, 90);
    const past = history(seed, id);
    outages.push(...past);
    const h = buildHistory(id, past, daysStart, hoursStart, latencyStart, base, seed);
    let latency_ms = round1(base * between(live("comp-j", id), 0.85, 1.15)
      + via.reduce((s, hid) => s + (hopById[hid].latency_ms || 0), 0));
    let state = "up";
    let cert_days = url ? ibetween(r, 20, 80) : null;
    if (sc === "incident") {
      if (id === "mail" && elapsed >= 20 && elapsed < 80) state = "down";
      if (id === "john2143-com" && elapsed >= 40 && elapsed < 70) { state = "degraded"; latency_ms = round1(latency_ms * 6); }
    }
    if (tunnelSlow && via.includes(TUNNEL_HOP[layout])) state = "degraded";
    if (sc === "cert-expiring") {
      if (id === "i-2143-me") cert_days = 6;
      if (id === "files-john2143-com") cert_days = 2;
    }
    if (homeOffline && (group === "home" || group === "monitoring")) state = "unknown";
    if (state === "down" || state === "unknown") latency_ms = null;
    const comp = {
      id, name, group, hosted, state, latency_ms, cert_days,
      uptime: h.uptime, since: BASE_T - 90 * DAY,
      daily_pct: h.daily_pct, daily_down_min: h.daily_down_min,
      hourly_pct: h.hourly_pct, latency_10m: h.latency_10m,
    };
    if (url) comp.url = url;
    comp.via = via;
    comp.route = COMPONENT_ROUTE[id] || null;
    return comp;
  });
  if (sc === "incident" && elapsed >= 20) {
    outages.push({ id: "mail", start: BASE_T + 20, end: elapsed >= 80 ? BASE_T + 80 : null });
  }
  outages.sort((a, b) => b.start - a.start || (a.id < b.id ? -1 : 1));

  // Traffic stats (http components only).
  const stats = {};
  for (const [id, , , , url] of COMPONENTS) {
    if (!url) { stats[id] = {}; continue; }
    const r = rng(seed, "stats", id);
    const s = { visitors_7d: ibetween(r, 40, 4000), requests_24h: ibetween(r, 200, 60000) };
    if (id === "i-2143-me") {
      s.uploads_7d = ibetween(r, 5, 300);
      s.upload_bytes_7d = Math.round(between(r, 20, 900) * MiB);
      s.aliases = ALIASES.slice();
    }
    stats[id] = s;
  }

  return {
    v: 2,
    source: "sim",
    scenario: sc,
    generated,
    tz: "America/New_York",
    probe_interval_s: 30,
    home_age_s: homeAge,
    sample_age_s: sc === "stale" ? elapsed : 0,
    days_start: isoDate(daysStart),
    hours_start: hoursStart,
    latency_start: latencyStart,
    groups: GROUPS.map((g) => ({ ...g })),
    layout,
    providers,
    sites,
    nodes,
    workloads,
    components,
    hops,
    stations,
    routes,
    stats,
    outages,
  };
}

// Exposed for scripts/estate_snapshot.py parity checks and the tests; not part of the contract.
export const FIXED = { NODES, STATIONS, HOPS, VIA, VIA_DEFAULT, PROVIDERS, SITES, GROUPS, ROUTES };
