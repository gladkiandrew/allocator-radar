"""IRS Form 990 / 990-PF e-file XML, read straight from the IRS's public TEOS batches.

The IRS publishes e-filed returns as zip batches (~100 MB each) plus a yearly index CSV
(apps.irs.gov/pub/epostcard/990/xml/<year>/). We never download a whole batch: the index gives
the batch for a return, one ranged GET reads the zip's central directory, and one more reads the
single XML member. Every request goes through radar.net.client (allowlist, robots, per-host delay).

THE WALL: a raw return lists officers, trustees, employees, their pay and street addresses. The XML is parsed
in memory and dropped. Kept: firm names, dollar amounts, line labels, and (owner's decision, D-B28) the names and
titles of officers, directors and trustees as printed. Never read: any compensation, hours or benefit element, any
address element, and employees who are not officers, directors, trustees or key employees.
"""

from __future__ import annotations

import csv
import io
import json
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

BASE = "https://apps.irs.gov/pub/epostcard/990/xml"
INDEX_MAX_BYTES = 200 * 1024 * 1024  # index_<year>.csv is ~80-95 MB and the server ignores Range on it


@dataclass(frozen=True)
class IndexRow:
    ein: str
    tax_period: str  # YYYYMM
    return_type: str  # 990, 990PF, 990EZ, 990T ...
    object_id: str
    batch: str
    index_year: int

    @property
    def zip_url(self) -> str:
        # The index sometimes lowercases the batch letter ("05a"); the files on the server are "05A".
        return f"{BASE}/{self.index_year}/{self.batch.upper()}.zip"


def index_rows(client, year: int, eins: set[str], cache_dir: Path) -> list[IndexRow]:
    """Rows of index_<year>.csv for these EINs. The filtered rows are cached; the 90 MB file is not kept."""
    cache = cache_dir / f"index_{year}.json"
    cached = json.loads(cache.read_text()) if cache.exists() else {"eins": [], "rows": []}
    if eins <= set(cached["eins"]):
        return [IndexRow(**r) for r in cached["rows"] if r["ein"] in eins]
    resp = client.get(f"{BASE}/{year}/index_{year}.csv", max_bytes=INDEX_MAX_BYTES)
    if resp.status != 200:
        raise RuntimeError(f"index_{year}.csv returned {resp.status}")
    want = eins | set(cached["eins"])
    rows = []
    for r in csv.DictReader(io.StringIO(resp.content.decode("utf-8", errors="replace"))):
        ein = (r.get("EIN") or "").strip().zfill(9)
        if ein in want and r.get("OBJECT_ID") and r.get("XML_BATCH_ID"):
            rows.append(IndexRow(ein, r["TAX_PERIOD"].strip(), r["RETURN_TYPE"].strip(), r["OBJECT_ID"].strip(),
                                 r["XML_BATCH_ID"].strip(), year).__dict__)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"eins": sorted(want), "rows": rows}))
    return [IndexRow(**r) for r in rows if r["ein"] in eins]


# ---------------- one member out of a remote zip ----------------
@dataclass
class _Member:
    method: int
    csize: int
    offset: int
    name: str


def _range(client, url: str, spec: str) -> bytes:
    r = client.get(url, headers={"Range": f"bytes={spec}"})
    if r.status != 206:
        raise RuntimeError(f"{url} did not honor Range ({r.status})")
    return r.content


def central_directory(client, url: str, cache_dir: Path) -> dict[str, _Member]:
    """member basename -> location. Cached per batch (file names and offsets only, no return content)."""
    cache = cache_dir / ("cd_" + url.rsplit("/", 2)[-2] + "_" + url.rsplit("/", 1)[-1] + ".json")
    if cache.exists():
        return {k: _Member(**v) for k, v in json.loads(cache.read_text()).items()}
    tail = _range(client, url, "-65558")
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise RuntimeError(f"{url}: end of central directory not found")
    _, _, _, _, n, cd_size, cd_off, _ = struct.unpack("<IHHHHIIH", tail[eocd:eocd + 22])
    if cd_off == 0xFFFFFFFF or cd_size == 0xFFFFFFFF or n == 0xFFFF:  # ZIP64
        loc = tail.rfind(b"PK\x06\x07", 0, eocd)
        z64_off = struct.unpack("<IIQI", tail[loc:loc + 20])[2]
        rec = _range(client, url, f"{z64_off}-{z64_off + 55}")
        n, cd_size, cd_off = struct.unpack("<QQQ", rec[32:56])
    cd = _range(client, url, f"{cd_off}-{cd_off + cd_size - 1}")
    out, i = {}, 0
    while i + 46 <= len(cd) and cd[i:i + 4] == b"PK\x01\x02":
        method, = struct.unpack("<H", cd[i + 10:i + 12])
        csize, usize, nlen, xlen, clen = struct.unpack("<IIHHH", cd[i + 20:i + 34])
        off, = struct.unpack("<I", cd[i + 42:i + 46])
        name = cd[i + 46:i + 46 + nlen].decode("utf-8", errors="replace")
        extra = cd[i + 46 + nlen:i + 46 + nlen + xlen]
        if 0xFFFFFFFF in (csize, usize, off):  # ZIP64 extra field: the 0xFFFFFFFF values, in order
            j = 0
            while j + 4 <= len(extra):
                tag, size = struct.unpack("<HH", extra[j:j + 4])
                if tag == 1:
                    vals, k = [], j + 4
                    for present in (usize == 0xFFFFFFFF, csize == 0xFFFFFFFF, off == 0xFFFFFFFF):
                        if present:
                            vals.append(struct.unpack("<Q", extra[k:k + 8])[0]); k += 8
                    it = iter(vals)
                    usize = next(it) if usize == 0xFFFFFFFF else usize
                    csize = next(it) if csize == 0xFFFFFFFF else csize
                    off = next(it) if off == 0xFFFFFFFF else off
                j += 4 + size
        out[name.rsplit("/", 1)[-1]] = _Member(method, csize, off, name)
        i += 46 + nlen + xlen + clen
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({k: v.__dict__ for k, v in out.items()}))
    return out


def fetch_return(client, row: IndexRow, cache_dir: Path) -> bytes:
    """The XML bytes of one return (in memory only)."""
    members = central_directory(client, row.zip_url, cache_dir)
    m = members.get(f"{row.object_id}_public.xml")
    if m is None:
        raise KeyError(f"{row.object_id} not in {row.batch}")
    blob = _range(client, row.zip_url, f"{m.offset}-{m.offset + 30 + len(m.name.encode()) + 2048 + m.csize}")
    nlen, xlen = struct.unpack("<HH", blob[26:30])
    data = blob[30 + nlen + xlen:30 + nlen + xlen + m.csize]
    if m.method == 0:
        return data
    if m.method == 8:
        return zlib.decompressobj(-15).decompress(data)
    if m.method == 9:  # Deflate64: the IRS 2025 batches use it; zlib cannot read it
        import inflate64

        return inflate64.Inflater().inflate(data)
    raise RuntimeError(f"unsupported zip method {m.method}")


# ---------------- parsing (firm names and amounts only) ----------------
def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _num(el: ET.Element | None) -> float | None:
    if el is None or el.text is None:
        return None
    try:
        return float(el.text.strip())
    except ValueError:
        return None


def _txt(el: ET.Element | None) -> str:
    return " ".join((el.text or "").split()) if el is not None else ""


ORG_TOKENS = re.compile(
    r"\b(LLC|L\.L\.C|INC|INCORPORATED|LP|L\.P|LLP|LTD|LIMITED|CORP|CORPORATION|COMPANY|CO|GROUP|PARTNERS|PARTNERSHIP|"
    r"ASSOCIATES|MANAGEMENT|MGMT|CAPITAL|ADVISORS|ADVISERS|ADVISORY|INVESTMENTS?|TRUST|BANK|BANCORP|SECURITIES|FUNDS?|"
    r"ASSET|HOLDINGS|CONSULTING|SERVICES|PLC|N\.A|NA|AG|SA|GMBH|UNIVERSITY|FOUNDATION|FINANCIAL|STRATEGIES|GLOBAL)\b",
    re.I)


STRONG_ORG = re.compile(
    r"\b(LLC|L\.L\.C|INC|INCORPORATED|LP|L\.P|LLP|LTD|LIMITED|CORP|CORPORATION|COMPANY|GROUP|PARTNERS|PARTNERSHIP|"
    r"ASSOCIATES|MANAGEMENT|MGMT|CAPITAL|ADVISORS|ADVISERS|INVESTMENTS|TRUST|BANK|BANCORP|SECURITIES|FUNDS?|PLC|N\.A)\b", re.I)


def is_firm_name(name: str, filed_as_person: bool = False) -> bool:
    """Keep a contractor only when the name is plainly an organization. A bare personal name, or a sole
    practitioner styled 'Name PA/CPA', is dropped: the Wall stores no individual's name. Some filers put
    firms in the PersonNm field; those pass only with a strong corporate token (LLC, LP, Capital ...)."""
    if not name:
        return False
    return bool((STRONG_ORG if filed_as_person else ORG_TOKENS).search(name))


def _contractor_name(g: ET.Element) -> tuple[str, bool]:
    """(name, filed_as_person). Address elements are never read."""
    biz = g.find(".//BusinessName")
    if biz is not None:
        return " ".join(x for x in (_txt(biz.find("BusinessNameLine1Txt")), _txt(biz.find("BusinessNameLine2Txt"))) if x), False
    person = g.find(".//PersonNm")
    return (_txt(person), True) if person is not None else ("", False)


def _person_title(g: ET.Element) -> tuple[str, str]:
    """(name, title) of one Part VII Section A / 990-PF Part VII line 1 entry. Pay, hours and address are not read."""
    name = _txt(g.find("PersonNm"))
    if not name and (biz := g.find("BusinessName")) is not None:
        name = _txt(biz.find("BusinessNameLine1Txt"))
    return name, _txt(g.find("TitleTxt"))


def _officers_990(form: ET.Element) -> list[Officer]:
    out = []
    for g in form.findall("Form990PartVIISectionAGrp"):
        if g.find("FormerOfcrDirectorTrusteeInd") is not None:
            continue
        if g.find("IndividualTrusteeOrDirectorInd") is not None or g.find("InstitutionalTrusteeInd") is not None:
            role = "director_trustee"
        elif g.find("OfficerInd") is not None:
            role = "officer"
        elif g.find("KeyEmployeeInd") is not None:
            role = "key_employee"
        else:
            continue  # highest-compensated employees who hold no office are not kept
        name, title = _person_title(g)
        if name:
            out.append(Officer(name, title, role))
    return out


_OFFICER_TITLE = re.compile(r"president|chair|treasurer|secretary|officer|\bceo\b|\bcfo\b|\bcoo\b|director of|executive director", re.I)


def _officers_pf(form: ET.Element) -> list[Officer]:
    out = []
    for g in form.findall(".//OfficerDirTrstKeyEmplGrp"):
        name, title = _person_title(g)
        if not name:
            continue
        role = "officer" if _OFFICER_TITLE.search(title) else "director_trustee"
        out.append(Officer(name, title, role))
    return out


INVESTMENT_SERVICE = re.compile(
    r"invest|portfolio|asset manag|fund manag|money manag|wealth manag|financial advis|hedge|private equity|"
    r"real estate fund|endowment|ocio|outsourced chief|custod|trust serv", re.I)
NOT_INVESTMENT = re.compile(r"\btax\b|audit|legal|attorney|fundrais|construct|architect|software|marketing", re.I)


def is_investment_service(svc: str) -> bool:
    return bool(INVESTMENT_SERVICE.search(svc or "")) and not NOT_INVESTMENT.search(svc or "")


@dataclass
class Contractor:
    firm: str
    service: str
    amount: float | None
    investment: bool


@dataclass
class OtherInvestment:
    description: str
    book_value: float | None
    fmv: float | None
    valuation: str | None = None  # Sch. D Part VII MethodValuationCd (F = end-of-year market value, C = cost) / 990-PF "AT COST" or "FMV"


@dataclass
class Officer:
    name: str
    title: str
    role: str  # director_trustee | officer | key_employee


@dataclass
class Filing:
    ein: str
    object_id: str
    return_type: str
    tax_period_end: str  # YYYY-MM-DD
    tax_year: int
    total_assets_eoy: float | None = None
    investments_public_eoy: float | None = None  # 990 Part X line 11 / 990-PF Part II lines 10b+10c
    investments_other_eoy: float | None = None  # 990 Part X line 12 / 990-PF Part II line 13 (FMV column)
    investments_other_book_eoy: float | None = None  # 990-PF Part II line 13 book value column
    contractors: list[Contractor] = field(default_factory=list)
    other_investments: list[OtherInvestment] = field(default_factory=list)  # Sch. D Part VII / 990-PF Part II line 13 statement
    contractor_count_over_100k: int | None = None
    officers: list[Officer] = field(default_factory=list)  # names and titles only (D-B28)
    endowment_eoy: float | None = None  # Schedule D Part V line 1g column (a), current year end balance
    endowment_boy: float | None = None  # Schedule D Part V line 1a column (a)
    investments_program_eoy: float | None = None  # 990 Part X line 13 (program-related investments)
    investments_land_eoy: float | None = None  # 990-PF Part II line 11 (land, buildings held for investment)
    investments_mortgage_eoy: float | None = None  # 990-PF Part II line 12 (mortgage loans)


def parse_return(xml: bytes, row: IndexRow) -> Filing:
    root = _strip_ns(ET.fromstring(xml))
    hdr = root.find("ReturnHeader")
    end = _txt(hdr.find("TaxPeriodEndDt")) if hdr is not None else ""
    tax_year = int(_txt(hdr.find("TaxYr")) or end[:4]) if hdr is not None else int(row.tax_period[:4])
    f = Filing(row.ein, row.object_id, row.return_type, end, tax_year)
    data = root.find("ReturnData")
    if data is None:
        return f
    if (form := data.find("IRS990")) is not None:
        f.total_assets_eoy = _num(form.find("TotalAssetsGrp/EOYAmt"))
        f.investments_public_eoy = _num(form.find("InvestmentsPubTradedSecGrp/EOYAmt"))
        f.investments_other_eoy = _num(form.find("InvestmentsOtherSecuritiesGrp/EOYAmt"))
        f.contractor_count_over_100k = int(_num(form.find("CntrctRcvdGreaterThan100KCnt")) or 0)
        f.investments_program_eoy = _num(form.find("InvestmentsProgramRelatedGrp/EOYAmt"))
        f.officers = _officers_990(form)
        for g in form.findall("ContractorCompensationGrp"):  # Part VII Section B line 1
            name, as_person = _contractor_name(g)
            if not is_firm_name(name, as_person):
                continue  # people are never stored
            svc = _txt(g.find("ServicesDesc"))
            f.contractors.append(Contractor(name, svc, _num(g.find("CompensationAmt")), is_investment_service(svc)))
        sd = data.find("IRS990ScheduleD")
        if sd is not None and (e := sd.find("CYEndwmtFundGrp")) is not None:  # Part V Endowment Funds, current year
            f.endowment_eoy = _num(e.find("EndYearBalanceAmt"))
            f.endowment_boy = _num(e.find("BeginningYearBalanceAmt"))
        if sd is not None:  # Part VII Investments - Other Securities
            for tag, label in (("FinancialDerivativesGrp", "Financial derivatives"),
                               ("CloselyHeldEquityInterestsGrp", "Closely held equity interests")):
                g = sd.find(tag)
                if g is not None and _num(g.find("BookValueAmt")):
                    f.other_investments.append(OtherInvestment(label, _num(g.find("BookValueAmt")), None))
            for g in sd.findall("OtherSecuritiesGrp"):
                f.other_investments.append(OtherInvestment(_txt(g.find("Desc")) or "Other", _num(g.find("BookValueAmt")), None,
                                                           _txt(g.find("MethodValuationCd")) or None))
    elif (form := data.find("IRS990PF")) is not None:
        f.total_assets_eoy = _num(form.find("Form990PFBalanceSheetsGrp/TotalAssetsEOYFMVAmt")) or \
            _num(form.find("Form990PFBalanceSheetsGrp/TotalAssetsEOYAmt"))
        bs = form.find("Form990PFBalanceSheetsGrp")
        if bs is not None:
            # The e-file schema spells the government line USGovtObligationsEOYFMVAmt; a printed 0 stays 0 (R6 d).
            pub = [_num(bs.find(t)) for t in ("CorporateStockEOYFMVAmt", "CorporateBondsEOYFMVAmt", "USGovtObligationsEOYFMVAmt",
                                             "USGovernmentObligationsEOYFMVAmt")]
            f.investments_public_eoy = None if all(v is None for v in pub) else sum(v for v in pub if v is not None)
            f.investments_other_eoy = _num(bs.find("OtherInvestmentsEOYFMVAmt"))
            f.investments_other_book_eoy = _num(bs.find("OtherInvestmentsEOYAmt"))
            f.investments_land_eoy = _num(bs.find("InvestmentsLandEOYFMVAmt"))
            f.investments_mortgage_eoy = _num(bs.find("MortgageLoansEOYFMVAmt"))
        f.officers = _officers_pf(form)
        for g in form.findall(".//CompensationOfHghstPdCntrctGrp"):  # five highest-paid contractors, professional services
            name, as_person = _contractor_name(g)
            if not is_firm_name(name, as_person):
                continue
            svc = _txt(g.find("ServiceTypeTxt"))
            f.contractors.append(Contractor(name, svc, _num(g.find("CompensationAmt")), is_investment_service(svc)))
        for g in data.findall(".//InvestmentsOtherSchedule2/InvestmentsOtherGrp") + data.findall(".//InvestmentsOtherSchedule/InvestmentsOtherGrp"):
            desc = _txt(g.find("CategoryOrItemTxt")) or _txt(g.find("Desc"))
            if desc:
                fmv = _num(g.find("EOYFMVAmt"))
                fmv = fmv if fmv is not None else _num(g.find("EndOfYearFMVAmt"))
                f.other_investments.append(OtherInvestment(desc, _num(g.find("BookValueAmt")), fmv,
                                                           _txt(g.find("ListedAtCostOrFMVCd")) or None))
    return f


def citation(f: Filing, what: str) -> str:
    """Form, tax year and part/line for one kind of fact."""
    pf = f.return_type.upper().startswith("990PF")
    ty = f.tax_year
    if pf:
        part_vii = "Part VII" if ty >= 2020 else "Part VIII"  # the 2020 revision removed old Part V; Part VIII became VII
        return {"contractors": f"Form 990-PF TY{ty} {part_vii} line 3 (five highest-paid independent contractors)",
                "total_assets": f"Form 990-PF TY{ty} Part II line 16 (total assets, fair market value)",
                "investments_other": f"Form 990-PF TY{ty} Part II line 13 (investments, other)",
                "other_investments": f"Form 990-PF TY{ty} Part II line 13 statement (investments, other)",
                "investments_public": f"Form 990-PF TY{ty} Part II lines 10a-10c (securities, fair market value)",
                "investments_total": f"Form 990-PF TY{ty} Part II lines 10a-13 (investments, fair market value)",
                "officers": f"Form 990-PF TY{ty} {part_vii} line 1 (officers, directors, trustees; names and titles)",
                "endowment": f"Form 990-PF TY{ty}: no endowment schedule (Schedule D Part V is a Form 990 schedule)"}[what]
    return {"contractors": f"Form 990 TY{ty} Part VII Section B line 1 (five highest compensated independent contractors)",
            "total_assets": f"Form 990 TY{ty} Part X line 16 (total assets)",
            "investments_other": f"Form 990 TY{ty} Part X line 12 (investments, other securities)",
            "other_investments": f"Form 990 TY{ty} Schedule D Part VII (investments, other securities)",
            "investments_public": f"Form 990 TY{ty} Part X line 11 (investments, publicly traded securities)",
            "investments_total": f"Form 990 TY{ty} Part X lines 11-13 (investments)",
            "officers": f"Form 990 TY{ty} Part VII Section A line 1a (officers, directors, trustees, key employees; names and titles)",
            "endowment": f"Form 990 TY{ty} Schedule D Part V line 1g column (a) (endowment, end of year balance)"}[what]


def filing_url(ein: str, object_id: str) -> str:
    """A page a reader can open for this return (ProPublica renders the IRS e-file)."""
    return f"https://projects.propublica.org/nonprofits/organizations/{int(ein)}/{object_id}/full"


# ---------------- contractor classification (D-B28) ----------------
_SVC_RULES = [
    ("law_firm", re.compile(r"\blegal\b|attorney|\blaw\b|litigation", re.I)),
    ("custodian", re.compile(r"custod", re.I)),
    ("consultant", re.compile(r"invest\w*\.? consult|consult\w* invest|pension consult|investment advisory consult", re.I)),
    ("manager", re.compile(r"invest\w*\.? (manag|mgmt|advis|services)|portfolio manag|asset manag|fund manag|money manag|"
                           r"wealth manag|outsourced chief|\bocio\b|investment managers?\b|investment fees", re.I)),
    ("bank", re.compile(r"\bbank(ing)?\b|trust services|trustee fees", re.I)),
]


def _firm_lists() -> dict[str, list[str]]:
    from radar.settings import load_yaml
    return load_yaml("ef_firms.yaml") or {}


def classify_contractor(firm: str, service: str, lists: dict[str, list[str]] | None = None) -> tuple[str, str]:
    """(class, basis): manager | consultant | custodian | bank | law_firm | other. A firm on the curated law-firm list
    is a law firm whatever its line says; otherwise the printed service decides, and the firm lists settle generic or
    missing descriptions."""
    lists = _firm_lists() if lists is None else lists
    f = (firm or "").lower()
    named = next((k for k, names in lists.items() for n in names if n in f), None)
    if named == "law_firm":
        return "law_firm", "firm"
    svc = service or ""
    for cls, pat in _SVC_RULES:
        if pat.search(svc):
            if cls == "manager" and named in ("consultant", "bank", "custodian") and \
                    not re.search(r"outsourced chief|\bocio\b", svc, re.I):
                return named, "firm (service printed as manager)"
            if cls == "custodian" and re.search(r"manag", svc, re.I):
                return "custodian", "service (custodian and manager)"
            return cls, "service"
    if named:
        return named, "firm"
    return "other", "service"
