"""Load config/statusd.toml and config/components.toml into frozen dataclasses."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass

EXPECT_RE = re.compile(r"^\d{3}(-\d{3})?$")
DEFAULT_EXPECT = ("200-399",)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Group:
    id: str
    name: str
    note: str


@dataclass(frozen=True)
class Component:
    id: str
    name: str
    group: str
    kind: str  # "http" | "lan"
    hosted: str  # "home" | "cloud"
    url: str | None
    expect: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    listen_host: str
    listen_port: int
    token_sha256: str
    db: str
    www: str
    snapshots: str
    deploy_failed: str
    interval_seconds: int
    timeout_seconds: int
    user_agent: str
    down_after_failures: int
    stale_seconds: int
    tz: str
    groups: tuple[Group, ...]
    components: tuple[Component, ...]

    def component(self, cid: str) -> Component | None:
        for c in self.components:
            if c.id == cid:
                return c
        return None


def expect_ok(expect: tuple[str, ...], status: int) -> bool:
    for e in expect:
        lo, _, hi = e.partition("-")
        if int(lo) <= status <= int(hi or lo):
            return True
    return False


def _load_toml(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e


def _get(d: dict, section: str, key: str, typ: type):
    try:
        v = d[section][key]
    except KeyError:
        raise ConfigError(f"missing [{section}] {key}") from None
    if not isinstance(v, typ) or (typ is int and isinstance(v, bool)):
        raise ConfigError(f"[{section}] {key} must be {typ.__name__}")
    return v


def load(config_path: str, components_path: str) -> Config:
    d = _load_toml(config_path)
    listen = _get(d, "server", "listen", str)
    host, sep, port = listen.rpartition(":")
    if not sep or not host or not port.isdigit():
        raise ConfigError(f"[server] listen must be host:port, got {listen!r}")
    token = _get(d, "auth", "token_sha256", str)
    if token and not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ConfigError("[auth] token_sha256 must be 64 lowercase hex characters or empty")

    groups, components = _load_components(components_path)
    return Config(
        listen_host=host,
        listen_port=int(port),
        token_sha256=token,
        db=_get(d, "paths", "db", str),
        www=_get(d, "paths", "www", str),
        snapshots=_get(d, "paths", "snapshots", str),
        deploy_failed=_get(d, "paths", "deploy_failed", str),
        interval_seconds=_get(d, "probe", "interval_seconds", int),
        timeout_seconds=_get(d, "probe", "timeout_seconds", int),
        user_agent=_get(d, "probe", "user_agent", str),
        down_after_failures=_get(d, "probe", "down_after_failures", int),
        stale_seconds=_get(d, "probe", "stale_seconds", int),
        tz=_get(d, "page", "tz", str),
        groups=groups,
        components=components,
    )


def _load_components(path: str) -> tuple[tuple[Group, ...], tuple[Component, ...]]:
    d = _load_toml(path)
    groups: list[Group] = []
    for g in d.get("group", []):
        try:
            groups.append(Group(id=g["id"], name=g["name"], note=g.get("note", "")))
        except (KeyError, TypeError):
            raise ConfigError(f"group needs id and name: {g!r}") from None
    group_ids = {g.id for g in groups}
    if len(group_ids) != len(groups):
        raise ConfigError("duplicate group id")

    comps: list[Component] = []
    seen: set[str] = set()
    for c in d.get("component", []):
        if not isinstance(c, dict) or not all(isinstance(c.get(k), str) for k in ("id", "name", "group", "kind", "hosted")):
            raise ConfigError(f"component needs string id, name, group, kind, hosted: {c!r}")
        cid = c["id"]
        if cid in seen:
            raise ConfigError(f"duplicate component id {cid!r}")
        seen.add(cid)
        if c["group"] not in group_ids:
            raise ConfigError(f"{cid}: unknown group {c['group']!r}")
        kind = c["kind"]
        if kind not in ("http", "lan"):
            raise ConfigError(f"{cid}: kind must be http or lan, got {kind!r}")
        url = c.get("url")
        if kind == "http" and not isinstance(url, str):
            raise ConfigError(f"{cid}: http component needs url")
        if kind == "lan" and url is not None:
            raise ConfigError(f"{cid}: lan component must not have url")
        if c["hosted"] not in ("home", "cloud"):
            raise ConfigError(f"{cid}: hosted must be home or cloud, got {c['hosted']!r}")
        expect = c.get("expect", list(DEFAULT_EXPECT))
        if not isinstance(expect, list) or not expect:
            raise ConfigError(f"{cid}: expect must be a non-empty list of strings")
        for e in expect:
            if not isinstance(e, str) or not EXPECT_RE.match(e):
                raise ConfigError(f"{cid}: bad expect entry {e!r} (want \"200\" or \"200-399\")")
        comps.append(Component(cid, c["name"], c["group"], kind, c["hosted"], url, tuple(expect)))
    return tuple(groups), tuple(comps)
