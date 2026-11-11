"""Auth. "none" for localhost; "basic" behind a login on a subdomain (step 8).

When RADAR_GATE=on, the session-cookie gate in radar.web.gate does the authentication instead."""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from radar.settings import get_settings
from radar.web.gate import verify_password

_basic = HTTPBasic(auto_error=False)


def require_user(creds: HTTPBasicCredentials | None = Depends(_basic)) -> str:
    s = get_settings()
    if s.auth_mode == "none" or s.gate_on:  # the gate (radar.web.gate) replaces basic auth when it is on
        return s.reviewer
    if creds is None or not s.auth_password_hash:
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    ok_user = secrets.compare_digest(creds.username, s.auth_user)
    ok_pass = verify_password(creds.password, s.auth_password_hash)  # pbkdf2 string or legacy sha256 hex
    if not (ok_user and ok_pass):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    return creds.username


