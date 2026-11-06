from fastapi.testclient import TestClient

from radar.web.app import create_app


def test_empty_table_renders():
    c = TestClient(create_app())
    r = c.get("/")
    assert r.status_code == 200 and "Florida allocators" in r.text
    assert c.get("/how").status_code == 200
