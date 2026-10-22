"""render_page(model, build_sha) -> str: the whole public page, server-rendered.

No scripts, no inline handlers, no external requests: one document that works
with HTML and CSS alone. Two rules shape it. Every component gets a row, so the
page reads the same whether six or sixty things are monitored. And the page
never implies more than it knows: a day the monitor did not report is drawn
differently from a day before this record existed, and the history strip says
how far back the data actually goes.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

CSS = (Path(__file__).with_name("style.css")).read_text()
STALE_S = 120
INCIDENT_ROWS = 20
BARS = 90
BAR_W = 5  # bar width plus its gap, in viewBox units
RECENT_S = 86400

# The rect every daily bar <use>s carries no fill, so each bar's class colours it.
# `hatch` is "the monitor did not report that day"; b-pre is a flatter grey for
# "this record did not exist yet". Same emptiness, different causes, different look.
BAR_DEFS = (
    '<svg class="defs" aria-hidden="true"><defs>'
    '<rect id="bar" width="4" height="24"/>'
    f'<pattern id="hatch" width="4" height="4" patternUnits="userSpaceOnUse" '
    'patternTransform="rotate(45)">'
    '<line x1="0" y1="0" x2="0" y2="4"/></pattern>'  # stroke comes from CSS: variables do not resolve in presentation attributes
    "</defs></svg>"
)
PILL = {
    "up": ("ok", "up"),
    "degraded": ("warn", "degraded"),
    "down": ("bad", "down"),
    "unknown": ("muted", "unknown"),
}


def fmt_time(ts: int, tz: ZoneInfo) -> str:
    d = datetime.fromtimestamp(ts, tz)
    return f"{d:%b} {d.day}, {d:%H:%M %Z}"


def fmt_date(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def fmt_duration(secs: int) -> str:
    secs = max(0, int(secs))
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days} d {hours} h" if hours else f"{days} d"
    if hours:
        return f"{hours} h {mins} m" if mins else f"{hours} h"
    return f"{mins} m" if mins else f"{secs} s"


def fmt_pct(p: float | None) -> str:
    if p is None:
        return "--"
    return "100%" if p == 100 else f"{p:.2f}%"


def _plural(n: int, word: str) -> str:
    return f"{n} service {word}" if n == 1 else f"{n} services {word}"


def headline(m: dict) -> tuple[str, str]:
    """(text, severity class). Ordered by how much the reader needs to know it."""
    states = [c["state"] for c in m["components"]]
    if m["sample_age_s"] is None or m["sample_age_s"] > STALE_S:
        return "Status checks are stale", "warn"
    if "down" in states:
        return _plural(states.count("down"), "down"), "bad"
    if "degraded" in states:
        return _plural(states.count("degraded"), "degraded"), "warn"
    if m["home_age_s"] is None or m["home_age_s"] > STALE_S:
        return "Home monitor not reporting", "warn"
    return "All systems operational", "ok"


def history_start(m: dict) -> int | None:
    """The oldest sample any component has: the moment this record begins."""
    stamps = [c["since"] for c in m["components"] if c.get("since") is not None]
    return min(stamps) if stamps else None


def _pre_until(m: dict, first_day: date, tz: ZoneInfo) -> int:
    """Bars before this index predate the record; bars after it should have data."""
    hs = history_start(m)
    if hs is None:
        return 0
    return max(0, min(BARS, (datetime.fromtimestamp(hs, tz).date() - first_day).days))


def _bar_class(p: float | None) -> str:
    if p is None:
        return "b-none"
    if p >= 99.9:
        return "b-ok"
    if p >= 99:
        return "b-minor"
    return "b-major"


def _bars(c: dict, first_day: date, pre_until: int) -> str:
    days = c["daily_pct"]
    figure = c["uptime"]["90d"] if c["uptime"]["90d"] is not None else c["uptime"]["30d"]
    shown = fmt_pct(figure) if figure is not None else "no data"
    label = f'{c["name"]}: 90-day uptime {shown}'
    out = [f'<svg class="bars" viewBox="0 0 {len(days) * BAR_W} 24" role="img" '
           f'aria-label="{escape(label)}">']
    for i, p in enumerate(days):
        d = first_day + timedelta(days=i)
        if i < pre_until:
            cls, what = "b-pre", "no history yet"
        else:
            cls, what = _bar_class(p), fmt_pct(p) if p is not None else "not reported"
        out.append(f'<use href="#bar" x="{i * BAR_W}" class="{cls}">'
                   f"<title>{escape(f'{d:%b} {d.day}: {what}')}</title></use>")
    out.append("</svg>")
    return "".join(out)


def _pill(c: dict) -> str:
    cls, label = PILL[c["state"]]
    # A silent home monitor means no report arrived; a stale droplet means the
    # outside check did not run. Both are unknown, and the row should say which.
    if c["state"] == "unknown" and not c.get("url"):
        label = "awaiting report"
    return f'<span class="pill {cls}">{label}</span>'


def _row(c: dict, first_day: date, pre_until: int) -> str:
    name = escape(c["name"])
    if c.get("url"):
        name = f'<a href="{escape(c["url"])}">{name}</a>'
    sub = escape(c["hosted"])
    if c.get("cert_days") is not None:
        sub += f' · cert {c["cert_days"]} d'
    up = c["uptime"]
    latency = "--" if c["latency_ms"] is None else f"{c['latency_ms']:g} ms"
    cells = "".join(f'<td class="num">{fmt_pct(up[k])}</td>' for k in ("24h", "7d", "30d"))
    return (f'<tr><td><span class="nm">{name}</span><span class="sub">{sub}</span></td>'
            f'<td>{_pill(c)}</td>{cells}<td class="num">{latency}</td>'
            f"<td>{_bars(c, first_day, pre_until)}</td></tr>")


def _cards(m: dict, tz: ZoneInfo) -> str:
    out = []
    for g in m["groups"]:
        comps = [c for c in m["components"] if c["group"] == g["id"]]
        if not comps:
            continue
        n = len(comps)
        up = sum(c["state"] == "up" for c in comps)
        down = sum(c["state"] == "down" for c in comps)
        if down:
            cls, hint = "bad", f"{down} down"
        elif up == n:
            cls, hint = "", "all up"
        else:
            cls, hint = "warn", f"{n - up} not reporting"
        out.append(f'<div class="{cls}"><b>{up} / {n}</b>{escape(g["name"])} · {hint}</div>')
    hs = history_start(m)
    if hs is None:
        out.append('<div><b>--</b>no history yet</div>')
    else:
        out.append(f'<div><b>{fmt_duration(m["generated"] - hs)}</b>of history · '
                   f'since {fmt_date(datetime.fromtimestamp(hs, tz).date())}</div>')
    age = m["sample_age_s"]
    out.append(f'<div><b>{"--" if age is None else f"{age} s"}</b>since the last check</div>')
    return '<div class="cards">' + "".join(out) + "</div>"


def _legend() -> str:
    return ('<p class="legend">'
            '<span><i class="sw ok"></i>up all day</span>'
            '<span><i class="sw minor"></i>partial</span>'
            '<span><i class="sw major"></i>outage</span>'
            '<span><i class="sw none"></i>monitor silent</span>'
            '<span><i class="sw pre"></i>before this record</span></p>')


def _banners(m: dict, tz: ZoneInfo, now: int) -> str:
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


def _recent_incident(m: dict, tz: ZoneInfo, now: int) -> str:
    """The incident a reader is most likely asking about: the one that just ended."""
    names = {c["id"]: c["name"] for c in m["components"]}
    live = [o for o in m["outages"] if o["end"] is None]
    if live:
        o = live[0]
        return (f'<p class="banner bad"><strong>{escape(names.get(o["id"], o["id"]))}</strong> '
                f'is down, ongoing for {fmt_duration(now - o["start"])}. '
                f'<a href="#incidents">Incidents</a>.</p>')
    recent = [o for o in m["outages"] if o["end"] is not None and o["end"] >= now - RECENT_S]
    if not recent:
        return ""
    o = max(recent, key=lambda x: x["end"] - x["start"])
    return (f'<p class="banner"><strong>{escape(names.get(o["id"], o["id"]))}</strong> was '
            f'unreachable for {fmt_duration(o["end"] - o["start"])}, ending {fmt_time(o["end"], tz)}. '
            f'<a href="#incidents">All incidents</a>.</p>')


def _sections(m: dict, first_day: date, pre_until: int) -> str:
    out = []
    for g in m["groups"]:
        rows = "".join(_row(c, first_day, pre_until)
                       for c in m["components"] if c["group"] == g["id"])
        if not rows:
            continue
        out.append(
            f'<section><h2>{escape(g["name"])}</h2>'
            f'<p class="note">{escape(g["note"])}</p>'
            '<div class="tw"><table><thead><tr><th>Component</th><th>State</th>'
            '<th class="num">24 h</th><th class="num">7 d</th><th class="num">30 d</th>'
            f'<th class="num">Latency</th><th>History ({BARS} d)</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div></section>")
    return "".join(out)


def _incidents(m: dict, tz: ZoneInfo, now: int) -> str:
    names = {c["id"]: c["name"] for c in m["components"]}
    rows = m["outages"][:INCIDENT_ROWS]
    if not rows:
        return '<h2 id="incidents">Incidents</h2><p>No incidents in the last 90 days.</p>'
    body = []
    for o in rows:
        ongoing = o["end"] is None
        ended = "--" if ongoing else fmt_time(o["end"], tz)
        length = fmt_duration((now if ongoing else o["end"]) - o["start"])
        cls, word = ("bad", "ongoing") if ongoing else ("ok", "resolved")
        body.append(f'<tr><td>{escape(names.get(o["id"], o["id"]))}</td>'
                    f'<td class="num">{fmt_time(o["start"], tz)}</td>'
                    f'<td class="num">{ended}</td><td class="num">{length}</td>'
                    f'<td><span class="pill {cls}">{word}</span></td></tr>')
    return ('<h2 id="incidents">Incidents</h2><div class="tw"><table><thead><tr>'
            '<th>Component</th><th>Started</th><th>Ended</th><th>Duration</th><th>State</th>'
            f"</tr></thead><tbody>{''.join(body)}</tbody></table></div>")


def render_page(model: dict, build_sha: str) -> str:
    m = model
    tz = ZoneInfo(m["tz"])
    now = m["generated"]
    first_day = date.fromisoformat(m["days_start"])
    pre_until = _pre_until(m, first_day, tz)
    text, cls = headline(m)
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="refresh" content="60"><meta name="color-scheme" content="light dark">'
        '<title>2143 Labs status</title><link rel="alternate" type="application/atom+xml" href="/feed.xml">'
        f"<style>{CSS}</style></head><body><main>"
        f"{BAR_DEFS}<h1>2143 Labs status</h1>"
        f'<p class="headline {cls}">{escape(text)}</p>'
        f'<p class="meta">Updated {fmt_time(now, tz)} · checks every '
        f'{m["probe_interval_s"]} s from Toronto and from home</p>'
        f'{_banners(m, tz, now)}{_cards(m, tz)}'
        f"{_legend()}{_recent_incident(m, tz, now)}"
        f"{_sections(m, first_day, pre_until)}{_incidents(m, tz, now)}"
        f'<footer><a href="/status.json">status.json</a> · <a href="/feed.xml">Atom feed</a> · '
        f'build <code>{escape(build_sha[:7])}</code> · Times in {escape(m["tz"])}</footer>'
        "</main></body></html>\n"
    )
