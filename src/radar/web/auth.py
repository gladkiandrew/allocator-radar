"""Auth. "none" for localhost; "basic" behind a login on a subdomain (step 8)."""

from __future__ import annotations

import hashlib
import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from radar.settings import get_settings

_basic = HTTPBasic(auto_error=False)


def require_user(creds: HTTPBasicCredentials | None = Depends(_basic)) -> str:
    s = get_settings()
    if s.auth_mode == "none":
        return s.reviewer
    if creds is None or not s.auth_password_hash:
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    ok_user = secrets.compare_digest(creds.username, s.auth_user)
    ok_pass = secrets.compare_digest(hashlib.sha256(creds.password.encode()).hexdigest(), s.auth_password_hash)
    if not (ok_user and ok_pass):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    return creds.username


