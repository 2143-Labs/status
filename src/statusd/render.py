"""render_page(model, build_sha) -> str: the whole public page, server-rendered.

Pure: no clock, no DB, no I/O except reading style.css once at import. No <script>
and no external URL other than the monitored sites' own links.
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

CSS = (Path(__file__).with_name("style.css")).read_text()
STALE_S = 120
INCIDENT_ROWS = 20
# The one rect every daily bar <use>s; it carries no fill, so each bar's class colours it.
BAR_DEFS = '<svg class="defs" aria-hidden="true"><defs><rect id="bar" width="4" height="24"/></defs></svg>'


def fmt_time(ts: int, tz: ZoneInfo) -> str:
    d = datetime.fromtimestamp(ts, tz)
    return f"{d:%b} {d.day}, {d:%H:%M %Z}"


def fmt_date(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def fmt_duration(secs: int) -> str:
    secs = max(0, int(secs))
    if secs < 60:
        return "<1 min"
    if secs < 3600:
        return f"{secs // 60} min"
    if secs < 86400:
        return f"{secs // 3600} h {secs % 3600 // 60} min"
    return f"{secs // 86400} d {secs % 86400 // 3600} h"


def fmt_pct(p: float | None) -> str:
    if p is None:
        return "--"
    return "100%" if p == 100 else f"{p:.2f}%"


def _plural(n: int, word: str) -> str:
    return f"{n} service {word}" if n == 1 else f"{n} services {word}"


def headline(m: dict) -> str:
    states = [c["state"] for c in m["components"]]
    if m["sample_age_s"] is None or m["sample_age_s"] > STALE_S:
        return "Status checks are stale"
    if "down" in states:
        return _plural(states.count("down"), "down")
    if "degraded" in states:
        return _plural(states.count("degraded"), "degraded")
    if m["home_age_s"] is None or m["home_age_s"] > STALE_S:
        return "Home monitor not reporting"
    return "All systems operational"


def _bar_class(p: float | None) -> str:
    if p is None:
        return "b-none"
    if p >= 99.9:
        return "b-ok"
    if p >= 99:
        return "b-minor"
    return "b-major"


def _bars(c: dict, first_day: date) -> str:
    figure = c["uptime"]["90d"] if c["uptime"]["90d"] is not None else c["uptime"]["30d"]
    label = f'{c["name"]}: 90-day uptime {fmt_pct(figure) if figure is not None else "no data"}'
    out = [f'<svg viewBox="0 0 450 24" role="img" aria-label="{escape(label)}">']
    for i, p in enumerate(c["daily_pct"]):
        d = first_day + timedelta(days=i)
        tip = f'{d:%b} {d.day}: {fmt_pct(p) if p is not None else "no data"}'
        out.append(f'<use href="#bar" x="{i * 5}" class="{_bar_class(p)}"><title>{escape(tip)}</title></use>')
    out.append("</svg>")
    return "".join(out)


def _sparkline(points: list) -> str:
    vals = [v for v in points if v is not None]
    if not vals:
        return '<p class="note">No latency data in the last 24 h.</p>'
    top = max(vals) or 1
    runs, cur = [], []
    for i, v in enumerate(points):
        if v is None:
            if cur:
                runs.append(cur)
            cur = []
        else:
            cur.append(f"{i * 2},{round(39 - v / top * 38)}")
    if cur:
        runs.append(cur)
    title = f"median {round(statistics.median(vals))} ms, max {round(max(vals))} ms"
    lines = "".join(f'<polyline points="{" ".join(r)}"/>' for r in runs)
    return f'<svg class="spark" viewBox="0 0 288 40" role="img" aria-label="24-hour latency"><title>{title}</title>{lines}</svg>'


def _component(c: dict, first_day: date, tz: ZoneInfo) -> str:
    name = escape(c["name"])
    if c.get("url"):
        name = f'<a href="{escape(c["url"])}">{name}</a>'
    up = c["uptime"]
    dl = [f"<dt>Uptime {k}</dt><dd>{fmt_pct(up[key])}</dd>"
          for k, key in (("24 h", "24h"), ("7 d", "7d"), ("30 d", "30d"), ("90 d", "90d"))]
    latency = "--" if c["latency_ms"] is None else f"{c['latency_ms']:g} ms"
    dl.append(f"<dt>Latency</dt><dd>{latency}</dd>")
    if c.get("url"):
        tls = "--" if c["cert_days"] is None else f"{c['cert_days']} days left"
        dl.append(f"<dt>TLS certificate</dt><dd>{tls}</dd>")
    since = fmt_date(datetime.fromtimestamp(c["since"], tz).date()) if c["since"] is not None else "no data yet"
    dl.append(f"<dt>Data since</dt><dd>{since}</dd>")
    return (
        f'<details><summary><span class="row"><span class="name">{name}</span>'
        f'<span class="state s-{c["state"]}">{c["state"]}</span>'
        f'<span class="up30">{fmt_pct(up["30d"])} 30 d</span></span>{_bars(c, first_day)}</summary>'
        f'<dl>{"".join(dl)}</dl>{_sparkline(c["latency_10m"])}</details>'
    )


def _banners(m: dict, tz: ZoneInfo) -> str:
    out = []
    h = m["home_age_s"]
    if h is None:
        out.append("Home monitor has not reported; home services show as unknown.")
    elif h > STALE_S:
        out.append(f"Home monitor last reported {fmt_duration(h)} ago; home services show as unknown.")
    s = m["sample_age_s"]
    if s is None:
        out.append("Outside checks have not run yet; websites show as unknown.")
    elif s > STALE_S:
        out.append(f"Outside checks last ran {fmt_duration(s)} ago; websites show as unknown.")
    return "".join(f'<p class="banner">{escape(b)}</p>' for b in out)


def _incidents(m: dict, tz: ZoneInfo, now: int) -> str:
    names = {c["id"]: c["name"] for c in m["components"]}
    rows = m["outages"][:INCIDENT_ROWS]
    if not rows:
        return "<h2>Incidents</h2><p>No incidents in the last 90 days.</p>"
    body = "".join(
        f'<tr><td>{escape(names.get(o["id"], o["id"]))}</td><td>{fmt_time(o["start"], tz)}</td>'
        f'<td>{"ongoing" if o["end"] is None else fmt_duration(o["end"] - o["start"])}</td></tr>'
        for o in rows
    )
    return ("<h2>Incidents</h2><table><thead><tr><th>Service</th><th>Started</th><th>Duration</th></tr></thead>"
            f"<tbody>{body}</tbody></table>")


def render_page(model: dict, build_sha: str) -> str:
    m = model
    tz = ZoneInfo(m["tz"])
    now = m["generated"]
    first_day = date.fromisoformat(m["days_start"])
    sections = []
    for g in m["groups"]:
        comps = "".join(_component(c, first_day, tz) for c in m["components"] if c["group"] == g["id"])
        sections.append(f'<section><h2>{escape(g["name"])}</h2><p class="note">{escape(g["note"])}</p>{comps}</section>')
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="refresh" content="60"><meta name="color-scheme" content="light dark">'
        '<title>2143 Labs status</title><link rel="alternate" type="application/atom+xml" href="/feed.xml">'
        f"<style>{CSS}</style></head><body><main>"
        f'{BAR_DEFS}<h1>2143 Labs status</h1><p class="headline">{escape(headline(m))}</p>'
        f'<p class="meta">Updated {fmt_time(now, tz)} · checks every {m["probe_interval_s"]} s from Toronto and from home</p>'
        f"{_banners(m, tz)}{''.join(sections)}{_incidents(m, tz, now)}"
        f'<footer><a href="/status.json">status.json</a> · <a href="/feed.xml">Atom feed</a> · '
        f"build <code>{escape(build_sha[:7])}</code> · Times in {escape(m['tz'])}</footer>"
        "</main></body></html>\n"
    )
