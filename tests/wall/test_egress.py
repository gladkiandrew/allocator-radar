"""Wall layer 4: outbound. GET/HEAD only, allowlisted hosts only, every request logged."""
import pytest

from radar import db
from radar.net.client import PoliteClient, RefusedRequest
from radar.wall import live


def test_post_is_refused_before_any_socket():
    c = PoliteClient()
    with pytest.raises(RefusedRequest):
        c.request("POST", "https://gese.org/")
    with pytest.raises(RefusedRequest):
        c.request("PUT", "https://gese.org/")


def test_unlisted_and_manual_hosts_refused_and_logged():
    c = PoliteClient()
    for url in ["https://example.com/", "https://www.sbafla.com/x.pdf", "https://www.resourcecenters.com/",
                "https://www.linkedin.com/company/x", "mailto:someone@example.invalid"]:
        with pytest.raises(RefusedRequest):
            c.get(url)
    with db.connect() as con:
        n = con.execute("SELECT count(*) FROM egress_log WHERE error LIKE 'refused%'").fetchone()[0]
        assert con.execute("SELECT count(*) FROM egress_log WHERE url LIKE '%someone%'").fetchone()[0] == 0
        assert live.check_egress(con) == [] and live.check_sweep(con) == []
    assert n == 5


def test_egress_check_flags_post():
    with db.connect() as con:
        con.execute("INSERT INTO egress_log VALUES (now(), 'POST', 'https://evil.test/form', 'evil.test', 200, 0, NULL, NULL, 'crawl', NULL)")
        assert live.check_egress(con)
        con.execute("DELETE FROM egress_log")
        con.execute("INSERT INTO egress_log VALUES (now(), 'POST', 'https://api.anthropic.com/v1/messages', 'api.anthropic.com', 200, 0, NULL, NULL, 'llm', NULL)")
        assert live.check_egress(con) == []
