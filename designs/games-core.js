// games-core.js — plumbing for designs/games.html.
// Data comes from games-live.json (the v1 feed written by scripts/games_snapshot.py
// every `interval_s` seconds — 3 s today) or games-sample.json (committed sample,
// also forced by ?source=sample).
// Nothing here touches the DOM at import time, so this module loads under Node for tests.
const DEFAULT_INTERVAL_S = 60;
// The feed carries its own cadence; the page follows it, clamped so a broken or
// absurd interval_s cannot turn into a request storm or a frozen page.
const POLL_MIN_S = 1;
const POLL_MAX_S = 30;
const STATES = ["up", "degraded", "down", "asleep", "off"];
const LABELS = { up: "Up", degraded: "Slow", down: "Down", asleep: "Asleep", off: "Off" };

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Only https links are rendered: a feed string never selects a scheme of its own.
export function safeHref(u) {
  return typeof u === "string" && u.startsWith("https://") ? u : null;
}

export function pollSecs(doc) {
  const raw = doc && Number.isFinite(doc.interval_s) && doc.interval_s > 0 ? doc.interval_s : DEFAULT_INTERVAL_S;
  return Math.min(POLL_MAX_S, Math.max(POLL_MIN_S, Math.floor(raw)));
}

export function stateLabel(s) {
  return LABELS[s] || "Not reporting";
}

function stateBadge(s) {
  const k = STATES.includes(s) ? s : "unknown";
  return `<span class="st st-${k}">${esc(stateLabel(s))}</span>`;
}

function hostOf(u) {
  try {
    return new URL(u).host;
  } catch {
    return u;
  }
}

function counts(s) {
  return s.ready == null || s.desired == null ? "—" : `${s.ready}/${s.desired}`;
}

// Sum ready / sum desired over the services that actually reported; "no data" when none did.
function servicesText(services) {
  let ready = 0, desired = 0, seen = false;
  for (const s of services) {
    if (s.ready == null || s.desired == null) continue;
    ready += s.ready;
    desired += s.desired;
    seen = true;
  }
  return seen ? `${ready}/${desired} ready` : "no data";
}

function fleetText(f) {
  if (!f) return null;
  const n = (v) => (Number.isFinite(v) ? v : 0);
  const instances = n(f.instances), slots = n(f.slots), ready = n(f.ready), players = n(f.players), warm = n(f.warm);
  const parts = [
    `${instances} instance${instances === 1 ? "" : "s"}`,
    `${ready}/${slots} slots`,
    `${players} player${players === 1 ? "" : "s"}`,
  ];
  if (warm > 0) parts.push(`${warm} warming`);
  return parts.join(" · ");
}

export function viewModel(doc) {
  const d = doc && typeof doc === "object" ? doc : {};
  const now = Math.floor(Date.now() / 1000);
  const generated = Number.isFinite(d.generated) ? d.generated : null;
  const ageS = generated == null ? null : Math.max(0, now - generated);
  const interval = Number.isFinite(d.interval_s) && d.interval_s > 0 ? d.interval_s : DEFAULT_INTERVAL_S;
  const cards = (Array.isArray(d.games) ? d.games : []).map((g) => {
    const envs = (Array.isArray(g.envs) ? g.envs : []).map((e) => {
      const services = Array.isArray(e.services) ? e.services : [];
      return {
        id: e.id,
        name: e.name || e.id || "Environment",
        ns: e.ns || "",
        url: e.url ?? null,
        href: safeHref(e.url),
        state: e.state || "unknown",
        servicesText: servicesText(services),
        servicesDetail: services.map((s) => `${s.name} ${counts(s)}`).join(" · "),
        argoText: e.argo ? [e.argo.app, e.argo.sync, e.argo.health].filter(Boolean).join(" · ") : null,
        fleetText: fleetText(e.fleet),
      };
    });
    const prs = (Array.isArray(g.prs) ? g.prs : []).map((p) => {
      const preview = p.preview && typeof p.preview === "object"
        ? { url: p.preview.url ?? null, ns: p.preview.ns ?? null, state: p.preview.state || "unknown" }
        : null;
      return {
        number: p.number,
        title: p.title ?? "",
        draft: !!p.draft,
        url: p.url ?? null,
        href: safeHref(p.url),
        updated: p.updated ?? null,
        preview,
        previewHref: preview && preview.state !== "unknown" ? safeHref(preview.url) : null,
      };
    });
    return {
      id: g.id,
      name: g.name || g.id || "Unknown",
      kind: g.kind || "game",
      url: g.url ?? null,
      href: safeHref(g.url),
      state: g.state || "unknown",
      servers: Number.isFinite(g.servers) ? g.servers : 0,
      envs,
      prs,
    };
  });
  return {
    generated,
    ageS,
    // A 3 s feed would otherwise call itself stale after a single slow run.
    stale: ageS == null ? true : ageS > Math.max(interval * 3, 15),
    githubOk: !!(d.github && d.github.ok === true),
    githubError: d.github && typeof d.github.error === "string" ? d.github.error : null,
    source: d.source || "live",
    cards,
  };
}

function prHtml(p) {
  const number = `#${esc(p.number)}`;
  const text = p.title ? `${number} ${esc(p.title)}` : number;
  const link = p.href ? `<a href="${esc(p.href)}">${text}</a>` : text;
  const draft = p.draft ? ' <span class="chip">draft</span>' : "";
  let preview = "";
  if (p.preview) {
    const badge = stateBadge(p.preview.state);
    preview = p.previewHref
      ? ` <a href="${esc(p.previewHref)}">→ preview</a> ${badge}`
      : ` <span class="sub">preview: ${esc(stateLabel(p.preview.state))}</span>`;
  }
  return `<li>${link}${draft}${preview}</li>`;
}

function cardHtml(c, vm) {
  const head = c.href ? `<a href="${esc(c.href)}">${esc(c.name)}</a>` : esc(c.name);
  const subtitle = `${esc(c.kind)}${c.servers ? ` · ${c.servers} server${c.servers === 1 ? "" : "s"}` : ""}`;
  const rows = c.envs.map((e) => {
    const host = e.href ? `<a href="${esc(e.href)}">${esc(hostOf(e.href))}</a>` : "—";
    const argo = e.argoText ? `<span class="sub">argo ${esc(e.argoText)}</span>` : "";
    const detail = e.servicesDetail ? `<span class="sub">${esc(e.servicesDetail)}</span>` : "";
    return `<tr><th scope="row">${esc(e.name)}<span class="sub">${esc(e.ns)}</span></th>` +
      `<td>${host}</td><td>${stateBadge(e.state)}</td>` +
      `<td>${esc(e.servicesText)}${detail}${argo}</td></tr>`;
  }).join("");
  const table = c.envs.length
    ? `<table><caption>Environments</caption><thead><tr><th scope="col">Env</th><th scope="col">Host</th><th scope="col">State</th><th scope="col">Services</th></tr></thead><tbody>${rows}</tbody></table>`
    : `<p class="sub">No environments in the feed.</p>`;
  const fleets = c.envs
    .filter((e) => e.fleetText)
    .map((e) => `<p class="fleet">${esc(e.name)}: ${esc(e.fleetText)}</p>`)
    .join("");
  let prs;
  if (c.kind === "server") {
    prs = "";
  } else if (!vm.githubOk) {
    prs = `<p class="plain-note">PRs unavailable${vm.githubError ? ` (${esc(vm.githubError)})` : ""}</p>`;
  } else if (c.prs.length) {
    prs = `<ul class="prs">${c.prs.map(prHtml).join("")}</ul>`;
  } else {
    prs = `<p class="sub">No open pull requests.</p>`;
  }
  return `<article class="card" id="game-${esc(c.id)}"><h2>${head} ${stateBadge(c.state)}</h2>` +
    `<p class="sub">${subtitle}</p>${table}${fleets}${prs}</article>`;
}

function plainHtml(vm) {
  const games = vm.cards.map((c) => {
    const name = c.href ? `<a href="${esc(c.href)}">${esc(c.name)}</a>` : esc(c.name);
    return `<tr id="row-game-${esc(c.id)}"><th scope="row">${name}</th><td>${esc(c.kind)}</td>` +
      `<td>${stateBadge(c.state)}</td><td>${c.servers}</td><td>${c.prs.length}</td></tr>`;
  }).join("");
  const envs = vm.cards.flatMap((c) => c.envs.map((e) => {
    const host = e.href ? `<a href="${esc(e.href)}">${esc(hostOf(e.href))}</a>` : "—";
    return `<tr><th scope="row">${esc(c.name)}</th><td>${esc(e.name)}</td><td>${esc(e.ns)}</td>` +
      `<td>${host}</td><td>${stateBadge(e.state)}</td><td>${esc(e.servicesText)}</td>` +
      `<td>${e.fleetText ? esc(e.fleetText) : "—"}</td></tr>`;
  })).join("");
  const prs = vm.cards.flatMap((c) => c.prs.map((p) => {
    const text = p.title ? `#${esc(p.number)} ${esc(p.title)}` : `#${esc(p.number)}`;
    const link = p.href ? `<a href="${esc(p.href)}">${text}</a>` : text;
    const preview = p.previewHref ? `<a href="${esc(p.previewHref)}">${esc(hostOf(p.previewHref))}</a>` : "—";
    return `<tr><th scope="row">${esc(c.name)}</th><td>${link}</td><td>${p.draft ? "draft" : "ready"}</td>` +
      `<td>${esc(stateLabel(p.preview ? p.preview.state : "unknown"))}</td><td>${preview}</td></tr>`;
  })).join("");
  const note = vm.githubOk ? "" : `<p class="plain-note">PRs unavailable${vm.githubError ? ` (${esc(vm.githubError)})` : ""}.</p>`;
  return `<h2 id="plain-h">All games as tables</h2>${note}
<table><caption>Games</caption><thead><tr><th scope="col">Game</th><th scope="col">Kind</th><th scope="col">State</th><th scope="col">Servers</th><th scope="col">Open PRs</th></tr></thead><tbody>${games}</tbody></table>
<table><caption>Environments</caption><thead><tr><th scope="col">Game</th><th scope="col">Env</th><th scope="col">Namespace</th><th scope="col">Host</th><th scope="col">State</th><th scope="col">Services</th><th scope="col">Fleet</th></tr></thead><tbody>${envs}</tbody></table>
<table><caption>Open pull requests</caption><thead><tr><th scope="col">Game</th><th scope="col">PR</th><th scope="col">Status</th><th scope="col">Preview state</th><th scope="col">Preview</th></tr></thead><tbody>${prs}</tbody></table>`;
}

export function render(doc, els) {
  const vm = viewModel(doc);
  if (els.cards) {
    els.cards.innerHTML = vm.cards.length
      ? vm.cards.map((c) => cardHtml(c, vm)).join("")
      : '<p class="sub">The feed lists no games.</p>';
  }
  if (els.plain) els.plain.innerHTML = plainHtml(vm);
  if (els.asof) {
    // The committed sample's `generated` is frozen at build time, so an age line
    // would call it stale forever; name the source instead.
    els.asof.textContent = vm.source === "sample"
      ? "committed sample — not live data"
      : vm.ageS == null
        ? (vm.stale ? "feed is stale" : "")
        : `${vm.stale ? "feed is stale — last update" : "updated"} ${vm.ageS}s ago`;
  }
  return vm;
}

export async function load(els) {
  const forceSample = new URLSearchParams(location.search).get("source") === "sample";
  let last = null;
  const setBanner = (msg) => {
    if (!els.banner) return;
    els.banner.textContent = msg;
    els.banner.hidden = !msg;
  };
  const getJson = async (url) => {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
    return res.json();
  };
  const show = (doc) => {
    last = doc;
    render(doc, els);
    setBanner("");
    return doc;
  };
  const tick = async () => {
    if (forceSample) {
      try {
        return show(await getJson("games-sample.json"));
      } catch (err) {
        setBanner(`the committed sample is unavailable (${err.message})`);
        return null;
      }
    }
    try {
      return show(await getJson("games-live.json"));
    } catch (err) {
      if (last) {
        setBanner(`live feed unavailable (${err.message}); showing the last good copy`);
        return last;
      }
      try {
        show(await getJson("games-sample.json"));
        setBanner("live feed unavailable, showing the committed sample");
        return last;
      } catch (err2) {
        setBanner(`live feed and committed sample are unavailable (${err2.message})`);
        return null;
      }
    }
  };
  let timer = null;
  const loop = async () => {
    if (!document.hidden) await tick();
    timer = setTimeout(loop, pollSecs(last) * 1000);
  };
  await loop();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      clearTimeout(timer);
      loop();
    }
  });
  return { doc: () => last };
}
