import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { viewModel, stateLabel, safeHref, pollSecs, render } from "../../designs/games-core.js";

const sample = JSON.parse(readFileSync(new URL("../../designs/games-sample.json", import.meta.url), "utf8"));
const IDS = ["rots", "dino", "moss", "steam-lobby", "how-poe-sale", "factorio", "minecraft", "teamspeak", "openrct2", "pelican"];

const card = (vm, id) => vm.cards.find((c) => c.id === id);

test("sample renders one card per game, in contract order", () => {
  const vm = viewModel(sample);
  assert.equal(sample.v, 1);
  assert.equal(sample.source, "sample");
  assert.equal(vm.cards.length, 10);
  assert.deepEqual(vm.cards.map((c) => c.id), IDS);
  assert.equal(vm.source, "sample");
  assert.equal(vm.githubOk, true);
  assert.equal(vm.githubError, null);
});

test("asleep reaches the card through the env", () => {
  const vm = viewModel(sample);
  const dino = card(vm, "dino");
  const prod = dino.envs.find((e) => e.id === "prod");
  assert.equal(prod.state, "asleep");
  assert.equal(prod.servicesText, "0/0 ready");
  assert.equal(dino.state, "asleep");
  assert.ok(vm.cards.some((c) => c.envs.some((e) => e.state === "asleep")));
});

test("down and off appear too", () => {
  const vm = viewModel(sample);
  assert.equal(card(vm, "moss").state, "down");
  assert.equal(card(vm, "factorio").state, "off");
});

test("services text sums ready and desired", () => {
  const vm = viewModel(sample);
  const rots = card(vm, "rots");
  assert.equal(rots.envs[0].servicesText, "6/6 ready");
  assert.equal(rots.envs[0].servicesDetail, "web 1/1 · authserver 2/2 · game servers 2/2 · postgres 1/1");
});

test("services without numbers report no data", () => {
  const doc = {
    v: 1, source: "live", generated: 1, interval_s: 60, github: { ok: false, error: "no token" }, games: [
      { id: "x", name: "X", kind: "server", url: null, repo: null, state: "unknown", servers: 0,
        envs: [{ id: "prod", name: "Production", ns: "default", url: null, state: "unknown", argo: null, services: [], fleet: null }],
        prs: [] },
    ],
  };
  const vm = viewModel(doc);
  assert.equal(vm.githubOk, false);
  assert.equal(vm.githubError, "no token");
  assert.equal(vm.cards[0].envs[0].servicesText, "no data");
});

test("fleet line", () => {
  const vm = viewModel(sample);
  assert.equal(card(vm, "rots").envs[0].fleetText, "2 instances · 2/64 slots · 5 players");
  assert.equal(card(vm, "dino").envs[0].fleetText, null);
});

test("prs: preview link only when the preview reports a state", () => {
  const vm = viewModel(sample);
  const prs = vm.cards.flatMap((c) => c.prs);
  const noPreview = prs.find((p) => p.preview === null);
  assert.ok(noPreview, "sample has a PR with preview: null");
  assert.equal(noPreview.previewHref, null);
  const withPreview = prs.find((p) => p.preview && p.preview.state === "up");
  assert.ok(withPreview);
  assert.equal(withPreview.previewHref, withPreview.preview.url);
  const unknown = prs.find((p) => p.preview && p.preview.state === "unknown");
  assert.ok(unknown, "sample has a PR whose preview is not reporting");
  assert.equal(unknown.previewHref, null);
  const draft = prs.find((p) => p.draft);
  assert.ok(draft);
});

test("server-kind games carry no PRs and no repo link", () => {
  const vm = viewModel(sample);
  for (const id of ["factorio", "minecraft", "teamspeak", "openrct2", "pelican"]) {
    const c = card(vm, id);
    assert.equal(c.kind, "server");
    assert.deepEqual(c.prs, []);
  }
  assert.equal(card(vm, "minecraft").href, null);
  assert.equal(card(vm, "minecraft").envs[0].href, null);
});

test("stateLabel", () => {
  assert.equal(stateLabel("up"), "Up");
  assert.equal(stateLabel("degraded"), "Slow");
  assert.equal(stateLabel("down"), "Down");
  assert.equal(stateLabel("asleep"), "Asleep");
  assert.equal(stateLabel("off"), "Off");
  assert.equal(stateLabel("unknown"), "Not reporting");
  assert.equal(stateLabel("nonsense"), "Not reporting");
  assert.equal(stateLabel(undefined), "Not reporting");
});

test("safeHref only allows https", () => {
  assert.equal(safeHref("https://github.com/2143-Labs/rots4/pull/7"), "https://github.com/2143-Labs/rots4/pull/7");
  assert.equal(safeHref("javascript:alert(1)"), null);
  assert.equal(safeHref("http://rots.john2143.com"), null);
  assert.equal(safeHref("//rots.john2143.com"), null);
  assert.equal(safeHref(""), null);
  assert.equal(safeHref(null), null);
  assert.equal(safeHref(42), null);
});

test("a feed string cannot smuggle a link", () => {
  const doc = {
    v: 1, source: "live", generated: 1, interval_s: 60, github: { ok: true, error: null }, games: [
      { id: "x", name: "X", kind: "game", url: "javascript:alert(1)", repo: "2143-Labs/x", state: "up", servers: 0,
        envs: [{ id: "prod", name: "Production", ns: "ns", url: "http://insecure", state: "up", argo: null, services: [], fleet: null }],
        prs: [{ number: 7, title: "t", draft: false, url: "javascript:alert(1)", updated: 1,
          preview: { url: "data:text/html,x", ns: "ns-pr-7", state: "up" } }] },
    ],
  };
  const vm = viewModel(doc);
  assert.equal(vm.cards[0].href, null);
  assert.equal(vm.cards[0].envs[0].href, null);
  assert.equal(vm.cards[0].prs[0].href, null);
  assert.equal(vm.cards[0].prs[0].previewHref, null);
});

test("staleness follows 3x the interval", () => {
  const now = Math.floor(Date.now() / 1000);
  const doc = (age) => ({ v: 1, source: "live", generated: now - age, interval_s: 60, github: { ok: true, error: null }, games: [] });
  assert.equal(viewModel(doc(120)).stale, false);
  assert.equal(viewModel(doc(240)).stale, true);
  assert.equal(viewModel({ v: 1, games: [] }).stale, true);
});

test("poll cadence follows the feed and stays in bounds", () => {
  assert.equal(pollSecs({ interval_s: 3 }), 3);
  // No usable interval_s falls back to DEFAULT_INTERVAL_S, which the ceiling clamps.
  assert.equal(pollSecs({ interval_s: 0 }), 30);
  assert.equal(pollSecs({}), 30);
  assert.equal(pollSecs({ interval_s: 0.4 }), 1);
  assert.equal(pollSecs({ interval_s: 600 }), 30);
});

test("a fresh feed is not called stale, and the age line reports it", () => {
  const now = Math.floor(Date.now() / 1000);
  const doc = { v: 1, source: "live", generated: now - 4, interval_s: 3, github: { ok: true, error: null }, games: [] };
  assert.equal(viewModel(doc).stale, false);
  const els = { asof: { textContent: "" } };
  render(doc, els);
  assert.match(els.asof.textContent, /updated \d+s ago/);
  const old = { ...doc, generated: now - 40 };
  render(old, { asof: els.asof });
  assert.match(els.asof.textContent, /stale/);
});

test("the committed sample names itself instead of a frozen age", () => {
  const els = { asof: { textContent: "" } };
  render(sample, els);
  assert.match(els.asof.textContent, /committed sample/);
});
