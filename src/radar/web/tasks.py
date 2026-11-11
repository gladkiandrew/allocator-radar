"""Manual fetch tasks (local admin only): data/manual_tasks.yaml, maintained by hand or by another session.

This module only reads the file, saves an uploaded document into the manual inbox, and flips ONE task's
status line from its current value to "received". It never rewrites the YAML from a parsed copy: the edit
is a single-line textual change, re-parsed and checked before it is written (temp file + rename), so the
comments and formatting of whoever maintains the file survive. When the edit cannot be made safely it
refuses with an error instead.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from radar.settings import get_settings

STATUSES = ("open", "received", "ingested", "dropped")
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


class TaskError(Exception):
    pass


def tasks_path() -> Path:
    return get_settings().data_dir / "manual_tasks.yaml"


def safe_name(s: Any) -> bool:
    return isinstance(s, str) and bool(SAFE_NAME.fullmatch(s)) and ".." not in s


def _parse(text: str) -> list[dict]:
    data = yaml.safe_load(text) if text.strip() else None
    if data is None:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("tasks", []), list):
        raise TaskError("manual_tasks.yaml must be a mapping with a 'tasks' list")
    return [t for t in (data.get("tasks") or []) if isinstance(t, dict)]


def load(path: Path | None = None) -> list[dict]:
    """All tasks, as written. A missing file is an empty list."""
    path = path or tasks_path()
    if not path.exists():
        return []
    return _parse(path.read_text())


def sorted_tasks(tasks: list[dict]) -> list[dict]:
    """Open first, then received, ingested, dropped (unknown statuses last); then by priority."""
    def key(t):
        st = str(t.get("status") or "open")
        pr = t.get("priority")
        return (STATUSES.index(st) if st in STATUSES else len(STATUSES),
                pr if isinstance(pr, (int, float)) else float("inf"), str(t.get("id")))
    return sorted(tasks, key=key)


def find(task_id: str, path: Path | None = None) -> dict:
    matches = [t for t in load(path) if str(t.get("id")) == task_id]
    if len(matches) != 1:
        raise TaskError("unknown task" if not matches else f"task id {task_id!r} appears more than once")
    return matches[0]


# ---------------- the one-line status edit ----------------
_KEY = re.compile(r"^(?P<pre> *(?:- +)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*):(?P<rest>.*)$")
_SCALAR = re.compile(r"^(?P<sp> *)(?P<val>'[^']*'|\"[^\"]*\"|[^\s#'\"][^#]*?)(?P<tail> *(?:#.*)?)$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _unquote(v: str) -> str:
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"" else v


def set_status_text(text: str, task_id: str, new_status: str) -> str:
    """Return `text` with only the status line of task `task_id` changed. Raises TaskError if unsure."""
    if new_status not in STATUSES:
        raise TaskError(f"unknown status {new_status!r}")
    if "\t" in text:
        raise TaskError("manual_tasks.yaml uses tabs; not editing it automatically")
    lines = text.splitlines(keepends=True)
    # 1. the line that carries this task's id
    id_lines = []
    for i, ln in enumerate(lines):
        m = _KEY.match(ln.rstrip("\r\n"))
        if m and m.group("key") == "id":
            sm = _SCALAR.match(m.group("rest"))
            if sm and _unquote(sm.group("val").strip()) == task_id:
                id_lines.append((i, len(m.group("pre"))))
    if len(id_lines) != 1:
        raise TaskError("could not find exactly one id line for this task in manual_tasks.yaml")
    i_id, col = id_lines[0]
    # 2. the item block: back to its "- " line, forward until a line indented less than its keys
    start = i_id
    while True:
        ln = lines[start].rstrip("\r\n")
        m = _KEY.match(ln)
        if m and "-" in m.group("pre"):
            break
        start -= 1
        if start < 0:
            raise TaskError("could not find the start of this task's entry")
        ln = lines[start].rstrip("\r\n")
        if not ln.strip() or ln.lstrip().startswith("#") or _indent(ln) > col:
            continue
        m = _KEY.match(ln)
        if not m or len(m.group("pre")) != col:
            raise TaskError("unexpected layout around this task's entry")
    end = i_id + 1
    while end < len(lines):
        ln = lines[end].rstrip("\r\n")
        if ln.strip() and not ln.lstrip().startswith("#") and _indent(ln) < col:
            break
        end += 1
    # 3. exactly one "status:" key at the item's key column
    hits = []
    for j in range(start, end):
        m = _KEY.match(lines[j].rstrip("\r\n"))
        if m and m.group("key") == "status" and len(m.group("pre")) == col:
            hits.append((j, m))
    if len(hits) != 1:
        raise TaskError("could not find exactly one status line for this task")
    j, m = hits[0]
    sm = _SCALAR.match(m.group("rest"))
    if not sm:
        raise TaskError("the status line is not a plain value")
    old = sm.group("val")
    q = old[0] if old[0] in "'\"" else ""
    eol = lines[j][len(lines[j].rstrip("\r\n")):]
    lines[j] = f"{m.group('pre')}status:{sm.group('sp')}{q}{new_status}{q}{sm.group('tail')}{eol}"
    new_text = "".join(lines)
    # 4. verify: the parsed file is identical except for this one task's status
    before, after = _parse(text), _parse(new_text)
    expect = [dict(t, status=new_status) if str(t.get("id")) == task_id else t for t in before]
    if after != expect:
        raise TaskError("the edit would change more than this task's status; not writing it")
    return new_text


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def mark(task_id: str, new_status: str = "received", path: Path | None = None) -> None:
    path = path or tasks_path()
    if not path.exists():
        raise TaskError("manual_tasks.yaml does not exist")
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    new_text = set_status_text(text, task_id, new_status)
    if path.read_bytes() != raw:  # the other session wrote meanwhile: do not clobber it
        raise TaskError("manual_tasks.yaml changed while it was being edited; try again")
    _atomic_write(path, new_text.encode("utf-8"))


# ---------------- upload ----------------
def destination(task: dict) -> Path:
    aid, fname = task.get("allocator_id"), task.get("file_name")
    if not safe_name(aid):
        raise TaskError("the task's allocator_id is not a plain name")
    if not safe_name(fname):
        raise TaskError("the task's file_name is not a plain name")
    inbox = get_settings().manual_inbox.resolve()
    dest = (inbox / aid / fname).resolve()
    if not dest.is_relative_to(inbox) or dest.parent.parent != inbox:
        raise TaskError("the destination would leave the manual inbox")
    return dest


def receive(task_id: str, data: bytes) -> Path:
    """Save the upload for one open task under manual_inbox/<allocator_id>/<file_name>, then mark it received."""
    task = find(task_id)
    if str(task.get("status") or "open") != "open":
        raise TaskError(f"task is {task.get('status')}, not open")
    if not data:
        raise TaskError("the uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        raise TaskError("the uploaded file is too large")
    dest = destination(task)
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink() or not dest.is_file() or dest.read_bytes() != data:
            raise TaskError(f"{dest.parent.name}/{dest.name} already exists with different content; not overwriting")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(dest, data)
    try:
        mark(task_id, "received")
    except TaskError as e:
        raise TaskError(f"file saved to {dest.parent.name}/{dest.name}, but the task status was not updated: {e}") from e
    return dest
