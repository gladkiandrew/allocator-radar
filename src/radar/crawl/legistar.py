"""T-LEG: Legistar boards via the public read-only Web API (GET JSON; no token needed for these clients).

A listing "page" for a Legistar board is stored as
  https://webapi.legistar.com/v1/<client>/events?body=<BodyId>
Events in the history window yield the agenda and minutes files (hosted on <client>.legistar.com),
dated by the event date (source-native).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import parse_qs, quote, urlsplit

from radar.net.client import PoliteClient

API = "https://webapi.legistar.com/v1"


def page_url(client: str, body_id: int) -> str:
    return f"{API}/{client}/events?body={body_id}"


def is_legistar(url: str) -> bool:
    return urlsplit(url).hostname == "webapi.legistar.com"


def events(client_http: PoliteClient, url: str, years: int) -> list[dict]:
    parts = urlsplit(url)
    client = parts.path.split("/")[2]
    body = int(parse_qs(parts.query)["body"][0])
    since = (date.today() - timedelta(days=365 * years)).isoformat()
    flt = quote(f"EventBodyId eq {body} and EventDate ge datetime'{since}'")
    r = client_http.get(f"{API}/{client}/events?$filter={flt}&$orderby=EventDate%20desc")
    if r.status != 200:
        return []
    out = []
    for e in json.loads(r.content):
        d = (e.get("EventDate") or "")[:10]
        for kind, key in (("AGENDA", "EventAgendaFile"), ("MINUTES", "EventMinutesFile")):
            if e.get(key):
                out.append({"url": e[key], "doc_type": kind, "doc_date": date.fromisoformat(d) if d else None,
                            "title": f"{e.get('EventBodyName', 'Board')} {kind.title()} {d}"})
    return out
