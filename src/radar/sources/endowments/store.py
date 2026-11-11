"""The E&F store: its own DuckDB file (data/ef.duckdb), so Phase C never contends with the pension
pipeline for the main database's write lock and never changes the pension schema.

THE WALL: no column may match config/wall.yaml banned_column_pattern (email, phone, address ...).
Names stored: organizations', firms', and officers', directors' and trustees' names with their titles as printed
(D-B28). Never stored: anyone's compensation, hours, benefits, address or contact details.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

import duckdb

from radar.settings import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS ef_orgs (
    ein VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,              -- UNIVERSITY_FOUNDATION | PRIVATE_UNIVERSITY | COMMUNITY_FOUNDATION | PRIVATE_FOUNDATION
    city VARCHAR,
    ntee VARCHAR,
    foundation_code VARCHAR,
    bmf_assets DOUBLE,                  -- IRS EO BMF ASSET_AMT (latest return on file)
    bmf_tax_period VARCHAR,
    rank_in_kind INTEGER,
    home_url VARCHAR,                   -- the organization's own site, when known
    loaded_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ef_pp_filings (   -- ProPublica Nonprofit Explorer API (IRS SOI extracts)
    ein VARCHAR, tax_prd VARCHAR, tax_year INTEGER, form VARCHAR,
    total_assets DOUBLE, total_liabilities DOUBLE, net_assets DOUBLE, total_revenue DOUBLE,
    PRIMARY KEY (ein, tax_prd, form)
);
CREATE TABLE IF NOT EXISTS ef_filings (      -- parsed IRS e-file XML (raw XML is never stored)
    object_id VARCHAR PRIMARY KEY, ein VARCHAR, return_type VARCHAR, tax_year INTEGER, tax_period_end DATE,
    total_assets DOUBLE, investments_public DOUBLE, investments_other DOUBLE, contractors_over_100k INTEGER,
    index_year INTEGER, batch VARCHAR, fetched_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ef_contractors (  -- firm names only (Part VII Sec. B / 990-PF contractors)
    object_id VARCHAR, ein VARCHAR, tax_year INTEGER, firm VARCHAR, service VARCHAR, amount DOUBLE, investment BOOLEAN
);
CREATE TABLE IF NOT EXISTS ef_other_investments (
    object_id VARCHAR, ein VARCHAR, tax_year INTEGER, description VARCHAR, book_value DOUBLE, fmv DOUBLE
);
CREATE TABLE IF NOT EXISTS ef_documents (    -- committee/board documents the organization publishes itself
    sha256 VARCHAR, ein VARCHAR, url VARCHAR, title VARCHAR, doc_date DATE, doc_type VARCHAR,
    page_count INTEGER, fetched_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (sha256, ein)
);
CREATE TABLE IF NOT EXISTS ef_signals (
    signal_id VARCHAR PRIMARY KEY,
    ein VARCHAR NOT NULL,
    signal_type VARCHAR NOT NULL,
    qualifier VARCHAR,
    event_date DATE NOT NULL,
    date_basis VARCHAR,                 -- tax_period_end (990) | meeting_date / document_date (committee)
    summary VARCHAR NOT NULL,
    source_type VARCHAR NOT NULL,       -- '990' | 'committee'
    source_url VARCHAR NOT NULL,
    doc_title VARCHAR,
    cite VARCHAR NOT NULL,              -- 990: form, tax year, part/line. committee: document and page
    page INTEGER,
    quote VARCHAR,
    hf_relevant BOOLEAN DEFAULT false,
    review_status VARCHAR NOT NULL DEFAULT 'new',
    reviewed_by VARCHAR,
    created_at TIMESTAMPTZ DEFAULT now()
);
"""


MIGRATIONS = [
    # D-B28: re-read filings
    "ALTER TABLE ef_filings ADD COLUMN IF NOT EXISTS endowment_eoy DOUBLE",       # Sch. D Part V line 1g (a)
    "ALTER TABLE ef_filings ADD COLUMN IF NOT EXISTS endowment_boy DOUBLE",       # Sch. D Part V line 1a (a)
    "ALTER TABLE ef_filings ADD COLUMN IF NOT EXISTS investments_program DOUBLE", # 990 Part X line 13
    "ALTER TABLE ef_filings ADD COLUMN IF NOT EXISTS investments_land DOUBLE",    # 990-PF Part II line 11
    "ALTER TABLE ef_filings ADD COLUMN IF NOT EXISTS investments_mortgage DOUBLE",# 990-PF Part II line 12
    "ALTER TABLE ef_filings ADD COLUMN IF NOT EXISTS officers_read BOOLEAN DEFAULT false",
    "ALTER TABLE ef_contractors ADD COLUMN IF NOT EXISTS contractor_class VARCHAR",  # manager|consultant|custodian|bank|law_firm|other
    "ALTER TABLE ef_contractors ADD COLUMN IF NOT EXISTS class_basis VARCHAR",
    "ALTER TABLE ef_other_investments ADD COLUMN IF NOT EXISTS value_status VARCHAR",
    "ALTER TABLE ef_other_investments ADD COLUMN IF NOT EXISTS valuation VARCHAR",   # F/C (Sch. D VII) or AT COST/FMV (990-PF)
    "ALTER TABLE ef_filings ADD COLUMN IF NOT EXISTS investments_other_book DOUBLE",  # 990-PF Part II line 13 book value  # stated | not_stated (placeholder 0/$1)
    """CREATE TABLE IF NOT EXISTS ef_officers (     -- names and titles as printed; nothing else about the person
        object_id VARCHAR, ein VARCHAR, tax_year INTEGER, name_as_printed VARCHAR, title_as_printed VARCHAR, role VARCHAR,
        cite VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS ef_exposure (     -- hedge fund / alternatives share of investments, per filing
        object_id VARCHAR PRIMARY KEY, ein VARCHAR, tax_year INTEGER, form VARCHAR,
        investments_total DOUBLE, hf_usd DOUBLE, alt_usd DOUBLE, hf_pct DOUBLE, alt_pct DOUBLE,
        hf_status VARCHAR, alt_status VARCHAR,       -- computed | not_disclosed
        basis VARCHAR, cite VARCHAR, cite_total VARCHAR, n_lines INTEGER)""",
    "ALTER TABLE ef_orgs ADD COLUMN IF NOT EXISTS signal_note VARCHAR",  # why there is no change signal, when there is none
]


def path() -> Path:
    return get_settings().data_dir / "ef.duckdb"


_LOCK = threading.RLock()  # the loader writes from two threads; one connection at a time


@contextmanager
def connect(read_only: bool = False):
    with _LOCK, _connect(read_only) as con:
        yield con


@contextmanager
def _connect(read_only: bool):
    p = path()
    if read_only and not p.exists():
        con = duckdb.connect(":memory:")
        con.execute(SCHEMA)
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(str(p), read_only=read_only)
    try:
        yield con
    finally:
        con.close()


def init() -> None:
    with connect() as con:
        con.execute(SCHEMA)
        for m in MIGRATIONS:
            con.execute(m)


def rows(con, sql: str, params=()) -> list[dict]:
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
