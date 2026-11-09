"""Discovery agent: seed list -> registry/florida.yaml (Touchpoint 1 reviews it).

For each seed plan it:
  1. looks up the host's verdict in config/sources.yaml (MANUAL/DROP hosts are not fetched)
  2. GETs the plan's home page through the PoliteClient (robots-checked, logged)
  3. finds same-site pages that look like document listings (board, minutes, agendas,
     investments, reports, financials, 112.664 disclosures, RFPs) and counts document links
  4. records robots verdict, bot walls, and the site's own <title> for the reviewer

No search engine is used. Nothing found here is crawled until the registry is approved.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from radar.crawl.links import classify, is_document, parse_links
from radar.net.client import AUTOMATABLE, BotWall, HostThrottled, PoliteClient, RefusedRequest, host_verdict
from radar.settings import get_settings

log = logging.getLogger(__name__)

PAGE_HINT = re.compile(
    r"(board|meeting|minutes|agenda|investment|performance|report|financial|actuarial|valuation|"
    r"112\.664|disclosure|policy|rfp|request for proposal|documents?|publications?|archive|trustees|"
    r"quarterly|audit|acfr|plan information|pension)",
    re.I,
)
SKIP = re.compile(r"(login|log-in|signin|sign-in|register|subscribe|contact|calendar\.aspx|facebook|twitter|"
                  r"instagram|linkedin|youtube|wp-admin|wp-login|feed/?$|/tag/|/author/|\?share=|print=)", re.I)
# On city-hosted pages (one plan among hundreds of city pages), a link must name the plan's world.
CITY_HINT = re.compile(r"(pension|retire|trustee|112\.664|actuar|investment|disclosure|board meeting|minutes|agenda)", re.I)
CITY_TEMPLATES = {"T-CP", "T-CITY", "T-GOV"}
MAX_CANDIDATES = 16
STRONG = re.compile(r"(agenda|minutes|meeting|investment|performance|quarterly|financial|actuarial|valuation|"
                    r"112\.664|disclosure|rfp|request for proposal|policy|documents?|publications?|reports?|audit|acfr)", re.I)
# Person bio pages (e.g. /board-of-trustees/<name>/): never needed, never fetched.
PERSON_PAGE = re.compile(r"/(board-of-trustees|trustees|staff|team|our-team|people|bios?)/[^/]+/?$", re.I)
NOISE = re.compile(r"(privacy|concern|compliment|newsletter|quicklinks|accessib|sitemap|faq|careers|jobs|news-?flash|"
                   r"report-a-problem|lang_upd|oc_lang)", re.I)
# Pre-checked at approval only if the page itself looks like a pension document listing.
SUGGEST = re.compile(r"(pension|retire|trust|112\.?664|actuar|investment|performance|board-meetings|minutes|agenda|"
                     r"financial|audit|polic|reports|publications|meeting)", re.I)


NOT_SUGGEST = re.compile(r"(/forms?/?|retiree-information|summary-plan|/spd/?|deferred-retirement-option|/drop\b|/benefits/?$|"
                         r"member-resources|commission-meetings|/AgendaCenter/?$|retirement-center)", re.I)


def suggest(p: dict) -> bool:
    typed = sum(n for k, n in p["by_type"].items() if k != "OTHER")
    if not p["documents"] or NOT_SUGGEST.search(p["url"]):
        return False
    return p["label"] == "plan page" or bool(SUGGEST.search(f"{p['label']} {p['url']}")) or typed >= 3


def resuggest(path: Path) -> None:
    """Recompute `suggested` flags in an existing registry file without fetching anything."""
    text = path.read_text()
    header = "".join(l for l in text.splitlines(True) if l.startswith("#"))
    doc = yaml.safe_load(text)
    for a in doc["allocators"]:
        for p in a.get("document_pages", []):
            p["suggested"] = suggest(p)
    path.write_text(header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120))


def _norm_url(u: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlunsplit

    parts = urlsplit(u)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in {"lang_upd", "oc_lang", "lang"}]
    path = parts.path if parts.path.endswith("/") or "." in parts.path.rsplit("/", 1)[-1] else parts.path
    return urlunsplit((parts.scheme, parts.netloc.lower(), path, urlencode(q), ""))


def _same_site(url: str, home: str) -> bool:
    a, b = urlsplit(url).hostname or "", urlsplit(home).hostname or ""
    strip = lambda h: h[4:] if h.startswith("www.") else h  # noqa: E731
    return strip(a) == strip(b)


def _summarize_docs(links) -> dict:
    docs = [l for l in links if is_document(l.url)]
    kinds = defaultdict(int)
    for l in docs:
        kinds[classify(f"{l.text} {l.url} {l.context}")] += 1
    return {"documents": len(docs), "by_type": dict(sorted(kinds.items()))}


def discover_plan(client: PoliteClient, seed: dict, deep: bool = False) -> dict:
    """deep: render every page with the browser (JS-built document lists) and follow a second level of links."""
    out = {
        "allocator_id": seed["id"],
        "display_name": seed["name"],
        "allocator_type": seed["type"],
        "jurisdiction_city": seed["city"],
        "jurisdiction_state": "FL",
        "home_url": seed["url"],
        "template": seed["template"],
        "source_id": seed["source"],
        "access": "AUTO",
        "access_reason": None,
        "site_title": None,
        "robots": None,
        "document_pages": [],
    }
    v = host_verdict(urlsplit(seed["url"]).hostname or "")
    if v is None or v["verdict"] not in AUTOMATABLE:
        out["access"] = "MANUAL"
        out["access_reason"] = (v or {}).get("reason") or f"host verdict {(v or {}).get('verdict', 'unlisted')}"
        return out
    try:
        allowed, delay = client.robots_allows(seed["url"])
        out["robots"] = "allowed" if allowed else "disallowed"
        if not allowed:
            out["access"], out["access_reason"] = "MANUAL", "robots.txt disallows the plan page"
            return out
        rendered = seed["template"] == "T-AIRO" or deep
        if rendered:
            from radar.net.render import render

            final, html = render(client, seed["url"])
            r = type("R", (), {"url": final, "status": 200, "text": html, "content_type": "text/html"})()
        else:
            r = client.get(seed["url"])
        if r.status != 200:
            out["access"], out["access_reason"] = "MANUAL", f"home page returned HTTP {r.status}"
            return out
        title, links = parse_links(r.text, r.url)
        out["site_title"] = title[:160] or None
        home_docs = _summarize_docs(links)
        pages = [{"url": r.url, "label": "plan page", **home_docs}]
        seen = {_norm_url(r.url), _norm_url(seed["url"])}
        cands = []
        for l in links:
            u = _norm_url(l.url)
            if (u in seen or is_document(u) or SKIP.search(u) or NOISE.search(u) or PERSON_PAGE.search(u)
                    or not _same_site(u, seed["url"])):
                continue
            label = f"{l.text} {u}"
            hint = CITY_HINT if seed["template"] in CITY_TEMPLATES else PAGE_HINT
            if hint.search(label):
                seen.add(u)
                l.url = u
                cands.append(l)
        cands.sort(key=lambda l: -len(STRONG.findall(f"{l.text} {l.url}")))
        budget = MAX_CANDIDATES * (2 if deep else 1)
        # Shared city pages (several plans on one page) and CivicPlus sites: stay close to the plan page.
        i = 0
        while i < len(cands) and i < budget:
            l = cands[i]
            i += 1
            try:
                if rendered:
                    final, html = render(client, l.url)
                    rr = type("R", (), {"url": final, "status": 200, "text": html, "content_type": "text/html"})()
                else:
                    rr = client.get(l.url)
            except (RefusedRequest, HostThrottled) as e:
                pages.append({"url": l.url, "label": l.text[:80], "documents": 0, "by_type": {}, "note": str(e)[:120]})
                continue
            if rr.status != 200 or "html" not in rr.content_type:
                continue
            _, sub = parse_links(rr.text, rr.url)
            s = _summarize_docs(sub)
            if deep and not s["documents"]:  # second level: follow this page's own pension/document links
                for l2 in sub:
                    u2 = _norm_url(l2.url)
                    if (u2 in seen or is_document(u2) or SKIP.search(u2) or NOISE.search(u2) or PERSON_PAGE.search(u2)
                            or not _same_site(u2, seed["url"])):
                        continue
                    hint = CITY_HINT if seed["template"] in CITY_TEMPLATES else PAGE_HINT
                    if hint.search(f"{l2.text} {u2}"):
                        seen.add(u2)
                        l2.url = u2
                        cands.append(l2)
            if s["documents"] and _norm_url(rr.url) not in {_norm_url(p["url"]) for p in pages}:
                pages.append({"url": rr.url, "label": (l.text or l.context)[:80], **s})
        if seed.get("legistar"):  # T-LEG board on the Legistar Web API
            from radar.crawl import legistar

            lg = seed["legistar"]
            evs = legistar.events(client, legistar.page_url(lg["client"], lg["body_id"]), 5)
            kinds = defaultdict(int)
            for e in evs:
                kinds[e["doc_type"]] += 1
            pages.append({"url": legistar.page_url(lg["client"], lg["body_id"]), "label": f"Legistar: {lg['body']}",
                          "documents": len(evs), "by_type": dict(kinds)})
        for p in pages:
            p["suggested"] = suggest(p) or p["url"].startswith("https://webapi.legistar.com/")
        out["document_pages"] = [p for p in pages if p["documents"] or p["label"] == "plan page"]
        if not any(p["documents"] for p in out["document_pages"]):
            out["access_reason"] = "no document links found on the plan page or its listing pages"
    except BotWall as e:
        out["access"], out["access_reason"] = "MANUAL", f"bot wall: {e}"
    except (RefusedRequest, HostThrottled) as e:
        out["access"], out["access_reason"] = "MANUAL", str(e)[:200]
    except Exception as e:  # network errors: recorded, reviewer decides
        out["access"], out["access_reason"] = "MANUAL", f"fetch failed: {type(e).__name__}: {str(e)[:120]}"
    return out


def run(seed_path: Path | None = None, out_path: Path | None = None, only: set[str] | None = None,
        deep: bool = False) -> Path:
    s = get_settings()
    seed_path = seed_path or s.registry_dir / "seed_florida.yaml"
    out_path = out_path or s.registry_dir / "florida.yaml"
    seed = yaml.safe_load(seed_path.read_text())

    # One thread per host; plans sharing a host run sequentially so per-host delays hold.
    previous = {}
    if only and out_path.exists():  # refresh some plans, keep the rest of the existing registry as-is
        previous = {a["allocator_id"]: a for a in yaml.safe_load(out_path.read_text())["allocators"]}
    by_host: dict[str, list[dict]] = defaultdict(list)
    for p in seed["plans"]:
        if only and p["id"] not in only and p["id"] in previous:
            continue
        by_host[urlsplit(p["url"]).hostname or ""].append(p)

    def work(plans: list[dict]) -> list[dict]:
        client = PoliteClient(purpose="discovery")
        try:
            done: dict[str, dict] = {}
            res = []
            for p in plans:
                if p.get("shared_page") and p["url"] in done:  # same page as a sibling plan
                    base = done[p["url"]]
                    res.append({**base, "allocator_id": p["id"], "display_name": p["name"],
                                "allocator_type": p["type"]})
                    continue
                log.info("discovering %s", p["id"])
                r = discover_plan(client, p, deep=deep)
                done[p["url"]] = r
                res.append(r)
            return res
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=12) as ex:
        results = [r for chunk in ex.map(work, by_host.values()) for r in chunk]
    got = {r["allocator_id"] for r in results}
    results += [a for aid, a in previous.items() if aid not in got]
    order = {p["id"]: i for i, p in enumerate(seed["plans"])}
    results.sort(key=lambda r: order[r["allocator_id"]])
    doc = {
        "region": seed["region"],
        "phase": seed.get("phase"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_by": "discovery agent (radar discover)",
        "seed": str(seed_path.relative_to(s.registry_dir.parent)),
        "allocators": results,
        "manual_only_sponsors": seed.get("manual_only_sponsors", []),
    }
    header = ("# Generated by the discovery agent. Review and approve in the app (Registry).\n"
              "# Edits are allowed before approval; any edit changes the version hash and needs re-approval.\n")
    out_path.write_text(header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120))
    return out_path


def version(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
