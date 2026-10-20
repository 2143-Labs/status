"""render_feed(model) -> str: Atom feed of the newest incidents."""
from __future__ import annotations

from datetime import datetime, timezone
from xml.sax.saxutils import escape

from .render import fmt_duration

FEED_ID = "tag:status.2143.me,2026:feed"
ENTRIES = 20


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def render_feed(model: dict) -> str:
    names = {c["id"]: c["name"] for c in model["components"]}
    entries = []
    for o in model["outages"][:ENTRIES]:
        name = names.get(o["id"], o["id"])
        ongoing = o["end"] is None
        title = f"{name} down (ongoing)" if ongoing else f"{name} down"
        summary = f"Started {_iso(o['start'])}; " + (
            "still ongoing." if ongoing else f"ended {_iso(o['end'])} after {fmt_duration(o['end'] - o['start'])}.")
        entries.append(
            f"<entry><id>tag:status.2143.me,2026:{escape(o['id'])}/{o['start']}</id>"
            f"<title>{escape(title)}</title><updated>{_iso(o['start'] if ongoing else o['end'])}</updated>"
            f'<link href="https://status.2143.me/"/><summary>{escape(summary)}</summary></entry>'
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">'
        f"<id>{FEED_ID}</id><title>2143 Labs status — incidents</title>"
        f"<updated>{_iso(model['generated'])}</updated>"
        '<link href="https://status.2143.me/"/><link rel="self" href="https://status.2143.me/feed.xml"/>'
        f"<author><name>2143 Labs</name></author>{''.join(entries)}</feed>\n"
    )
