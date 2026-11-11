"""D-B19: a candidate page that fails to load (file download, timeout) is skipped; the plan is not marked MANUAL."""

from radar.registry.discovery import discover_plan

BASE = "https://www.coralgables.com"


class FakeResp:
    def __init__(self, url, text):
        self.url, self.text, self.status, self.content_type = url, text, 200, "text/html"


class FakeClient:
    def robots_allows(self, url):
        return True, 0

    def get(self, url):
        if url.rstrip("/").endswith("/retirement"):
            return FakeResp(url, f"""<html><title>Retirement</title><body>
                <a href="{BASE}/pension-board-download">Pension board packet</a>
                <a href="{BASE}/pension-board-minutes">Pension board minutes</a></body></html>""")
        if "download" in url:
            raise RuntimeError("Page.goto: Download is starting")
        return FakeResp(url, f"""<html><body>
            <a href="{BASE}/files/Retirement-Board-Agenda-2026-10-15.pdf">Retirement Board Agenda October 15, 2026</a>
            <a href="{BASE}/files/Retirement-Board-Minutes-2026-09-17.pdf">Retirement Board Minutes September 17, 2026</a>
            </body></html>""")


def test_failed_candidate_does_not_fail_plan():
    seed = {"id": "fl-x", "name": "X Retirement", "type": "GENERAL", "city": "Coral Gables", "url": f"{BASE}/retirement",
            "template": "T-CITY", "source": "SF-CG"}
    out = discover_plan(FakeClient(), seed)
    assert out["access"] == "AUTO", out["access_reason"]
    assert any(p["documents"] for p in out["document_pages"])
