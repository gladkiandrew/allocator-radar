"""Import research-pass results (YAML) into the seed list and the source allowlist.

VERIFIED  -> seed row (template from platform) + allowlist entry for the host (verdict CRAWL)
BLOCKED   -> seed row with template MANUAL + allowlist entry with verdict MANUAL (the gap stays visible)
NOT FOUND -> skipped
Hosts already on the allowlist keep their existing verdict (a MANUAL/DROP/HARD_REJECT host is never upgraded).
Nothing is fetched here; discovery re-verifies every page through the PoliteClient (robots, GET-only).
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from radar.net.client import host_verdict
from radar.settings import get_settings

DO_NOT_CRAWL = ("resourcecenters.com", "boarddocs.com", "primegov.com")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _kind(name: str, typ: str) -> str:
    n = name.lower()
    if "police" in n and "fire" in n:
        return "pf"
    if "police" in n or "law enforcement" in n:
        return "police"
    if "fire" in n:
        return "fire"
    if "general" in n or "employees" in n:
        return "general"
    if "management" in n:
        return "management"
    return "pf" if typ == "POLICE_FIRE" else "plan"


def _template(platform: str, url: str) -> str:
    p = (platform or "").lower()
    if "airo" in p:
        return "T-AIRO"
    if "wordpress" in p or "/wp-content/" in url:
        return "T-WP"
    if "civicplus" in p or "/documentcenter/" in url.lower() or "/agendacenter" in url.lower():
        return "T-CP"
    if "granicus" in p or "opencities" in p:
        return "T-GOV"
    if "legistar" in p:
        return "T-LEG"
    return "T-CITY"


def load_entries(path: Path) -> list[dict]:
    text = path.read_text()
    m = re.search(r"```yaml\s*(.*?)```", text, re.S)
    data = yaml.safe_load(m.group(1) if m else text)
    return [e for e in (data or []) if isinstance(e, dict)]


def import_entries(entries: list[dict], batch: str) -> dict:
    s = get_settings()
    seed_path = s.registry_dir / "seed_florida.yaml"
    src_path = s.config_dir / "sources.yaml"
    seed_text, src_text = seed_path.read_text(), src_path.read_text()
    existing_ids = set(re.findall(r"id: (fl-[a-z0-9-]+)", seed_text))
    existing_urls = set(re.findall(r'url: "([^"]+)"', seed_text))
    rows, sources, out = [], [], {"added": [], "manual": [], "skipped": []}
    new_hosts: dict[str, dict] = {}
    for e in entries:
        status = str(e.get("status", "")).upper()
        url = str(e.get("url") or "").strip()
        name = str(e.get("name") or "").strip()
        if status.startswith("NOT") or not url.startswith("http") or not name:
            out["skipped"].append(f"{name or '?'}: {status or 'no url'}")
            continue
        host = (urlsplit(url).hostname or "").lower()
        if any(h in host for h in DO_NOT_CRAWL):
            out["skipped"].append(f"{name}: do-not-crawl host {host}")
            continue
        if url in existing_urls and status.startswith("VERIFIED"):
            out["skipped"].append(f"{name}: URL already seeded")
            continue
        city = str(e.get("city") or e.get("county") or "fl")
        aid = f"fl-{_slug(city)}-{_kind(name, str(e.get('type')))}"
        base, n = aid, 2
        while aid in existing_ids:
            aid = f"{base}-{n}"
            n += 1
        existing_ids.add(aid)
        typ = e.get("type") if e.get("type") in ("POLICE_FIRE", "PUBLIC_PENSION_LOCAL") else "PUBLIC_PENSION_LOCAL"
        blocked = status.startswith("BLOCKED")
        v = host_verdict(host)
        if v is None and host not in new_hosts:
            new_hosts[host] = {"verdict": "MANUAL" if blocked else "CRAWL", "name": f"{city} ({name})"[:80],
                               "reason": (str(e.get("note") or "")[:120] or None)}
        source_id = (v or {}).get("id") or f"PB-{_slug(host)[:24]}"
        template = "MANUAL" if blocked or (v and v["verdict"] not in ("CRAWL", "API", "BULK")) else _template(
            str(e.get("platform") or ""), url)
        rows.append({"id": aid, "name": name.replace('"', "'"), "type": typ, "city": city.replace('"', "'"), "url": url,
                     "template": template, "source": source_id})
        (out["manual"] if template == "MANUAL" else out["added"]).append(aid)
    if rows:
        lines = [f"  # Phase B import {batch} (research pass; re-verified by discovery)"]
        for r in rows:
            lines.append(f'  - {{id: {r["id"]}, name: "{r["name"]}", type: {r["type"]}, city: "{r["city"]}", '
                         f'url: "{r["url"]}", template: {r["template"]}, source: {r["source"]}}}')
        seed_text = seed_text.replace("  - {id: fl-sba-frs,", "\n".join(lines) + "\n  - {id: fl-sba-frs,", 1)
        seed_path.write_text(seed_text)
    if new_hosts:
        lines = [f"  # ---- Phase B import {batch} ----"]
        for h, v in new_hosts.items():
            sid = f"PB-{_slug(h)[:24]}"
            reason = f', reason: "{v["reason"]}"' if v["reason"] and v["verdict"] == "MANUAL" else ""
            lines.append(f'  - {{id: {sid}, name: "{v["name"].replace(chr(34), "")}", hosts: [{h}], verdict: {v["verdict"]}{reason}}}')
        src_text = src_text.replace("  # ---- Blocked to non-browser clients (§2.4) -> MANUAL ----",
                                    "\n".join(lines) + "\n\n  # ---- Blocked to non-browser clients (§2.4) -> MANUAL ----", 1)
        src_path.write_text(src_text)
    out["new_hosts"] = len(new_hosts)
    return out
