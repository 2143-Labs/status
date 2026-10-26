// estate-core.js — shared plumbing for designs/demo-*.html.
// Data comes from estate-sim.js (default) or estate-real.json (?source=snapshot);
// the contract is documented at the top of estate-sim.js.
import { simulate, SCENARIOS, LAYOUTS } from "./estate-sim.js";

export function params() {
  const q = new URLSearchParams(location.search);
  const int = (v, d) => {
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? n : d;
  };
  const scenario = SCENARIOS.includes(q.get("scenario")) ? q.get("scenario") : "normal";
  const layout = LAYOUTS.includes(q.get("layout")) ? q.get("layout") : "today";
  const speedRaw = Number(q.get("speed"));
  const speed = q.has("speed") && Number.isFinite(speedRaw) ? Math.min(600, Math.max(0, speedRaw)) : 1;
  return {
    scenario,
    seed: int(q.get("seed"), 1),
    speed,
    t: q.has("t") ? Math.max(0, int(q.get("t"), 0)) : null,
    source: q.get("source") === "snapshot" ? "snapshot" : "sim",
    plain: q.has("plain"),
    layout,
  };
}

let current = null;

export async function start(onEstate) {
  const p = params();
  let timer = null;
  let stopped = false;
  const emit = (e) => {
    current = e;
    onEstate(e);
  };

  if (p.source === "snapshot") {
    try {
      const res = await fetch("estate-real.json", { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      const e = await res.json();
      e.frozen = true;
      emit(e);
    } catch {
      const e = simulate({ seed: p.seed, scenario: "normal", elapsed: 0, layout: p.layout });
      e.load_error = "estate-real.json not available";
      emit(e);
    }
    return { stop() { stopped = true; }, estate: () => current };
  }

  let elapsed = p.t ?? 0;
  const tick = () => emit(simulate({ seed: p.seed, scenario: p.scenario, elapsed, layout: p.layout }));
  tick();
  if (p.t === null) {
    timer = setInterval(() => {
      if (stopped || document.hidden) return;
      elapsed += p.speed;
      tick();
    }, 1000);
  }
  return {
    stop() {
      stopped = true;
      clearInterval(timer);
    },
    estate: () => current,
  };
}

export function guard() {
  if (params().plain) return "plain";
  if (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 2) return "plain";
  if (navigator.deviceMemory && navigator.deviceMemory <= 2) return "plain";
  return "full";
}

export function watchFrames(onSlow) {
  const deltas = [];
  let last = null;
  const step = (ts) => {
    if (last !== null) deltas.push(ts - last);
    last = ts;
    if (deltas.length < 60) return requestAnimationFrame(step);
    deltas.sort((a, b) => a - b);
    if (deltas[30] > 25) onSlow();
  };
  requestAnimationFrame(step);
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function stateWord(s) {
  return { up: "Up", degraded: "Slow", down: "Down" }[s] || "Not reporting";
}

export function stateBadge(s) {
  const k = ["up", "degraded", "down"].includes(s) ? s : "unknown";
  return `<span class="st st-${k}">${stateWord(s)}</span>`;
}

export function fmtBytes(n) {
  if (n == null) return "—";
  const u = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let i = 0;
  while (Math.abs(n) >= 1024 && i < u.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n >= 10 || i === 0 ? Math.round(n) : n.toFixed(1)} ${u[i]}`;
}

export function fmtPct(n) {
  return n == null ? "—" : `${Math.round(n)}%`;
}

export function fmtMs(n) {
  if (n == null) return "—";
  return n < 10 ? `${n.toFixed(1)} ms` : `${Math.round(n)} ms`;
}

export function fmtAge(s) {
  if (s == null) return "never";
  if (s < 60) return `${Math.round(s)} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function renderPlain(e, el) {
  const head = el.querySelector("h2") ? el.querySelector("h2").outerHTML : "";
  const age = (n) => (n.state === "unknown" ? fmtAge(n.age_s) : fmtAge(n.age_s));
  const hosts = Object.fromEntries((e.stations || []).map((s) => [s.id, s.name]));
  const comps = e.components.map((c) => `<tr id="row-component-${esc(c.id)}"><th scope="row">${esc(c.name)}</th><td>${stateBadge(c.state)}</td><td>${fmtMs(c.latency_ms)}</td><td>${c.uptime ? c.uptime["30d"] + "%" : "—"}</td><td>${c.cert_days ?? "—"}</td><td>${c.group === "web" ? fmtAge(e.sample_age_s) : fmtAge(e.home_age_s)}</td></tr>`).join("");
  const nodes = e.nodes.map((n) => `<tr id="row-node-${esc(n.id)}"><th scope="row">${esc(n.name)}</th><td>${esc(n.site)}</td><td>${esc(n.role)}</td><td>${n.lan_ip ? esc(n.lan_ip) : "—"}${n.public_ip_locked ? ' <span class="chip locked">Sign in to reveal</span>' : ""}</td><td>${fmtPct(n.cpu_pct)}</td><td>${fmtPct(n.ram_pct)}</td><td>${fmtPct(n.disk_pct)}</td><td>${stateBadge(n.state)}</td><td>${age(n)}</td></tr>`).join("");
  const hops = e.hops.map((h) => `<tr id="row-hop-${esc(h.id)}"><th scope="row">${esc(hosts[h.from] || h.from)} → ${esc(hosts[h.to] || h.to)}</th><td>${esc(h.label)}</td><td>${fmtMs(h.latency_ms)}</td><td>${stateBadge(h.state)}</td></tr>`).join("");
  const note = e.load_error ? `<p class="plain-note">${esc(e.load_error)} — showing simulated data.</p>` : "";
  el.innerHTML = `${head}${note}
<table><caption>Components</caption><thead><tr><th scope="col">Name</th><th scope="col">State</th><th scope="col">Latency</th><th scope="col">30 d uptime</th><th scope="col">Cert days</th><th scope="col">Reported</th></tr></thead><tbody>${comps}</tbody></table>
<table><caption>Nodes</caption><thead><tr><th scope="col">Name</th><th scope="col">Site</th><th scope="col">Role</th><th scope="col">LAN IP</th><th scope="col">CPU</th><th scope="col">RAM</th><th scope="col">Disk</th><th scope="col">State</th><th scope="col">Reported</th></tr></thead><tbody>${nodes}</tbody></table>
<table><caption>Hops</caption><thead><tr><th scope="col">Path</th><th scope="col">Link</th><th scope="col">Latency</th><th scope="col">State</th></tr></thead><tbody>${hops}</tbody></table>`;
}

export function scenarioBar(el) {
  const p = params();
  const go = (patch) => {
    const q = new URLSearchParams(location.search);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null) q.delete(k);
      else q.set(k, v);
    }
    location.search = q.toString();
  };
  const snap = p.source === "snapshot";
  const shownLayout = snap && current ? current.layout : p.layout;
  const plainHref = (() => {
    const q = new URLSearchParams(location.search);
    q.set("plain", "");
    return "?" + q.toString().replace(/plain=(&|$)/, "plain$1");
  })();
  el.className = "scenario-bar";
  el.setAttribute("role", "toolbar");
  el.setAttribute("aria-label", "Demo controls");
  el.innerHTML = `
<label>Scenario <select data-k="scenario"${snap ? " disabled" : ""}>${SCENARIOS.map((s) => `<option${s === p.scenario ? " selected" : ""}>${s}</option>`).join("")}</select></label>
<span class="toggle" role="group" aria-label="Layout">${LAYOUTS.map((l) => `<button type="button" data-layout="${l}" aria-pressed="${l === shownLayout}"${snap ? " disabled" : ""}>${l === "today" ? "Today" : "Target"}</button>`).join("")}</span>
<label>Seed <input data-k="seed" type="number" min="1" value="${p.seed}" size="4"${snap ? " disabled" : ""}></label>
<label>Speed <select data-k="speed"${snap ? " disabled" : ""}>${[1, 10, 60, 600].map((s) => `<option value="${s}"${s === p.speed ? " selected" : ""}>${s}×</option>`).join("")}</select></label>
<a href="${esc(plainHref)}">Plain view</a>
<span class="clock" aria-live="off"></span>`;
  el.querySelectorAll("select,input").forEach((c) => c.addEventListener("change", () => go({ [c.dataset.k]: c.value, t: null })));
  el.querySelectorAll("[data-layout]").forEach((b) => b.addEventListener("click", () => go({ layout: b.dataset.layout })));
  const clock = el.querySelector(".clock");
  const upd = () => {
    if (!current) return;
    const src = current.source === "snapshot" ? "snapshot" : `${current.scenario} · t+${Math.round(current.generated - 1792800000 + (current.sample_age_s || 0))} s`;
    clock.textContent = `${src} · ${new Date(current.generated * 1000).toISOString().slice(11, 19)} UTC`;
  };
  upd();
  setInterval(upd, 1000);
}

// Mode logic shared by every demo: returns "full" or "plain" and wires the slow-device fallback.
export function mountModes({ stage, plain, notice, onPlain }) {
  const toPlain = (msg) => {
    stage.hidden = true;
    notice.textContent = msg;
    notice.hidden = false;
    if (onPlain) onPlain();
  };
  if (guard() === "plain") {
    toPlain("Showing the plain view (weak device or ?plain).");
    return "plain";
  }
  watchFrames(() => toPlain("Switched to the plain view: this device is rendering slowly."));
  return "full";
}
