"""Register one more listing page for an approved plan.

The registry file is the source of truth (approval rewrites allocator_pages from it), so the page is
inserted into the plan's `document_pages` list in registry/florida.yaml as text (the file's layout and
comments stay as they are) and into the live allocator_pages table. Robots and the allowlist still apply
at crawl time; this only tells the crawler where to look.
"""

from __future__ import annotations

from radar import db
from radar.settings import get_settings


def _insert_yaml(text: str, allocator_id: str, url: str, label: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.strip() == f"- allocator_id: {allocator_id}"), None)
    if start is None:
        raise KeyError(allocator_id)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("- allocator_id:")), len(lines))
    block = "".join(lines[start:end])
    if f"url: {url}\n" in block:
        return text, False
    dp = next((i for i in range(start, end) if lines[i].strip() == "document_pages:"), None)
    if dp is None:
        raise ValueError(f"{allocator_id} has no document_pages list")
    safe = label.replace("'", "''")
    entry = [f"  - url: {url}\n", f"    label: '{safe}'\n", "    documents: 0\n", "    by_type: {}\n",
             "    suggested: true\n"]
    lines[dp + 1:dp + 1] = entry
    return "".join(lines), True


def add_page(allocator_id: str, url: str, label: str) -> dict:
    s = get_settings()
    path = s.registry_dir / "florida.yaml"
    new, added = _insert_yaml(path.read_text(), allocator_id, url, label)
    from radar.registry import approve as reg
    with db.connect() as con:
        was_current = reg.is_current_approved(con)
        selected = set(con.execute("SELECT allocator_id, url FROM allocator_pages").fetchall())
    if added:
        path.write_text(new)
    selected.add((allocator_id, url))
    approved = False
    if was_current:
        # Re-approve the edited file with exactly the pages already selected plus this one, so no other plan's
        # page list changes (registry auto-approval is on, D-A32).
        _, v = reg.load("florida")
        reg.approve("auto-approve (owner policy 2026-11-06): add-page", v, selected,
                    note=f"add listing page for {allocator_id}: {url}")
        approved = True
    with db.connect() as con:
        con.execute("INSERT INTO allocator_pages VALUES (?, ?, ?) ON CONFLICT DO NOTHING", [allocator_id, url, label])
        db.audit(con, "data-session", "add_page", allocator_id, detail={"url": url, "label": label, "approved": approved})
    return {"allocator_id": allocator_id, "url": url, "registry_added": added, "reapproved": approved}
