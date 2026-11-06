from fastapi.testclient import TestClient

from radar.web.app import create_app


def test_empty_table_renders():
    c = TestClient(create_app())
    r = c.get("/")
    assert r.status_code == 200 and "Florida allocators" in r.text
    assert c.get("/how").status_code == 200


def test_basic_auth_required_when_enabled(monkeypatch):
    import hashlib

    from radar import settings as S
    monkeypatch.setenv("RADAR_AUTH_MODE", "basic")
    monkeypatch.setenv("RADAR_AUTH_PASSWORD_HASH", hashlib.sha256(b"s3cret").hexdigest())
    S.get_settings.cache_clear()
    c = TestClient(create_app())
    assert c.get("/").status_code == 401
    assert c.get("/", auth=("owner", "wrong")).status_code == 401
    assert c.get("/", auth=("owner", "s3cret")).status_code == 200
    assert c.get("/doc/" + "0" * 64).status_code == 401
