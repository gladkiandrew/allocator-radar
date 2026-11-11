"""The E&F store: its own DuckDB file (data/ef.duckdb), so Phase C never contends with the pension
pipeline for the main database's write lock and never changes the pension schema.

THE WALL: no column may match config/wall.yaml banned_column_pattern (email, phone, address ...),
and the only names stored are organizations' and firms'.
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


def rows(con, sql: str, params=()) -> list[dict]:
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
