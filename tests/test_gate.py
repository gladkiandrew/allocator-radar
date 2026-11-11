"""Access gate: audiences (public / member / admin), the local login, the static builds, the gated Worker."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jinja2 import ChoiceLoader, DictLoader

from radar import settings as S
from radar import static_export
from radar.wall import static_site
from radar.web import gate
from radar.web.app import create_app, templates

SECRET = "test-session-secret-0123456789"


def _env(monkeypatch, **kv):
    for k, v in kv.items():
        monkeypatch.setenv(k, v)
    S.get_settings.cache_clear()


@pytest.fixture
def gated(monkeypatch):
    _env(monkeypatch, RADAR_GATE="on", RADAR_SESSION_SECRET=SECRET,
         RADAR_MEMBER_PASSWORD_HASH=gate.hash_password("member-pass", iterations=1000),
         RADAR_ADMIN_PASSWORD_HASH=gate.hash_password("admin-pass", iterations=1000))
    return TestClient(create_app())


def _login(c, pw):
    return c.post("/login", data={"password": pw, "next": "/"}, follow_redirects=False)


# ------------------------------------------------------------ passwords and cookies

def test_password_hash_roundtrip_and_legacy():
    import hashlib

    h = gate.hash_password("s3cret")
    assert h.startswith("pbkdf2_sha256$100000$") and h != gate.hash_password("s3cret")  # salted
    assert gate.verify_password("s3cret", h) and not gate.verify_password("s3creT", h)
    assert gate.verify_password("s3cret", hashlib.sha256(b"s3cret").hexdigest())  # legacy RADAR_AUTH_PASSWORD_HASH
    assert not gate.verify_password("s3cret", None) and not gate.verify_password("s3cret", "pbkdf2_sha256$x")


def test_session_cookie_signed_and_expiring():
    c = gate.sign_session("member", SECRET, 60)
    assert gate.verify_session(c, SECRET) == "member"
    assert gate.verify_session(c.replace("member", "admin", 1), SECRET) is None  # role cannot be edited
    assert gate.verify_session(c, "another-secret-0123456789") is None
    assert gate.verify_session(gate.sign_session("member", SECRET, 60, now=1000), SECRET) is None  # expired
    assert gate.verify_session(gate.sign_session("public", SECRET, 60), SECRET) is None
    assert gate.verify_session("garbage", SECRET) is None


def test_backoff_locks_after_repeated_failures():
    b = gate.Backoff()
    for _ in range(4):
        b.fail("ip", now=100)
    assert b.wait_s("ip", now=100) == 0
    b.fail("ip", now=100)
    assert b.wait_s("ip", now=100) > 0
    b.ok("ip")
    assert b.wait_s("ip", now=100) == 0


def test_hash_password_cli(capsys, monkeypatch):
    import io

    from radar import cli

    monkeypatch.setattr("sys.stdin", io.StringIO("pw-from-stdin\n"))
    assert cli.main(["hash-password"]) == 0
    out = capsys.readouterr().out.strip()
    assert gate.verify_password("pw-from-stdin", out)


# ------------------------------------------------------------ the audience contract

def _with_probe(app):
    """A tiny template that prints the audience variables, served through routes.templates_partial."""
    from fastapi import Request
    from fastapi.responses import HTMLResponse

    from radar.web.routes import templates_partial

    def probe(request: Request):
        return templates_partial(request, "_probe.html")

    app.add_api_route("/probe", probe, response_class=HTMLResponse)
    return app


@pytest.fixture
def probe_template():
    old = templates.env.loader
    templates.env.loader = ChoiceLoader([DictLoader({"_probe.html": "{{ audience }}|{{ can_see_contacts }}|{{ can_admin }}"}), old])
    templates.env.cache.clear() if templates.env.cache else None
    yield
    templates.env.loader = old


def test_audience_local_app_is_admin(probe_template):
    c = TestClient(_with_probe(create_app()))
    assert c.get("/probe").text == "admin|True|True"
    assert '<meta name="radar-audience" content="admin">' in c.get("/").text  # render() too


def test_audience_public_mode_is_public(monkeypatch, probe_template):
    _env(monkeypatch, RADAR_PUBLIC_MODE="true")
    c = TestClient(_with_probe(create_app()))
    assert c.get("/probe").text == "public|False|False"
    home = c.get("/").text
    assert '<meta name="radar-audience" content="public">' in home and 'href="/export.csv"' not in home


def test_audience_gate_is_the_signed_in_role(gated, probe_template):
    c = TestClient(_with_probe(gated.app))
    _login(c, "member-pass")
    assert c.get("/probe").text == "member|True|False"
    home = c.get("/").text
    assert '<meta name="radar-audience" content="member">' in home
    assert 'href="/review"' not in home and 'href="/export.csv"' not in home and 'href="/logout"' in home
    c.cookies.clear()
    _login(c, "admin-pass")
    assert c.get("/probe").text == "admin|True|True"
    assert 'href="/review"' in c.get("/").text


# ------------------------------------------------------------ (c) the local gate

def test_gate_refuses_to_start_without_secret(monkeypatch):
    _env(monkeypatch, RADAR_GATE="on", RADAR_MEMBER_PASSWORD_HASH=gate.hash_password("x", iterations=1000))
    with pytest.raises(gate.GateConfigError, match="RADAR_SESSION_SECRET"):
        create_app()
    _env(monkeypatch, RADAR_SESSION_SECRET=SECRET, RADAR_MEMBER_PASSWORD_HASH="")
    with pytest.raises(gate.GateConfigError, match="PASSWORD_HASH"):
        create_app()


def test_gate_off_by_default():
    assert S.get_settings().gate == "off"
    c = TestClient(create_app())
    assert c.get("/", follow_redirects=False).status_code == 200
    assert c.get("/login").status_code == 404  # no login route unless the gate is on


def test_gate_redirects_anonymous(gated):
    c = gated
    for path in ("/", "/how", "/a/canary", "/ef", "/review", "/export.csv", "/doc/" + "0" * 64):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login?next="), path
    assert c.post("/review/x", data={"decision": "approved"}, follow_redirects=False).status_code == 401
    assert c.post("/registry/approve", data={"version": "x"}, follow_redirects=False).status_code == 401
    assert c.get("/login").status_code == 200 and 'type="password"' in c.get("/login").text
    assert "Allocator" not in c.get("/login").text.split("<main")[0].replace("Allocator Radar · Sign in", "")
    assert c.get("/static/style.css").status_code == 200 and c.get("/healthz").status_code == 200


def test_gate_login_cookie_and_logout(gated):
    c = gated
    bad = _login(c, "wrong")
    assert bad.status_code == 401 and "set-cookie" not in bad.headers
    ok = _login(c, "member-pass")
    assert ok.status_code == 303 and ok.headers["location"] == "/"
    sc = ok.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc and gate.COOKIE in sc
    assert c.get("/", follow_redirects=False).status_code == 200
    r = c.get("/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    c.cookies.clear()
    assert c.get("/", follow_redirects=False).status_code == 303
    c.cookies.set(gate.COOKIE, gate.sign_session("admin", "forged-secret-0123456789", 60))
    assert c.get("/", follow_redirects=False).status_code == 303  # forged cookie


def test_gate_open_redirect_blocked(gated):
    r = gated.post("/login", data={"password": "member-pass", "next": "//evil.example/x"}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_gate_member_gets_403_on_admin_paths(gated):
    c = gated
    _login(c, "member-pass")
    for path in ("/review", "/review/next", "/tasks", "/export.csv", "/manual", "/records", "/registry"):
        assert c.get(path, follow_redirects=False).status_code == 403, path
    for path, data in (("/review/x", {"decision": "approved"}), ("/registry/approve", {"version": "x"}),
                       ("/manual", {}), ("/records", {}), ("/inbox/ingest", {}), ("/tasks/x/upload", {})):
        assert c.post(path, data=data, follow_redirects=False).status_code == 403, path
    for path in ("/", "/how", "/about", "/gaps", "/ef"):
        assert c.get(path).status_code == 200, path


def test_gate_admin_sees_admin_pages(gated):
    c = gated
    _login(c, "admin-pass")
    for path in ("/review", "/registry", "/records", "/manual", "/tasks", "/export.csv"):
        assert c.get(path, follow_redirects=False).status_code == 200, path


def test_gate_backoff_after_failures(gated):
    c = gated
    codes = [_login(c, "wrong").status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert _login(c, "member-pass").status_code == 429  # even the right password waits


def test_basic_auth_accepts_pbkdf2(monkeypatch):
    _env(monkeypatch, RADAR_AUTH_MODE="basic", RADAR_AUTH_PASSWORD_HASH=gate.hash_password("s3cret", iterations=1000))
    c = TestClient(create_app())
    assert c.get("/").status_code == 401
    assert c.get("/", auth=("owner", "s3cret")).status_code == 200


# ------------------------------------------------------------ (a) (b) the static builds

def _store():
    from tests.test_static_export import _store_with_signals

    return _store_with_signals()


@pytest.fixture
def builds(tmp_path):
    _store()
    pub, mem = tmp_path / "dist", tmp_path / "dist-member"
    static_export.export(pub)
    static_export.export(mem, audience="member")
    return pub, mem


def test_static_builds_pass_their_own_wall(builds):
    pub, mem = builds
    assert static_site.check(pub) == []
    assert static_site.check(mem, "member") == []
    assert '<meta name="radar-audience" content="member">' in (mem / "index.html").read_text()
    assert '<meta name="radar-audience" content="public">' in (pub / "index.html").read_text()
    assert any("rendered for member" in v for v in static_site.check(mem))  # a member build never passes as public
    assert os.environ.get("RADAR_PUBLIC_AUDIENCE") is None  # export restored the env


def test_static_export_refuses_admin_audience(tmp_path):
    with pytest.raises(static_export.ExportError):
        static_export.export(tmp_path / "x", audience="admin")


CONTACT = '<section data-audience="member"><h3>Contacts</h3><table><tr><td>jane.doe@example.org</td></tr></table></section>'


def _inject(dist: Path, snippet: str, page: str = "a/canary.html"):
    f = dist / page
    f.write_text(f.read_text().replace("</main>", snippet + "</main>"))


def test_public_build_rejects_any_member_or_admin_block(builds):
    pub, _ = builds
    _inject(pub, '<section data-audience="member"><p>Jane Doe, CIO</p></section>')
    assert any("block marked for member" in v for v in static_site.check(pub))
    shutil.rmtree(pub)
    static_export.export(pub)
    _inject(pub, '<div class="contacts">Jane Doe</div>', "index.html")
    assert any("block marked for member" in v for v in static_site.check(pub))
    static_export.export(pub)
    _inject(pub, "<div data-audience='admin'>x</div>")
    assert any("block marked for admin" in v for v in static_site.check(pub))


def test_public_build_has_no_export_link(builds):
    pub, _ = builds
    for f in pub.rglob("*.html"):
        assert "export.csv" not in f.read_text(), f
    _inject(pub, '<a href="../export.csv">CSV</a>')
    assert any("export" in v for v in static_site.check(pub))


def test_member_build_allows_contacts_only_inside_member_blocks(builds):
    _, mem = builds
    _inject(mem, CONTACT)
    assert static_site.check(mem, "member") == []
    assert static_site.check(mem)  # the same page is a violation in a public build
    _inject(mem, "<p>Write to jane.doe@example.org</p>")  # outside a member block
    assert any(v.startswith("contact:") for v in static_site.check(mem, "member"))


def test_member_build_has_no_admin_pages_or_links(builds):
    _, mem = builds
    for f in mem.rglob("*.html"):
        text = f.read_text()
        for hidden in ("review", "export.csv", "manual.html", "records", "registry", "tasks", "/doc/"):
            assert f'href="{hidden}' not in text and f'/{hidden}"' not in text, (f, hidden)
    assert not any(p.stem in ("review", "tasks", "registry", "records", "manual") for p in mem.rglob("*.html"))
    _inject(mem, '<a href="../review.html">Review</a>')
    assert any("points to /review" in v for v in static_site.check(mem, "member"))
    (mem / "review.html").write_text("<html></html>")
    assert any("admin page" in v for v in static_site.check(mem, "member"))
    _inject(mem, '<div data-audience="admin">x</div>', "index.html")
    assert any("block marked for admin" in v for v in static_site.check(mem, "member"))


# ------------------------------------------------------------ the gated Worker (plain node, if installed)

@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_gate_worker_with_node():
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, RADAR_TEST_PY_HASH=gate.hash_password("member-pass"))  # python hash, checked by the worker
    r = subprocess.run(["node", "--test", str(root / "tests" / "worker" / "gate-worker.test.mjs")],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "# fail 0" in r.stdout
