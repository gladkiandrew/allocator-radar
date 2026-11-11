"""HTML link extraction and document-link classification (shared by discovery + crawler)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from urllib.parse import unquote, urldefrag, urljoin, urlsplit


@dataclass
class Link:
    url: str
    text: str
    context: str  # nearest preceding heading text


class _Parser(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.links: list[Link] = []
        self._href: str | None = None
        self._buf: list[str] = []
        self._heading: str = ""
        self._in_h: bool = False
        self._hbuf: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "base" and a.get("href"):
            self.base = urljoin(self.base, a["href"])
        if tag == "a" and a.get("href"):
            self._href = a["href"].strip()
            self._buf = [a.get("title") or a.get("aria-label") or ""]
        if tag in ("h1", "h2", "h3", "h4", "h5", "strong", "summary", "button"):
            self._in_h, self._hbuf = True, []
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            href = self._href
            self._href = None
            if href.startswith(("mailto:", "tel:", "javascript:", "#", "sms:")):
                return  # never followed, never stored
            url = urldefrag(urljoin(self.base, href))[0]
            text = re.sub(r"\s+", " ", " ".join(self._buf)).strip()
            self.links.append(Link(url, text, self._heading))
        if tag in ("h1", "h2", "h3", "h4", "h5", "strong", "summary", "button") and self._in_h:
            t = re.sub(r"\s+", " ", "".join(self._hbuf)).strip()
            if 2 < len(t) < 120:
                self._heading = t
            self._in_h = False
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)
        if self._in_h:
            self._hbuf.append(data)
        if self._in_title:
            self.title += data


def parse_links(html: str, base: str) -> tuple[str, list[Link]]:
    p = _Parser(base)
    try:
        p.feed(html)
    except Exception:
        pass
    return re.sub(r"\s+", " ", p.title).strip(), p.links


DOC_EXT = re.compile(r"\.(pdf|docx?|xlsx?)(?:$|\?)", re.I)
CP_DOC = re.compile(r"/(DocumentCenter/View|AgendaCenter/ViewFile|Archive\.aspx\?ADID=|ArchiveCenter/ViewFile)", re.I)


def is_document(url: str) -> bool:
    return bool(DOC_EXT.search(urlsplit(url).path) or CP_DOC.search(url))


_TYPE_RULES = [
    ("RFP", r"\b(rfp|rfq|rfi|request for (proposals?|qualifications?|information)|solicitations?|search)\b"),
    ("IPS", r"\b(investment policy|ips)\b"),
    ("MINUTES", r"\bminutes?\b"),
    ("AGENDA", r"\b(agenda|packet|board meeting|meeting materials)\b"),
    ("PERFORMANCE", r"\b(performance|quarterly (review|report)|investment (review|report)|flash|executive summary|portfolio review)\b"),
    ("ACTUARIAL", r"\b(actuarial|valuation|experience study|impact statement)\b"),
    ("FINANCIAL", r"\b(financial statements?|audit|acfr|cafr|annual report|annual financial)\b"),
    ("DISCLOSURE_112664", r"(112\.664|state required|disclosure|fact sheet)"),
]


def classify(text: str) -> str:
    t = unquote(text).lower().replace("_", " ").replace("-", " ")
    for kind, pat in _TYPE_RULES:
        if re.search(pat, t):
            return kind
    return "OTHER"


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        if y < 100:
            y += 2000
        if not 2000 <= y <= 2100:
            return None
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(text: str) -> date | None:
    """Best-effort source-native date from a link title or filename. None if unsure."""
    t = unquote(text).replace("_", " ")
    # 2026-03-12 / 2026.03.12 / 20260312
    if m := re.search(r"(20\d{2})[-./ ]?(0[1-9]|1[0-2])[-./ ]?(0[1-9]|[12]\d|3[01])(?!\d)", t):
        return _mk(int(m[1]), int(m[2]), int(m[3]))
    # 03-12-2026 / 3.12.26 / 03122026
    if m := re.search(r"(?<!\d)(0?[1-9]|1[0-2])[-./ ](0?[1-9]|[12]\d|3[01])[-./ ](20\d{2}|\d{2})(?!\d)", t):
        return _mk(int(m[3]), int(m[1]), int(m[2]))
    if m := re.search(r"(?<!\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(20\d{2})(?!\d)", t):
        return _mk(int(m[3]), int(m[1]), int(m[2]))
    # March 12, 2026 / Mar 12 2026 / 12 March 2026
    # also hyphenated file names: February-13-2024-Minutes.pdf (D-B26)
    if m := re.search(r"\b([A-Za-z]{3,9})\.?[\s-]+(\d{1,2})(?:st|nd|rd|th)?,?[\s-]+(20\d{2})\b", t):
        mo = _MONTHS.get(m[1][:3].lower())
        if mo:
            return _mk(int(m[3]), mo, int(m[2]))
    if m := re.search(r"\b(\d{1,2})[\s-]+([A-Za-z]{3,9})\.?,?[\s-]+(20\d{2})\b", t):
        mo = _MONTHS.get(m[2][:3].lower())
        if mo:
            return _mk(int(m[3]), mo, int(m[1]))
    # Quarter end: Q1 2026, 1Q26, "March 2026", "3/31/2026" handled above
    if m := re.search(r"\b(?:Q([1-4])|([1-4])Q)[\s-]?(20\d{2}|\d{2})\b", t, re.I):
        q = int(m[1] or m[2])
        y = int(m[3])
        return _mk(y, q * 3, 30 if q in (2, 3) else 31)
    if m := re.search(r"\b([A-Za-z]{3,9})\.?\s+(20\d{2})\b", t):
        mo = _MONTHS.get(m[1][:3].lower())
        if mo:
            return _mk(int(m[2]), mo, 1)
    return None
