"""Document lake: raw public documents, content-addressed by sha256, encrypted at rest.

Decision (AGENDA §10 Q3): raw bytes are kept UNREDACTED as evidence, but encrypted
(Fernet / AES-128-CBC + HMAC). Every derived layer (page text, extractions, vectors,
UI) is scrubbed. The key lives outside the repo (~/.config/allocator-radar/lake.key
or RADAR_LAKE_KEY), is generated on first use, and is never logged.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet

from radar.settings import get_settings


def _key() -> bytes:
    s = get_settings()
    if s.lake_key:
        return s.lake_key.encode()
    path = s.lake_key_file
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(Fernet.generate_key())
    return path.read_bytes().strip()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path(digest: str) -> Path:
    return get_settings().lake_dir / "raw" / digest[:2] / digest


def put(data: bytes) -> str:
    digest = sha256(data)
    path = _path(digest)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(Fernet(_key()).encrypt(data))
        tmp.replace(path)
    return digest


def get(digest: str) -> bytes:
    data = Fernet(_key()).decrypt(_path(digest).read_bytes())
    if sha256(data) != digest:
        raise ValueError(f"lake integrity failure for {digest}")
    return data


def exists(digest: str) -> bool:
    return _path(digest).exists()
