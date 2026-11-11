"""The Phase C universe: the largest Florida endowments and foundations, chosen by rule from the IRS
Exempt Organizations Business Master File (eo_fl.csv) and saved with sources to data/ef_universe.csv.

Selection (assets = BMF ASSET_AMT, the latest return the IRS has on file):
  UNIVERSITY_FOUNDATION  name says university/college + foundation/investment/endowment (NTEE B*, T3* or blank)
  PRIVATE_UNIVERSITY     education NTEE (B*), foundation code 11 (school), a university or college itself
  COMMUNITY_FOUNDATION   NTEE T31, or "community foundation" in the name, or a listed override (COMMUNITY_OVERRIDES)
  PRIVATE_FOUNDATION     foundation code 04 (private non-operating foundation)
Each kind is ranked by assets and capped (CAPS). The BMF's street, in-care-of and ZIP columns are dropped at load.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

BMF_URL = "https://www.irs.gov/pub/irs-soi/eo_fl.csv"
BMF_SOURCE = "IRS Exempt Organizations Business Master File, eo_fl.csv"
PP_ORG = "https://projects.propublica.org/nonprofits/organizations/{ein}"
CAPS = {"UNIVERSITY_FOUNDATION": 12, "PRIVATE_UNIVERSITY": 6, "COMMUNITY_FOUNDATION": 10, "PRIVATE_FOUNDATION": 20}
KIND_LABEL = {"UNIVERSITY_FOUNDATION": "University foundation", "PRIVATE_UNIVERSITY": "Private university",
              "COMMUNITY_FOUNDATION": "Community foundation", "PRIVATE_FOUNDATION": "Private foundation"}
KEEP = ("EIN", "NAME", "CITY", "STATE", "SUBSECTION", "FOUNDATION", "ASSET_AMT", "NTEE_CD", "TAX_PERIOD", "RULING")

UNI = re.compile(r"\b(UNIVERSITY|COLLEGE|INSTITUTE OF TECHNOLOGY)\b")
UNI_FUND = re.compile(r"\b(FOUNDATION|INVESTMENT|ENDOWMENT)\b")
UNI_EXCLUDE = re.compile(r"\b(ATHLETIC|BOOSTER|RESEARCH|ALUMNI|HOUSING|STUDENT|HEALTH|HOSPITAL|MEDICAL|PRESS|BOOKSTORE|"
                         r"FACULTY|CHURCH|SORORITY|FRATERNITY|PARENT|CLUB|MINISTR|CHAPEL|BAPTIST|ACADEMY|PREPARATORY|"
                         r"HIGH SCHOOL|SCHOOL BOARD|DISTRICT|TRANSFER|CREDIT UNION|LAB)\b")


def load_bmf(text: str) -> list[dict]:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        row = {k: (r.get(k) or "").strip() for k in KEEP}  # street / ICO / ZIP never leave this function
        if row["STATE"] != "FL" or row["SUBSECTION"] != "03":
            continue
        try:
            row["ASSET_AMT"] = float(row["ASSET_AMT"] or 0)
        except ValueError:
            row["ASSET_AMT"] = 0.0
        out.append(row)
    return out


# Community foundations the IRS codes as general public foundations (T30) rather than T31. Each one is a
# community foundation by its own description; listed here so the rule stays visible.
COMMUNITY_OVERRIDES = {"650350357": "The Miami Foundation (NTEE T30Z)"}


def classify(r: dict) -> str | None:
    name, ntee, fc = r["NAME"].upper(), r["NTEE_CD"].upper(), r["FOUNDATION"]
    if r["EIN"] in COMMUNITY_OVERRIDES:
        return "COMMUNITY_FOUNDATION"
    if UNI.search(name) and not UNI_EXCLUDE.search(name) and "COMMUNITY FOUNDATION" not in name:
        if UNI_FUND.search(name) and (ntee.startswith(("B", "T3")) or not ntee):  # a college foundation may be coded T31
            return "UNIVERSITY_FOUNDATION"
        if fc == "11" and ntee.startswith("B"):
            return "PRIVATE_UNIVERSITY"
    if ntee.startswith("T31") or "COMMUNITY FOUNDATION" in name:
        return "COMMUNITY_FOUNDATION"
    if fc == "04":
        return "PRIVATE_FOUNDATION"
    return None


def select(bmf: list[dict]) -> list[dict]:
    by_kind: dict[str, list[dict]] = {k: [] for k in CAPS}
    for r in bmf:
        k = classify(r)
        if k:
            by_kind[k].append({**r, "kind": k})
    picked = []
    for k, rows in by_kind.items():
        rows.sort(key=lambda r: -r["ASSET_AMT"])
        for i, r in enumerate(rows[:CAPS[k]], 1):
            picked.append({**r, "rank_in_kind": i})
    return picked


def write_csv(picked: list[dict], path: Path, bmf_date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ein", "name", "kind", "rank_in_kind", "city", "ntee", "foundation_code", "bmf_total_assets_usd",
                    "bmf_tax_period", "source_universe", "source_filings"])
        for r in sorted(picked, key=lambda r: (list(CAPS).index(r["kind"]), r["rank_in_kind"])):
            w.writerow([r["EIN"], r["NAME"], r["kind"], r["rank_in_kind"], r["CITY"].title(), r["NTEE_CD"], r["FOUNDATION"],
                        int(r["ASSET_AMT"]), r["TAX_PERIOD"], f"{BMF_SOURCE} ({BMF_URL}, file dated {bmf_date})",
                        PP_ORG.format(ein=int(r["EIN"]))])
