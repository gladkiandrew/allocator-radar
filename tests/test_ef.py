"""Phase C: Florida endowments and foundations."""

import io
import zipfile
from datetime import date, timedelta

import pytest

from radar.sources.endowments import irs, signals as S, store, universe

NS = 'xmlns="http://www.irs.gov/efile"'


def _row(rt="990", ein="590000001", oid="202401319349300001"):
    return irs.IndexRow(ein, "202306", rt, oid, "2024_TEOS_XML_01A", 2024)


def _990(contractors: str, sched_d: str = "", ty="2022", end="2023-06-30") -> bytes:
    return f"""<?xml version="1.0"?><Return {NS}><ReturnHeader><TaxPeriodEndDt>{end}</TaxPeriodEndDt><TaxYr>{ty}</TaxYr>
      <Filer><BusinessName><BusinessNameLine1Txt>EXAMPLE FOUNDATION INC</BusinessNameLine1Txt></BusinessName>
      <USAddress><AddressLine1Txt>100 Main Street</AddressLine1Txt><ZIPCd>32601</ZIPCd></USAddress></Filer></ReturnHeader>
      <ReturnData><IRS990>
        <Form990PartVIISectionAGrp><PersonNm>Jane Trustee</PersonNm><TitleTxt>CHAIR</TitleTxt></Form990PartVIISectionAGrp>
        <TotalAssetsGrp><EOYAmt>2745249683</EOYAmt></TotalAssetsGrp>
        <InvestmentsPubTradedSecGrp><EOYAmt>1619080</EOYAmt></InvestmentsPubTradedSecGrp>
        <InvestmentsOtherSecuritiesGrp><EOYAmt>2386980068</EOYAmt></InvestmentsOtherSecuritiesGrp>
        {contractors}
      </IRS990>{sched_d}</ReturnData></Return>""".encode()


def _contractor(name_xml, svc, amt):
    return (f"<ContractorCompensationGrp><ContractorName>{name_xml}</ContractorName><ContractorAddress><USAddress>"
            f"<AddressLine1Txt>1 Wall Street</AddressLine1Txt><ZIPCd>10005</ZIPCd></USAddress></ContractorAddress>"
            f"<ServicesDesc>{svc}</ServicesDesc><CompensationAmt>{amt}</CompensationAmt></ContractorCompensationGrp>")


BIZ = lambda n: f"<BusinessName><BusinessNameLine1Txt>{n}</BusinessNameLine1Txt></BusinessName>"
PERSON = lambda n: f"<PersonNm>{n}</PersonNm>"


# ---------------- 990 contractor parsing ----------------
def test_990_contractors_keep_firms_and_drop_people():
    xml = _990(_contractor(BIZ("CAMBRIDGE ASSOCIATES LLC"), "INVESTMENT CONSULTING", 3071782)
               + _contractor(PERSON("John Q Smith"), "FUNDRAISING COUNSEL", 400000)        # a person: never stored
               + _contractor(PERSON("BRIDGEWATER ASSOCIATES LP"), "INVESTMENT MGMT", 900000)  # a firm filed as PersonNm
               + _contractor(PERSON("Mary Jones CPA"), "AUDIT", 150000)                     # sole practitioner: dropped
               + _contractor(BIZ("THE EXPO GROUP"), "PRODUCTION SERVICES", 1734038),
               sched_d="""<IRS990ScheduleD><FinancialDerivativesGrp><BookValueAmt>0</BookValueAmt></FinancialDerivativesGrp>
                 <OtherSecuritiesGrp><Desc>HEDGE FUNDS</Desc><BookValueAmt>412000000</BookValueAmt></OtherSecuritiesGrp>
                 <OtherSecuritiesGrp><Desc>PRIVATE EQUITY</Desc><BookValueAmt>800000000</BookValueAmt></OtherSecuritiesGrp>
               </IRS990ScheduleD>""")
    f = irs.parse_return(xml, _row())
    firms = {c.firm: c for c in f.contractors}
    assert set(firms) == {"CAMBRIDGE ASSOCIATES LLC", "BRIDGEWATER ASSOCIATES LP", "THE EXPO GROUP"}
    assert firms["CAMBRIDGE ASSOCIATES LLC"].investment and firms["BRIDGEWATER ASSOCIATES LP"].investment
    assert not firms["THE EXPO GROUP"].investment and firms["CAMBRIDGE ASSOCIATES LLC"].amount == 3071782
    assert (f.tax_year, f.tax_period_end, f.total_assets_eoy, f.investments_other_eoy) == (2022, "2023-06-30", 2745249683, 2386980068)
    assert [(o.description, o.book_value) for o in f.other_investments] == [("HEDGE FUNDS", 412000000), ("PRIVATE EQUITY", 800000000)]
    # nothing personal survives the parse
    dumped = repr(f)
    for leak in ("John Q Smith", "Mary Jones", "Jane Trustee", "Wall Street", "Main Street", "10005", "32601"):
        assert leak not in dumped, leak


def test_990pf_contractors_and_investment_statement():
    xml = f"""<?xml version="1.0"?><Return {NS}><ReturnHeader><TaxPeriodEndDt>2023-12-31</TaxPeriodEndDt><TaxYr>2023</TaxYr></ReturnHeader>
      <ReturnData><IRS990PF><Form990PFBalanceSheetsGrp><TotalAssetsEOYFMVAmt>2533365916</TotalAssetsEOYFMVAmt>
        <CorporateStockEOYFMVAmt>500000000</CorporateStockEOYFMVAmt><CorporateBondsEOYFMVAmt>62719778</CorporateBondsEOYFMVAmt>
        <OtherInvestmentsEOYFMVAmt>1575553422</OtherInvestmentsEOYFMVAmt></Form990PFBalanceSheetsGrp>
        <OfficerDirTrstKeyEmplInfoGrp><CompensationOfHghstPdCntrctGrp><BusinessName><BusinessNameLine1Txt>CAMBRIDGE ASSOCIATES LLC</BusinessNameLine1Txt></BusinessName>
          <USAddress><AddressLine1Txt>125 High Street</AddressLine1Txt></USAddress><ServiceTypeTxt>OUTSOURCED CHIEF INVESTMENT OFFICE</ServiceTypeTxt>
          <CompensationAmt>3071782</CompensationAmt></CompensationOfHghstPdCntrctGrp>
          <CompensationOfHghstPdCntrctGrp><PersonNm>Pat Lawyer</PersonNm><ServiceTypeTxt>LEGAL</ServiceTypeTxt><CompensationAmt>99000</CompensationAmt></CompensationOfHghstPdCntrctGrp>
        </OfficerDirTrstKeyEmplInfoGrp></IRS990PF>
        <InvestmentsOtherSchedule2><InvestmentsOtherGrp><CategoryOrItemTxt>DE SHAW COMPOSITE FUND LLC - HEDGE FUND</CategoryOrItemTxt>
          <BookValueAmt>40000000</BookValueAmt><EndOfYearFMVAmt>52000000</EndOfYearFMVAmt></InvestmentsOtherGrp></InvestmentsOtherSchedule2>
      </ReturnData></Return>""".encode()
    f = irs.parse_return(xml, _row("990PF"))
    assert [c.firm for c in f.contractors] == ["CAMBRIDGE ASSOCIATES LLC"] and f.contractors[0].investment
    assert f.total_assets_eoy == 2533365916 and f.investments_public_eoy == 562719778 and f.investments_other_eoy == 1575553422
    assert f.other_investments[0].fmv == 52000000 and "Pat Lawyer" not in repr(f)
    assert irs.citation(f, "contractors").startswith("Form 990-PF TY2023 Part VII line 3")
    old = irs.Filing("1", "x", "990PF", "2019-12-31", 2019)
    assert "Part VIII line 3" in irs.citation(old, "contractors")
    assert irs.citation(irs.Filing("1", "x", "990", "2023-06-30", 2022), "contractors").startswith("Form 990 TY2022 Part VII Section B line 1")


def test_firm_name_rule():
    assert irs.is_firm_name("NEUBERGER BERMAN GROUP LLC")
    assert irs.is_firm_name("PRUDENTIAL INVESTMENTS")  # BusinessName field: looser rule
    assert not irs.is_firm_name("John Smith")
    assert not irs.is_firm_name("JOHN SMITH CPA PA")
    assert not irs.is_firm_name("Jane Doe Consulting", filed_as_person=True)  # PersonNm needs a strong corporate token
    assert irs.is_firm_name("COMMONFUND CAPITAL INC", filed_as_person=True)


# ---------------- remote zip member ----------------
class _ZipClient:
    """Serves Range requests against an in-memory zip, like apps.irs.gov."""

    def __init__(self, blob: bytes):
        self.blob = blob

    def get(self, url, headers=None, max_bytes=None):
        spec = headers["Range"].split("=", 1)[1]
        if spec.startswith("-"):
            body = self.blob[-int(spec[1:]):]
        else:
            a, b = spec.split("-")
            body = self.blob[int(a):int(b) + 1]
        return type("R", (), {"status": 206, "content": body})()


def test_fetch_one_member_from_a_remote_zip(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(50):
            z.writestr(f"2024_TEOS_XML_01A/2024000000{i:02d}_public.xml", f"<Return n='{i}'>{'x' * 500}</Return>")
    row = irs.IndexRow("1", "202312", "990", "202400000017", "2024_TEOS_XML_01a", 2024)
    assert row.zip_url.endswith("/2024/2024_TEOS_XML_01A.zip")  # the index's lowercase batch letter is normalized
    got = irs.fetch_return(_ZipClient(buf.getvalue()), row, tmp_path)
    assert got.startswith(b"<Return n='17'>")
    assert list(tmp_path.glob("cd_*.json"))  # central directory cached (names and offsets only)


# ---------------- signals and the WATCHING cap ----------------
def _sig(source_type, stype="MANDATE_AWARDED", days_ago=10, status="approved", qualifier="manager"):
    return dict(signal_id=f"{source_type}-{stype}", ein="590000001", signal_type=stype, qualifier=qualifier,
                event_date=date.today() - timedelta(days=days_ago), source_type=source_type, review_status=status,
                summary="x", hf_relevant=False)


def test_990_only_signals_are_capped_at_watching():
    r = S.ef_readiness([_sig("990")], has_filings=True)  # would be DEPLOYING if it came from a committee document
    assert r.tier == "WATCHING" and "990" in r.rule_meaning
    r = S.ef_readiness([_sig("990", "EF_MANAGER_ADDED"), _sig("990", "TARGET_ALLOCATION_CHANGED", qualifier="increase")], True)
    assert r.tier == "WATCHING"


def test_committee_document_can_set_deploying_or_preparing():
    assert S.ef_readiness([_sig("committee"), _sig("990")], True).tier == "DEPLOYING"
    assert S.ef_readiness([_sig("committee", "MANAGER_TERMINATED", 120, qualifier=None)], True).tier == "PREPARING"
    assert S.ef_readiness([_sig("committee", status="new")], True).tier == "WATCHING"  # pending never counts
    assert S.ef_readiness([], has_filings=False).tier == "STALE"


def test_derive_manager_changes_and_new_hedge_fund_line():
    org = {"ein": "590000001"}
    f_now = {"object_id": "B", "ein": "590000001", "return_type": "990", "tax_year": 2023, "tax_period_end": date(2024, 6, 30)}
    f_prev = {"object_id": "A", "ein": "590000001", "return_type": "990", "tax_year": 2022, "tax_period_end": date(2023, 6, 30)}
    c = lambda firm, inv=True: {"firm": firm, "service": "INVESTMENT MANAGEMENT", "amount": 1e6, "investment": inv}
    contractors = {"B": [c("CAMBRIDGE ASSOCIATES LLC"), c("AQR CAPITAL MANAGEMENT LLC")], "A": [c("Cambridge Associates, LLC"), c("WELLINGTON MANAGEMENT CO LLP")]}
    others = {"B": [{"description": "ABSOLUTE RETURN FUNDS", "book_value": 9e7, "fmv": None}],
              "A": [{"description": "PRIVATE EQUITY", "book_value": 5e7, "fmv": None}]}
    out = S.derive(org, [f_now, f_prev], contractors, others, irs.filing_url, lambda f, w: f"cite {w} TY{f['tax_year']}")
    by = {}
    for s in out:
        by.setdefault(s["signal_type"], []).append(s)
    assert [s["summary"].split(" (")[0] for s in by["EF_MANAGER_ADDED"]] == ["AQR CAPITAL MANAGEMENT LLC"]  # Cambridge matched despite punctuation
    assert "WELLINGTON MANAGEMENT CO LLP" in by["EF_MANAGER_DROPPED"][0]["summary"]
    assert by["EF_ALT_HOLDING_ADDED"][0]["hf_relevant"] and "ABSOLUTE RETURN FUNDS" in by["EF_ALT_HOLDING_ADDED"][0]["summary"]
    assert all(s["source_type"] == "990" and s["cite"].startswith("cite") and s["date_basis"] == "tax_period_end" for s in out)
    assert all(s["review_status"] == "auto_approved" for s in out)
    assert S.ef_readiness(out, True).tier == "WATCHING"


# ---------------- universe ----------------
def _bmf(ein, name, ntee="", fc="15", assets=1e9):
    return {"EIN": ein, "NAME": name, "CITY": "MIAMI", "STATE": "FL", "SUBSECTION": "03", "FOUNDATION": fc,
            "ASSET_AMT": assets, "NTEE_CD": ntee, "TAX_PERIOD": "202306", "RULING": ""}


def test_universe_rules():
    assert universe.classify(_bmf("1", "UNIVERSITY OF FLORIDA FOUNDATION INC", "B110")) == "UNIVERSITY_FOUNDATION"
    assert universe.classify(_bmf("2", "BROWARD COLLEGE FOUNDATION INC", "T31")) == "UNIVERSITY_FOUNDATION"
    assert universe.classify(_bmf("3", "UNIVERSITY OF MIAMI", "B430", "11")) == "PRIVATE_UNIVERSITY"
    assert universe.classify(_bmf("650350357", "THE MIAMI FOUNDATION INC", "T30Z")) == "COMMUNITY_FOUNDATION"
    assert universe.classify(_bmf("4", "COMMUNITY FOUNDATION OF TAMPA BAY INC", "T31")) == "COMMUNITY_FOUNDATION"
    assert universe.classify(_bmf("5", "KNIGHT FOUNDATION", "B82", "04")) == "PRIVATE_FOUNDATION"
    assert universe.classify(_bmf("6", "UNIVERSITY ATHLETIC ASSOCIATION FOUNDATION", "B110")) is None
    picked = universe.select([_bmf(str(i), f"FAMILY FOUNDATION {i}", "T20", "04", i * 1e6) for i in range(30)])
    assert len(picked) == universe.CAPS["PRIVATE_FOUNDATION"] and picked[0]["ASSET_AMT"] == 29e6


def test_bmf_load_drops_street_and_zip():
    text = ("EIN,NAME,ICO,STREET,CITY,STATE,ZIP,GROUP,SUBSECTION,AFFILIATION,CLASSIFICATION,RULING,DEDUCTIBILITY,FOUNDATION,"
            "ACTIVITY,ORGANIZATION,STATUS,TAX_PERIOD,ASSET_CD,INCOME_CD,FILING_REQ_CD,PF_FILING_REQ_CD,ACCT_PD,ASSET_AMT,"
            "INCOME_AMT,REVENUE_AMT,NTEE_CD,SORT_NAME\n"
            "650464177,JOHN S AND JAMES L KNIGHT FOUNDATION INC,% SOMEONE,2850 TIGERTAIL AVE,MIAMI,FL,33133-5300,0,03,3,1000,"
            "199401,1,04,000000000,1,01,202312,9,9,1,1,12,2533365916,1,1,B82,\n")
    rows = universe.load_bmf(text)
    assert rows[0]["ASSET_AMT"] == 2533365916 and "STREET" not in rows[0] and "ZIP" not in rows[0] and "ICO" not in rows[0]
    assert "TIGERTAIL" not in repr(rows) and "33133" not in repr(rows)


# ---------------- store, wall, pages ----------------
def _seed_store():
    store.init()
    with store.connect() as con:
        con.execute("INSERT INTO ef_orgs (ein, name, kind, city, bmf_assets, rank_in_kind) VALUES "
                    "('590000001', 'EXAMPLE UNIVERSITY FOUNDATION INC', 'UNIVERSITY_FOUNDATION', 'Gainesville', 3e9, 1)")
        con.execute("INSERT INTO ef_filings (object_id, ein, return_type, tax_year, tax_period_end, total_assets, investments_other) "
                    "VALUES ('202401319349300001', '590000001', '990', 2023, DATE '2024-06-30', 3e9, 2e9)")
        con.execute("INSERT INTO ef_contractors (object_id, ein, tax_year, firm, service, amount, investment) VALUES ('202401319349300001', '590000001', 2023, 'CAMBRIDGE ASSOCIATES LLC', "
                    "'INVESTMENT CONSULTING', 3e6, true)")
        con.execute("INSERT INTO ef_other_investments (object_id, ein, tax_year, description, book_value, fmv) VALUES ('202401319349300001', '590000001', 2023, 'HEDGE FUNDS', 4e8, NULL)")
    from radar.sources.endowments import pipeline
    return pipeline.derive_signals()


def test_store_wall_catches_contact_data():
    from radar.sources.endowments import wall
    _seed_store()
    assert wall.check() == []
    with store.connect() as con:
        con.execute("UPDATE ef_contractors SET service = 'call 305-555-0177'")
    assert any("phone" in v for v in wall.check())


def test_ef_pages_render_and_export(tmp_path):
    from fastapi.testclient import TestClient

    from radar import static_export
    from radar.wall import static_site
    from radar.web.app import create_app

    assert _seed_store() >= 2
    c = TestClient(create_app())
    page = c.get("/ef")
    assert page.status_code == 200 and "Example University Foundation Inc" in page.text and "WATCHING" in page.text
    brief = c.get("/ef/590000001")
    assert brief.status_code == 200 and "CAMBRIDGE ASSOCIATES LLC" in brief.text and "Part VII Section B" in brief.text
    assert "IRS 990" in brief.text and "HEDGE FUNDS" in brief.text
    assert c.get("/ef/12").status_code == 404 and c.get("/ef/999999999").status_code == 404
    out = tmp_path / "dist"
    st = static_export.export(out)
    assert (out / "ef.html").is_file() and (out / "ef" / "590000001.html").is_file() and st["ef_briefs"] == 1
    assert 'href="../ef.html"' in (out / "ef" / "590000001.html").read_text()
    assert "Endowments &amp; Foundations" in (out / "index.html").read_text()
    assert static_site.check(out) == []


def test_deflate64_member(tmp_path):
    """The IRS 2025 batches store members with Deflate64 (zip method 9)."""
    import struct

    import inflate64

    payload = b"<Return>" + b"y" * 4000 + b"</Return>"
    d = inflate64.Deflater()
    comp = d.deflate(payload) + d.flush()
    name = b"2025000000001_public.xml"
    local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 9, 0, 0, 0, len(comp), len(payload), len(name), 0) + name + comp
    cd = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 9, 0, 0, 0, len(comp), len(payload), len(name), 0, 0, 0, 0, 0, 0) + name
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(cd), len(local), 0)
    row = irs.IndexRow("1", "202412", "990", "2025000000001", "2025_TEOS_XML_01A", 2025)
    assert irs.fetch_return(_ZipClient(local + cd + eocd), row, tmp_path) == payload


def test_committee_signals_need_the_quote_on_the_page(monkeypatch):
    """A committee signal is stored only if its quote is on the cited page; terminations wait for a person."""
    from radar.sources.endowments import committee as C

    pages = ["Agenda", "The Investment Committee voted to issue a Request for Proposals for a hedge fund manager "
             "for the endowment pool. The Committee also voted to terminate Acme Capital as a manager."]
    monkeypatch.setattr(C, "_pages", lambda pdf: pages)
    today = date.today()
    res = C.CResult(document_date=today, document_kind="MINUTES", signals=[
        C.CSignal(signal_type="RFP_ISSUED", qualifier="manager", event_date=today, page=2,
                  summary="The Investment Committee voted to issue an RFP for a hedge fund manager.",
                  quote="voted to issue a Request for Proposals for a hedge fund manager for the endowment pool"),
        C.CSignal(signal_type="MANAGER_TERMINATED", qualifier="manager", event_date=today, page=2,
                  summary="The Committee voted to terminate Acme Capital.", quote="voted to terminate Acme Capital as a manager"),
        C.CSignal(signal_type="MANDATE_AWARDED", qualifier="manager", event_date=today, page=2,
                  summary="The Committee awarded a mandate.", quote="awarded a large mandate to Example Partners today"),
    ])
    provider = type("P", (), {"complete": lambda self, **kw: (res, None)})()
    client = type("Cl", (), {"get": lambda self, url, **kw: type("R", (), {"status": 200, "content": b"%PDF-1.7 x"})()})()
    store.init()
    out = C.read_document(client, provider, "596166292", "https://uwf.edu/x/minutes.pdf", "Investment Committee Minutes")
    assert out["signals"] == 2 and out["dropped_quote_not_on_page"] == 1
    with store.connect() as con:
        got = dict(con.execute("SELECT signal_type, review_status FROM ef_signals").fetchall())
    assert got == {"RFP_ISSUED": "auto_approved", "MANAGER_TERMINATED": "new"}
    with store.connect() as con:
        sigs = store.rows(con, "SELECT * FROM ef_signals")
    assert S.ef_readiness(sigs, True).tier == "DEPLOYING"  # a dated committee document can set DEPLOYING
    assert C.read_document(client, provider, "596166292", "https://uwf.edu/x/minutes.pdf", "x")["skipped"]


def test_committee_pages_are_scrubbed_text():
    """_pages reads a real PDF and returns scrubbed strings (contact details redacted before any model sees them)."""
    from fpdf import FPDF

    from radar.sources.endowments import committee as C

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 6, "Investment Committee minutes. The Committee voted to issue an RFP for a hedge fund manager. "
                         "Questions: call 850-555-0142.")
    pages = C._pages(bytes(pdf.output()))
    assert len(pages) == 1 and isinstance(pages[0], str)
    assert "hedge fund manager" in pages[0] and "850-555-0142" not in pages[0] and "REDACTED" in pages[0]


def test_hf_flag_and_investment_service_are_tight():
    assert S.HF.search("ABSOLUTE RETURN AND HEDGE FUNDS") and S.HF.search("ROKOS GLOBAL MACRO FUND LTD")
    assert S.HF.search("Quantitative strategies") and S.HF.search("ALTERNATIVE INVESTMENTS")
    assert not S.HF.search("GREEN THUMB INDUSTRIES WARRANTS 35,270.000 QUANTITY")
    assert not S.HF.search("BRIDGES PROPERTY ALTERNATIVES FUND V")
    xml = _990(_contractor(BIZ("DELOITTE TAX LLP"), "TAX PREPARATION AND ADVISORY", 200000)
               + _contractor(BIZ("PRIME BUCHHOLZ AND ASSOC INC"), "INVESTMENT CONSULTING", 300000))
    inv = {c.firm: c.investment for c in irs.parse_return(xml, _row()).contractors}
    assert inv == {"DELOITTE TAX LLP": False, "PRIME BUCHHOLZ AND ASSOC INC": True}


def test_garbled_firm_name_is_not_a_change():
    org = {"ein": "1"}
    f1 = {"object_id": "B", "ein": "1", "return_type": "990PF", "tax_year": 2023, "tax_period_end": date(2024, 6, 30)}
    f0 = {"object_id": "A", "ein": "1", "return_type": "990PF", "tax_year": 2022, "tax_period_end": date(2023, 6, 30)}
    c = lambda firm: {"firm": firm, "service": "INVESTMENT MANAGEMENT FEES", "amount": 1e5, "investment": True}
    out = S.derive(org, [f1, f0], {"B": [c("SOUTHERN SUN ASSET MANAGEMENT")], "A": [c("SOUTHERN SUN ASSET MANAGESOUTHERN SUN ASMENT")]},
                   {}, irs.filing_url, lambda f, w: "cite")
    assert not [s for s in out if s["signal_type"] in ("EF_MANAGER_ADDED", "EF_MANAGER_DROPPED")]


# ---------------- Hedge fund buyers view ----------------
def _seed_hf_store():
    store.init()
    with store.connect() as con:
        orgs = [("590000011", "ALPHA UNIVERSITY FOUNDATION INC", "UNIVERSITY_FOUNDATION"),
                ("590000022", "BETA FAMILY FOUNDATION", "PRIVATE_FOUNDATION"),
                ("590000033", "GAMMA COMMUNITY FOUNDATION INC", "COMMUNITY_FOUNDATION"),
                ("590000044", "DELTA COLLEGE FOUNDATION INC", "UNIVERSITY_FOUNDATION"),
                ("590000055", "EPSILON FOUNDATION", "PRIVATE_FOUNDATION")]
        for ein, name, kind in orgs:
            con.execute("INSERT INTO ef_orgs (ein, name, kind, city, bmf_assets, rank_in_kind) VALUES (?, ?, ?, 'Miami', 1e8, 1)",
                        [ein, name, kind])
        f = ("INSERT INTO ef_filings (object_id, ein, return_type, tax_year, tax_period_end, total_assets, investments_public, "
             "investments_other) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
        con.execute(f, ["202401000000000011", "590000011", "990", 2023, date(2024, 6, 30), 5e8, 1e8, 3e8])
        con.execute(f, ["202301000000000011", "590000011", "990", 2022, date(2023, 6, 30), 5e8, 1e8, 3e8])  # older: ignored
        con.execute(f, ["202401000000000022", "590000022", "990PF", 2024, date(2024, 12, 31), 6e8, None, 5e8])
        con.execute(f, ["202401000000000033", "590000033", "990", 2023, date(2024, 6, 30), 3e9, 1e9, 1e9])
        con.execute(f, ["202401000000000044", "590000044", "990", 2024, date(2025, 6, 30), 1e8, None, None])
        oi = "INSERT INTO ef_other_investments (object_id, ein, tax_year, description, book_value, fmv) VALUES (?, ?, ?, ?, ?, ?)"
        con.execute(oi, ["202401000000000011", "590000011", 2023, "HEDGE FUNDS", 4e7, None])
        con.execute(oi, ["202401000000000011", "590000011", 2023, "PRIVATE EQUITY", 2.6e8, None])
        con.execute(oi, ["202301000000000011", "590000011", 2022, "ABSOLUTE RETURN FUNDS", 9e9, None])  # older return
        con.execute(oi, ["202401000000000022", "590000022", 2024, "XYZ GLOBAL MACRO FUND LTD", 1e8, 1.2e8])
        con.execute(oi, ["202401000000000033", "590000033", 2023, "ALTERNATIVE INVESTMENTS", 9e8, None])


def test_hf_buyers_ranking_percent_and_not_disclosed():
    from radar.sources.endowments import views

    _seed_hf_store()
    rows = views.hf_buyers()
    by = {r["ein"]: r for r in rows}
    # ranked by the named hedge fund figure first, then the broader figure, then neither
    assert [r["ein"] for r in rows] == ["590000022", "590000011", "590000033", "590000044", "590000055"]
    assert [r["rank"] for r in rows] == [1, 2, 3, 4, 5]
    a, b, c, d, e = (by[k] for k in ("590000011", "590000022", "590000033", "590000044", "590000055"))
    # 990: Schedule D line, book value; denominator Part X lines 11 + 12; only the latest return counts
    assert a["hf"]["amount"] == 4e7 and a["hf"]["basis"] == "book value" and a["hf_pct"] == pytest.approx(10.0)
    assert a["alt"]["amount"] == 3e8 and a["alt_pct"] == pytest.approx(75.0) and a["denom"]["amount"] == 4e8
    assert a["hf"]["cite"] == "Form 990 TY2023 Schedule D Part VII (investments, other securities)"
    assert a["alt"]["cite"].startswith("Form 990 TY2023 Part X line 12")
    assert "Part X line 11" in a["denom"]["cite"] and "Part X line 12" in a["denom"]["cite"]
    assert a["latest"]["url"].endswith("/590000011/202401000000000011/full")
    # 990-PF: FMV used when the line has one; a blank public-securities line counts as zero and is reported as blank
    assert b["hf"]["amount"] == 1.2e8 and b["hf"]["basis"] == "FMV" and b["hf_pct"] == pytest.approx(24.0)
    assert b["denom"]["blank"] == ["public securities"] and b["alt"]["basis"] == "FMV"
    assert b["hf"]["cite"] == "Form 990-PF TY2024 Part II line 13 statement (investments, other)"
    # "ALTERNATIVE INVESTMENTS" is not a hedge fund line: no hedge fund figure, noted under the broader figure
    assert c["hf"] is None and c["hf_pct"] is None and c["alt_pct"] == pytest.approx(50.0)
    assert [x["description"] for x in c["alt_named"]] == ["ALTERNATIVE INVESTMENTS"]
    # nothing disclosed: None, never 0, and no percent
    assert d["hf"] is None and d["alt"] is None and d["denom"] is None and d["alt_pct"] is None
    assert e["latest"] is None and e["hf"] is None and e["alt"] is None


def test_hf_named_lines_are_tighter_than_the_hf_flag():
    from radar.sources.endowments.views import HF_NAMED

    for s in ("HEDGE FUNDS", "ABSOLUTE RETURN AND HEDGE FUNDS", "ROKOS GLOBAL MACRO FUND LTD", "99NETA4T7 LIGHTHOUSE MULTI-STR",
              "INFINITY PREMIER (QP) CAYMAN LP - HEDGE FUND", "Multi-strategy fund"):
        assert HF_NAMED.search(s), s
    for s in ("ALTERNATIVE INVESTMENTS", "ALTERNATIVES & PRIVATE EQUITIES", "PRIVATE EQUITY", "OSSO MULTIFAMILY I LP",
              "GOLDEN TREE MULTI SECTOR", "DIVERSIFIED EQUITY FUND", "GREEN THUMB INDUSTRIES WARRANTS 35,270.000 QUANTITY"):
        assert not HF_NAMED.search(s), s


def test_hf_buyers_page_and_static_export(tmp_path):
    from fastapi.testclient import TestClient

    from radar import static_export
    from radar.wall import static_site
    from radar.web.app import create_app

    _seed_hf_store()
    c = TestClient(create_app())
    assert 'href="/ef/hedge-fund-buyers"' in c.get("/ef").text  # the toggle on the by-assets view
    page = c.get("/ef/hedge-fund-buyers")
    t = page.text
    assert page.status_code == 200 and 'href="/ef"' in t and "Hedge fund buyers" in t
    assert t.index("Beta Family Foundation") < t.index("Alpha University Foundation Inc") < t.index("Gamma Community Foundation Inc") \
        < t.index("Delta College Foundation Inc") < t.index("Epsilon Foundation")
    assert "$120,000,000" in t and "24.0% of investments" in t and "10.0% of investments" in t
    assert "Form 990 TY2023 Schedule D Part VII (investments, other securities)" in t
    assert "Form 990-PF TY2024 Part II line 13 statement (investments, other)" in t
    assert "https://projects.propublica.org/nonprofits/organizations/590000011/202401000000000011/full" in t
    assert t.count("not disclosed") >= 5 and "% not computable" in t and "public securities + other investments" in t
    assert "$0" not in t
    out = tmp_path / "dist"
    static_export.export(out)
    hf = (out / "ef-hf.html").read_text()
    assert 'href="ef.html"' in hf and 'href="ef/590000022.html"' in hf
    assert 'href="ef-hf.html"' in (out / "ef.html").read_text()
    assert 'href="../ef.html"' in (out / "ef" / "590000022.html").read_text()
    assert static_site.check(out) == []
