import test from "node:test";
import assert from "node:assert/strict";
import { simulate, SCENARIOS, LAYOUTS, BASE_T } from "../../designs/estate-sim.js";

const KEYS = ["v", "source", "scenario", "generated", "tz", "probe_interval_s", "home_age_s", "sample_age_s",
  "days_start", "hours_start", "latency_start", "groups", "layout", "providers", "sites", "nodes",
  "workloads", "components", "hops", "stations", "stats", "outages"];
const comp = (e, id) => e.components.find((c) => c.id === id);
const node = (e, id) => e.nodes.find((n) => n.id === id);
const hop = (e, id) => e.hops.find((h) => h.id === id);

test("deterministic", () => {
  const a = simulate({ seed: 7, scenario: "incident", elapsed: 42 });
  assert.deepEqual(a, simulate({ seed: 7, scenario: "incident", elapsed: 42 }));
});

test("every scenario and layout has the full contract", () => {
  for (const scenario of SCENARIOS)
    for (const layout of LAYOUTS)
      for (const elapsed of [0, 30, 90]) {
        const e = simulate({ scenario, elapsed, layout });
        assert.deepEqual(Object.keys(e).sort(), [...KEYS].sort(), `${scenario}/${layout}/${elapsed}`);
        assert.equal(e.components.length, 12);
      }
});

test("incident", () => {
  assert.equal(comp(simulate({ scenario: "incident", elapsed: 30 }), "mail").state, "down");
  const e = simulate({ scenario: "incident", elapsed: 90 });
  assert.equal(comp(e, "mail").state, "up");
  assert.ok(e.outages.some((o) => o.id === "mail" && o.start === BASE_T + 20 && o.end === BASE_T + 80));
});

test("home-offline", () => {
  const e = simulate({ scenario: "home-offline", elapsed: 30 });
  for (const c of e.components) if (c.group !== "web") assert.equal(c.state, "unknown", c.id);
  assert.equal(e.home_age_s, 20);
  assert.equal(hop(e, "pite-report").state, "down");
});

test("stale", () => {
  const e = simulate({ scenario: "stale", elapsed: 90 });
  assert.equal(e.generated, BASE_T);
  assert.equal(e.sample_age_s, 90);
});

test("node-pressure", () => {
  const nas = node(simulate({ scenario: "node-pressure", elapsed: 60 }), "nas");
  assert.ok(nas.disk_pct >= 95);
  assert.equal(nas.state, "degraded");
});

test("tunnel-slow in both layouts", () => {
  const t = simulate({ scenario: "tunnel-slow", elapsed: 20 });
  assert.equal(hop(t, "doks-wg").latency_ms, 480);
  assert.equal(comp(t, "temporal").state, "degraded");
  assert.equal(hop(simulate({ scenario: "tunnel-slow", elapsed: 20, layout: "target" }), "wg-doks").latency_ms, 480);
});

test("cert-expiring", () => {
  assert.equal(comp(simulate({ scenario: "cert-expiring" }), "i-2143-me").cert_days, 6);
});

test("deploy-storm moves grafana", () => {
  const e = simulate({ scenario: "deploy-storm", elapsed: 31 });
  assert.equal(e.workloads.find((w) => w.name === "grafana").node, "big");
});

test("layouts", () => {
  const t = simulate({ layout: "today" });
  assert.ok(t.stations.some((s) => s.id === "do-lb"));
  assert.ok(hop(t, "internet-router"));
  assert.equal(t.providers.find((p) => p.id === "hetzner").planned, true);
  const g = simulate({ layout: "target" });
  assert.ok(!g.stations.some((s) => s.id === "do-lb"));
  assert.ok(!hop(g, "internet-router"));
  assert.ok(node(g, "edge-1") && node(g, "edge-2"));
  assert.equal(simulate({ layout: "bogus" }).layout, "today");
  for (const e of [t, g]) {
    const hopIds = new Set(e.hops.map((h) => h.id));
    const stIds = new Set(e.stations.map((s) => s.id));
    for (const c of e.components) for (const h of c.via) assert.ok(hopIds.has(h), `${e.layout} ${c.id} via ${h}`);
    for (const h of e.hops) assert.ok(stIds.has(h.from) && stIds.has(h.to), `${e.layout} ${h.id}`);
    for (const w of e.workloads) assert.ok(e.nodes.some((n) => n.id === w.node), w.id);
  }
});

test("RAM requests fit every node", () => {
  for (let seed = 1; seed <= 20; seed++)
    for (const layout of LAYOUTS) {
      const e = simulate({ seed, layout, scenario: "deploy-storm", elapsed: 60 });
      for (const n of e.nodes) {
        const sum = e.workloads.filter((w) => w.node === n.id).reduce((s, w) => s + w.ram_req_bytes, 0);
        assert.ok(sum <= 0.85 * n.ram_bytes, `${seed} ${n.id}`);
      }
    }
});

test("no public IP in output", () => {
  const quad = /(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.-])/g;
  for (const scenario of SCENARIOS)
    for (const layout of LAYOUTS) {
      const s = JSON.stringify(simulate({ scenario, layout, elapsed: 45 }));
      for (const m of s.matchAll(quad)) {
        const o = m.slice(1).map(Number);
        assert.ok(o[0] === 10 || o[0] === 127 || (o[0] === 192 && o[1] === 168), m[0]);
      }
    }
});
