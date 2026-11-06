"""Relational store. DuckDB by default; SQL kept to the portable subset (Postgres-ready).

DuckDB allows one read-write process per file, so every caller opens a short-lived
connection via connect() and closes it. The crawler holds no connection while it
waits on the network. connect() retries while another process holds the lock.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Iterator
from typing import Any

import duckdb

from radar.settings import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS allocators (
    allocator_id VARCHAR PRIMARY KEY,
    legal_name VARCHAR NOT NULL,
    display_name VARCHAR NOT NULL,
    allocator_type VARCHAR NOT NULL,
    jurisdiction_city VARCHAR,
    jurisdiction_state VARCHAR,
    jurisdiction_country VARCHAR NOT NULL DEFAULT 'US',
    fiscal_year_end_month INTEGER,
    home_url VARCHAR,
    template VARCHAR,
    access VARCHAR NOT NULL,              -- AUTO | MANUAL
    access_reason VARCHAR,
    source_id VARCHAR,
    registry_version VARCHAR,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS allocator_pages (  -- document-listing pages to crawl
    allocator_id VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    label VARCHAR,
    PRIMARY KEY (allocator_id, url)
);

CREATE TABLE IF NOT EXISTS registry_approvals (
    region VARCHAR NOT NULL,
    registry_version VARCHAR NOT NULL,     -- sha256 of the registry file
    approved_by VARCHAR NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL,
    note VARCHAR,
    PRIMARY KEY (region, registry_version)
);

CREATE TABLE IF NOT EXISTS documents (
    sha256 VARCHAR PRIMARY KEY,
    content_type VARCHAR,
    n_bytes BIGINT,
    page_count INTEGER,
    provenance VARCHAR NOT NULL,           -- CRAWL | MANUAL | RECORDS_REQUEST
    first_seen TIMESTAMPTZ NOT NULL,
    parsed_at TIMESTAMPTZ,
    parse_method VARCHAR,
    parse_error VARCHAR
);

CREATE TABLE IF NOT EXISTS document_urls (
    sha256 VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    allocator_id VARCHAR NOT NULL,
    title VARCHAR,
    doc_type VARCHAR,                      -- AGENDA | MINUTES | PERFORMANCE | IPS | ACTUARIAL | FINANCIAL | RFP | DISCLOSURE_112664 | OTHER
    doc_date DATE,                         -- source-native date parsed from title/filename
    listed_on VARCHAR,                     -- page where the link was found
    first_seen TIMESTAMPTZ NOT NULL,
    last_fetched TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (sha256, url, allocator_id)
);

CREATE TABLE IF NOT EXISTS pages (         -- scrubbed text only
    sha256 VARCHAR NOT NULL,
    page_no INTEGER NOT NULL,
    text_scrubbed VARCHAR NOT NULL,
    redactions INTEGER NOT NULL,
    prefilter_terms VARCHAR,               -- JSON list of matched vocabulary
    PRIMARY KEY (sha256, page_no)
);

CREATE TABLE IF NOT EXISTS egress_log (    -- every outbound HTTP request
    ts TIMESTAMPTZ NOT NULL,
    method VARCHAR NOT NULL,
    url VARCHAR NOT NULL,
    host VARCHAR NOT NULL,
    status INTEGER,
    n_bytes BIGINT,
    body_sha256 VARCHAR,
    robots_verdict VARCHAR,
    purpose VARCHAR,
    error VARCHAR
);

CREATE TABLE IF NOT EXISTS audit_log (     -- append-only
    ts TIMESTAMPTZ NOT NULL,
    actor VARCHAR NOT NULL,
    action VARCHAR NOT NULL,
    subject VARCHAR,
    inputs_hash VARCHAR,
    outputs_hash VARCHAR,
    model VARCHAR,
    duration_ms INTEGER,
    status VARCHAR,
    detail VARCHAR
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id VARCHAR PRIMARY KEY,
    document_sha256 VARCHAR,
    source_url VARCHAR NOT NULL,
    page INTEGER,
    quote VARCHAR NOT NULL,
    extractor VARCHAR NOT NULL,
    extractor_version VARCHAR NOT NULL,
    prompt_hash VARCHAR,
    extracted_at TIMESTAMPTZ NOT NULL,
    confidence DOUBLE NOT NULL,
    provenance VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id VARCHAR PRIMARY KEY,
    allocator_id VARCHAR NOT NULL,
    signal_type VARCHAR NOT NULL,
    qualifier VARCHAR,
    event_date DATE NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    summary VARCHAR NOT NULL,
    review_status VARCHAR NOT NULL,        -- new | approved | rejected
    origin VARCHAR NOT NULL,               -- MACHINE | MANUAL
    dedupe_key VARCHAR,
    reviewed_by VARCHAR,
    reviewed_at TIMESTAMPTZ,
    review_note VARCHAR
);

CREATE TABLE IF NOT EXISTS mandates (
    mandate_id VARCHAR PRIMARY KEY,
    allocator_id VARCHAR NOT NULL,
    manager_name_as_disclosed VARCHAR NOT NULL,
    bucket_label_as_disclosed VARCHAR NOT NULL,
    bucket VARCHAR NOT NULL,
    amount_usd_as_disclosed DOUBLE,
    amount_kind VARCHAR,
    as_of DATE NOT NULL,
    status VARCHAR NOT NULL,
    fee_as_disclosed VARCHAR,
    dedupe_key VARCHAR
);

CREATE TABLE IF NOT EXISTS officers (      -- person-as-role ONLY
    officer_id VARCHAR PRIMARY KEY,
    allocator_id VARCHAR NOT NULL,
    name_as_printed VARCHAR NOT NULL,
    title_as_printed VARCHAR NOT NULL,
    role_type VARCHAR NOT NULL,
    as_of DATE NOT NULL,
    dedupe_key VARCHAR
);

CREATE TABLE IF NOT EXISTS consultant_engagements (
    engagement_id VARCHAR PRIMARY KEY,
    allocator_id VARCHAR NOT NULL,
    consultant_name_as_disclosed VARCHAR NOT NULL,
    as_of DATE NOT NULL,
    dedupe_key VARCHAR
);

CREATE TABLE IF NOT EXISTS row_evidence (  -- provenance edges: any row -> evidence
    row_kind VARCHAR NOT NULL,             -- signal | mandate | officer | engagement
    row_id VARCHAR NOT NULL,
    evidence_id VARCHAR NOT NULL,
    PRIMARY KEY (row_kind, row_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS extraction_runs (
    sha256 VARCHAR NOT NULL,
    page_no INTEGER NOT NULL,
    extractor_version VARCHAR NOT NULL,
    run_at TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL,
    PRIMARY KEY (sha256, page_no, extractor_version)
);

CREATE TABLE IF NOT EXISTS merge_queue (   -- Touchpoint 2
    merge_id VARCHAR PRIMARY KEY,
    entity_kind VARCHAR NOT NULL,
    left_name VARCHAR NOT NULL,
    right_name VARCHAR NOT NULL,
    score DOUBLE NOT NULL,
    context VARCHAR,
    status VARCHAR NOT NULL,               -- open | merged | distinct
    decided_by VARCHAR,
    decided_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS records_requests (  -- Touchpoint 5 (tracking only; system never sends)
    request_id VARCHAR PRIMARY KEY,
    allocator_id VARCHAR NOT NULL,
    request_text VARCHAR NOT NULL,
    date_filed DATE NOT NULL,
    status VARCHAR NOT NULL,               -- filed | acknowledged | fulfilled | denied | withdrawn
    response_folder VARCHAR NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    note VARCHAR
);

CREATE TABLE IF NOT EXISTS export_log (
    export_id VARCHAR PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    actor VARCHAR NOT NULL,
    n_rows INTEGER NOT NULL,
    sha256 VARCHAR NOT NULL,
    filters VARCHAR
);
"""


def _open(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    s = get_settings()
    s.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(s.duckdb_path), read_only=read_only)


@contextlib.contextmanager
def connect(*, timeout_s: float = 60.0) -> Iterator[duckdb.DuckDBPyConnection]:
    deadline = time.monotonic() + timeout_s
    delay = 0.05
    while True:
        try:
            con = _open()
            break
        except duckdb.IOException as e:  # lock held by another process
            if "lock" not in str(e).lower() or time.monotonic() > deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.0)
    try:
        yield con
    finally:
        con.close()


def init_db() -> None:
    with connect() as con:
        con.execute(SCHEMA)


def rows(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | tuple = ()) -> list[dict]:
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def audit(
    con: duckdb.DuckDBPyConnection,
    actor: str,
    action: str,
    subject: str | None = None,
    *,
    status: str = "ok",
    detail: Any = None,
    model: str | None = None,
    inputs_hash: str | None = None,
    outputs_hash: str | None = None,
    duration_ms: int | None = None,
) -> None:
    con.execute(
        "INSERT INTO audit_log VALUES (now(), ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            actor,
            action,
            subject,
            inputs_hash,
            outputs_hash,
            model,
            duration_ms,
            status,
            json.dumps(detail, default=str) if detail is not None else None,
        ],
    )
