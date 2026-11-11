"""THE WALL, publishing layer: check a static snapshot (dist/) before it is deployed.

check(dist) returns a list of violations; empty = safe to publish.
  1. contact:  no contact-shaped span in any page's visible text (the scrubber's detector), and no
               mailto:, email or social-profile URL in any link
  2. links:    no link (absolute or relative) to the review queue, the export, the tools, or the lake, and
               no link a reader cannot open (manual://, file://)
  3. forms:    no form that posts anywhere
  4. files:    only page and asset types; no database, lake, key or env file; no key material in any text file
  5. audience: (access gate) the build is for one audience, set by `--audience`:
               public (default): no block marked for members or admins (data-audience="member"/"admin", or
                 class="contacts"), and every page says <meta name="radar-audience" content="public">
               member: contact details are allowed only inside blocks marked data-audience="member" or
                 class="contacts"; everything outside them is checked as above; no data-audience="admin" block;
                 still no admin page, review, tools or export link
"""

from __future__ import annotations

import html
import posixpath
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from radar.wall.scrubber import detect

HIDDEN = ("/review", "/export", "/manual", "/records", "/registry", "/inbox", "/tasks", "/doc/")
ALLOWED_SUFFIXES = {".html", ".css", ".js", ".woff2", ".md", ".txt", ".svg", ".png", ".ico"}
SECRET = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|RADAR_AUTH_PASSWORD_HASH|"
                    r"RADAR_(?:MEMBER|ADMIN)_PASSWORD_HASH|RADAR_SESSION_SECRET|GATE_SESSION_SECRET|"
                    r"GATE_MEMBER_PASSWORD_HASH|pbkdf2_sha256\$|CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY")
ATTR = re.compile(r'\b(href|src|action|data-href|formaction)\s*=\s*"([^"]*)"', re.I)
DROP = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
TAG = re.compile(r"<[^>]+>")


# Table cells, rows and list items are separate pieces of text: a number ending one cell and a firm name starting the
# next ("$450,282" | "Park Place International LLC") must not read as a street address. Text inside one cell is still
# checked whole, so nothing is loosened.
CELL = re.compile(r"</?(?:td|th|tr|li)\b[^>]*>", re.I)


def visible_text(page: str) -> str:
    return html.unescape(TAG.sub(" ", CELL.sub(" | ", DROP.sub(" ", page))))


AUDIENCES = ("public", "member")
# An opening tag marked for an audience: data-audience="member" / "admin", or a "contacts" class.
MARKED = re.compile(r"""<([a-zA-Z][a-zA-Z0-9]*)\b(?=[^>]*?(?:\bdata-audience\s*=\s*["']?(member|admin)\b|"""
                    r"""\bclass\s*=\s*["'][^"']*\bcontacts\b))[^>]*>""", re.I)
AUDIENCE_META = re.compile(r'<meta\s+name="radar-audience"\s+content="([a-z]+)"', re.I)


def strip_marked(page: str, *, allow: tuple[str, ...] = ("member",)) -> tuple[str, list[str]]:
    """Remove the blocks marked for an allowed audience (with everything nested inside them).

    Returns (the page without them, the audiences of marked blocks that are not allowed). A "contacts" class with no
    data-audience counts as a member block."""
    out, bad, pos = [], [], 0
    while True:
        m = MARKED.search(page, pos)
        if not m:
            out.append(page[pos:])
            return "".join(out), bad
        tag, aud = m.group(1).lower(), (m.group(2) or "member").lower()
        if aud not in allow:
            bad.append(aud)
            out.append(page[pos:m.end()])
            pos = m.end()
            continue
        out.append(page[pos:m.start()])
        if m.group(0).endswith("/>"):
            pos = m.end()
            continue
        depth, i = 1, m.end()  # find the matching close tag of the same name
        tok = re.compile(rf"<(/?){tag}\b[^>]*?(/?)>", re.I)
        while depth:
            t = tok.search(page, i)
            if not t:
                i = len(page)
                break
            if t.group(1):
                depth -= 1
            elif not t.group(2):
                depth += 1
            i = t.end()
        pos = i


def _resolve(page_rel: str, url: str) -> str | None:
    """A link as a site path ("/review"), or None for an external link."""
    parts = urlsplit(url)
    if parts.scheme or url.startswith("//"):
        return None
    if url.startswith("/"):
        return parts.path
    return "/" + posixpath.normpath(posixpath.join(posixpath.dirname(page_rel), parts.path)).lstrip("./")


def check(dist: Path, audience: str = "public") -> list[str]:
    dist = Path(dist)
    if audience not in AUDIENCES:
        return [f"audience: unknown build audience {audience!r} (public or member)"]
    if not (dist / "index.html").is_file():
        return [f"files: {dist}/index.html missing (run export-static first)"]
    out: list[str] = []
    for f in sorted(p for p in dist.rglob("*") if p.is_file()):
        rel = f.relative_to(dist).as_posix()
        if f.suffix.lower() not in ALLOWED_SUFFIXES or f.name.startswith(".") or "lance" in rel or "lake" in rel:
            out.append(f"files: {rel} is not a page or asset type the public site may hold")
            continue
        if f.suffix.lower() == ".woff2" or f.suffix.lower() in (".png", ".ico"):
            continue
        bare_rel = "/" + re.sub(r"\.html$", "", rel)
        if bare_rel.startswith(HIDDEN):
            out.append(f"files: {rel} is an admin page")
        text = f.read_text(errors="replace")
        if SECRET.search(text):
            out.append(f"files: {rel} holds key or credential material")
        if f.suffix.lower() == ".html":
            # Audience: what may appear, and where. In a member build the contact checks below run on the page with
            # the member blocks removed; in a public build no marked block may exist at all.
            stripped, bad = strip_marked(text, allow=("member",) if audience == "member" else ())
            for aud in sorted(set(bad)):
                out.append(f"audience: {rel} has a block marked for {aud} in a {audience} build")
            meta = AUDIENCE_META.search(text)
            if meta and meta.group(1) != audience:
                out.append(f"audience: {rel} was rendered for {meta.group(1)}, not {audience}")
            text = stripped
        if "mailto:" in text.lower():
            out.append(f"contact: {rel} holds a mailto: link")
        if f.suffix.lower() != ".html":
            continue
        hits = detect(visible_text(text))
        out += [f"contact: {rel} shows {h.kind}: {h.text[:40]!r}" for h in hits[:3]]
        for attr, raw in ATTR.findall(text):
            url = html.unescape(raw).strip()
            for h in detect(url):
                if h.kind in ("email", "mailto", "social_url"):
                    out.append(f"contact: {rel} links to {h.kind}: {url[:60]!r}")
            scheme = urlsplit(url).scheme.lower()
            if scheme and scheme not in ("http", "https", "data", "mailto"):  # mailto is reported above
                out.append(f"links: {rel} {attr} uses a non-public scheme: {url[:60]!r}")
            path = _resolve(rel, url)
            if path is not None:
                bare = re.sub(r"\.html$", "", path)
                if bare.startswith(HIDDEN) or bare in ("/review", "/export"):
                    out.append(f"links: {rel} {attr} points to {path}")
        if re.search(r'<form\b[^>]*method\s*=\s*"?post', text, re.I):
            out.append(f"forms: {rel} has a form that posts")
    return out


def main(dist: str, audience: str = "public") -> int:
    violations = check(Path(dist), audience)
    pages = sum(1 for _ in Path(dist).rglob("*.html")) if Path(dist).exists() else 0
    if violations:
        print(f"WALL FAILED on {dist} ({len(violations)} violations):", file=sys.stderr)
        for v in violations[:100]:
            print("  " + v, file=sys.stderr)
        return 1
    print(f'{{"wall_static": "ok", "dir": "{dist}", "audience": "{audience}", "pages": {pages}}}')
    return 0
