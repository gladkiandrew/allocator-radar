"""THE WALL, publishing layer: check a static snapshot (dist/) before it is deployed.

check(dist) returns a list of violations; empty = safe to publish.
  1. contact:  no contact-shaped span in any page's visible text (the scrubber's detector), and no
               mailto:, email or social-profile URL in any link
  2. links:    no link (absolute or relative) to the review queue, the export, the tools, or the lake, and
               no link a reader cannot open (manual://, file://)
  3. forms:    no form that posts anywhere
  4. files:    only page and asset types; no database, lake, key or env file; no key material in any text file
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
                    r"CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY")
ATTR = re.compile(r'\b(href|src|action|data-href|formaction)\s*=\s*"([^"]*)"', re.I)
DROP = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
TAG = re.compile(r"<[^>]+>")


def visible_text(page: str) -> str:
    return html.unescape(TAG.sub(" ", DROP.sub(" ", page)))


def _resolve(page_rel: str, url: str) -> str | None:
    """A link as a site path ("/review"), or None for an external link."""
    parts = urlsplit(url)
    if parts.scheme or url.startswith("//"):
        return None
    if url.startswith("/"):
        return parts.path
    return "/" + posixpath.normpath(posixpath.join(posixpath.dirname(page_rel), parts.path)).lstrip("./")


def check(dist: Path) -> list[str]:
    dist = Path(dist)
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
        text = f.read_text(errors="replace")
        if SECRET.search(text):
            out.append(f"files: {rel} holds key or credential material")
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


def main(dist: str) -> int:
    violations = check(Path(dist))
    pages = sum(1 for _ in Path(dist).rglob("*.html")) if Path(dist).exists() else 0
    if violations:
        print(f"WALL FAILED on {dist} ({len(violations)} violations):", file=sys.stderr)
        for v in violations[:100]:
            print("  " + v, file=sys.stderr)
        return 1
    print(f'{{"wall_static": "ok", "dir": "{dist}", "pages": {pages}}}')
    return 0
