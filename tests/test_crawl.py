from datetime import date

from radar.crawl import runner
from radar.crawl.links import classify, is_document, parse_date, parse_links
from radar.net.client import Response


def test_parse_date_variants():
    assert parse_date("Board Meeting Minutes March 12, 2026") == date(2026, 3, 12)
    assert parse_date("/wp-content/uploads/2026/03/Agenda-03-12-2026.pdf") == date(2026, 3, 12)
    assert parse_date("Minutes_2025-11-04.pdf") == date(2025, 11, 4)
    assert parse_date("Quarterly Review Q2 2026") == date(2026, 6, 30)
    assert parse_date("Investment Policy Statement") is None


def test_classify_and_is_document():
    assert classify("Request for Proposals - Investment Consultant") == "RFP"
    assert classify("Regular Board Meeting Minutes") == "MINUTES"
    assert classify("Investment Policy Statement 2025") == "IPS"
    assert is_document("https://x.gov/DocumentCenter/View/1234/Agenda")
    assert is_document("https://x.org/wp-content/uploads/2026/01/a.pdf")
    assert not is_document("https://x.org/board/")


def test_links_skip_mailto_and_tel():
    _, links = parse_links('<a href="mailto:a@b.c">mail</a><a href="tel:3055550100">t</a><a href="/a.pdf">A</a>',
                           "https://x.org/")
    assert [l.url for l in links] == ["https://x.org/a.pdf"]


class FakeClient:
    def __init__(self, html):
        self.html = html

    def get(self, url):
        return Response(url, 200, self.html.encode(), "text/html", "x")


def test_shared_page_assigns_by_plan_word():
    html = """<a href="/p/Parks-Plan.pdf">Parks master plan</a><a href="/p/Pension-112664-Report.pdf">Chapter 112.664 Report</a>
              <h2>Police Officers' Pension</h2><a href="/p/Police-Minutes-01-15-2026.pdf">Police Minutes January 15, 2026</a>
              <h2>Firefighters' Pension</h2><a href="/p/Fire-IPS.pdf">Firefighters IPS</a>
              <a href="/p/Old-Minutes-01-15-2015.pdf">Police Minutes January 15, 2015</a>
"""
    allocs = [{"allocator_id": "fl-x-police", "template": "T-GOV"}, {"allocator_id": "fl-x-fire", "template": "T-GOV"}]
    pages = {"fl-x-police": ["https://x.gov/pensions"], "fl-x-fire": ["https://x.gov/pensions"]}
    links = {l.url.rsplit("/", 1)[-1]: l for l in runner.collect_links(FakeClient(html), allocs, pages)}
    assert links["Police-Minutes-01-15-2026.pdf"].allocator_ids == ["fl-x-police"]
    assert links["Fire-IPS.pdf"].allocator_ids == ["fl-x-fire"]
    assert sorted(links["Pension-112664-Report.pdf"].allocator_ids) == ["fl-x-fire", "fl-x-police"]
    assert "Old-Minutes-01-15-2015.pdf" not in links  # older than 5 years
    assert "Parks-Plan.pdf" not in links  # not a pension document on a city page


def test_foreign_plan_page():
    fire = [{"allocator_id": "fl-hollywood-fire", "display_name": "Hollywood Firefighters' Pension", "template": "T-CP"}]
    assert runner.foreign_plan_page("https://www.hollywoodfl.org/172/Police-Officers-Pension-Board", fire)
    assert runner.foreign_plan_page("https://www.hollywoodfl.org/597/Employees-Retirement-Fund-Pension-Board", fire)
    assert not runner.foreign_plan_page("https://www.hollywoodfl.org/164/Firefighters-Pension-Board", fire)
    pf = [{"allocator_id": "fl-boca-pf", "display_name": "Boca Raton Police and Firefighters' Retirement System", "template": "T-CP"}]
    assert not runner.foreign_plan_page("https://www.myboca.us/337/Police-Firefighters-Retirement-System", pf)


def test_select_targets_depth():
    from datetime import timedelta
    from radar.crawl.runner import DocLink, select_targets
    today = date.today()
    L = [DocLink(f"https://x/a{i}.pdf", "a", "AGENDA", today - timedelta(days=30 * i), "p", ["x"]) for i in range(30)]
    L += [DocLink(f"https://x/ips{i}.pdf", "ips", "IPS", today - timedelta(days=400 * i), "p", ["x"]) for i in range(4)]
    L += [DocLink("https://x/v.pdf", "valuation", "ACTUARIAL", None, "p", ["x"])]
    got = {d.url for d in select_targets(L)}
    assert sum(u.startswith("https://x/a") for u in got) == 19  # 0..18 months
    assert {"https://x/ips0.pdf", "https://x/ips1.pdf"} <= got and "https://x/ips3.pdf" not in got
    assert "https://x/v.pdf" in got
