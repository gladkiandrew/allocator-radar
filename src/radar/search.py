"""Retrieval: LanceDB hybrid search (BM25 full-text + dense vectors, fused with RRF) over SCRUBBED pages.

Payloads are scrubbed page text only (it comes from the `pages` table, which is written after the
scrubber). The wall sweeps every payload here (radar.wall.live.check_lance).
Embeddings: a small local model (BAAI/bge-small-en-v1.5), downloaded once; no API calls.
"""

from __future__ import annotations

import functools
import logging

from radar import db
from radar.settings import get_settings

log = logging.getLogger(__name__)
TABLE = "pages"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"


def _db():
    import lancedb

    path = get_settings().data_dir / "lance"
    path.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(path))


@functools.lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBED_MODEL, device="cpu")


def _embed(texts: list[str]) -> list[list[float]]:
    return _model().encode([t[:2000] for t in texts], normalize_embeddings=True, batch_size=32).tolist()


def index_new(limit: int = 5000) -> int:
    with db.connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS search_indexed (sha256 VARCHAR, page_no INTEGER, PRIMARY KEY (sha256, page_no))")
        rows = db.rows(con, """SELECT p.sha256, p.page_no, p.text_scrubbed AS text, du.allocator_id,
                                      any_value(du.title) AS title, any_value(du.doc_date) AS doc_date
                               FROM pages p JOIN document_urls du USING (sha256)
                               WHERE length(p.text_scrubbed) > 40
                                 AND NOT EXISTS (SELECT 1 FROM search_indexed s WHERE s.sha256 = p.sha256 AND s.page_no = p.page_no)
                               GROUP BY p.sha256, p.page_no, p.text_scrubbed, du.allocator_id
                               LIMIT ?""", [limit])
    if not rows:
        return 0
    vecs = _embed([r["text"] for r in rows])
    data = [{"sha256": r["sha256"], "page_no": r["page_no"], "allocator_id": r["allocator_id"],
             "title": r["title"] or "", "doc_date": str(r["doc_date"] or ""), "text": r["text"], "vector": v}
            for r, v in zip(rows, vecs)]
    ldb = _db()
    if TABLE in ldb.table_names():
        t = ldb.open_table(TABLE)
        t.add(data)
    else:
        t = ldb.create_table(TABLE, data=data)
    t.create_fts_index("text", replace=True)
    with db.connect() as con:
        con.executemany("INSERT INTO search_indexed VALUES (?, ?) ON CONFLICT DO NOTHING",
                        list({(r["sha256"], r["page_no"]) for r in rows}))
    return len(rows)


def search(q: str, allocator_id: str | None = None, k: int = 10) -> list[dict]:
    ldb = _db()
    if TABLE not in ldb.table_names() or not q.strip():
        return []
    t = ldb.open_table(TABLE)
    where = f"allocator_id = '{allocator_id}'" if allocator_id and allocator_id.replace("-", "").isalnum() else None
    cols = ["sha256", "page_no", "allocator_id", "title", "doc_date", "text"]

    def run(builder):
        if where:
            builder = builder.where(where, prefilter=True)
        return builder.select(cols).limit(k * 3).to_list()

    try:
        fts = run(t.search(q, query_type="fts"))
    except Exception as e:  # malformed FTS query syntax: fall back to vector only
        log.info("fts failed: %s", e)
        fts = []
    vec = run(t.search(_embed([q])[0], query_type="vector"))
    scores: dict[tuple, float] = {}
    keep: dict[tuple, dict] = {}
    for ranked in (fts, vec):  # reciprocal rank fusion
        for i, r in enumerate(ranked):
            key = (r["sha256"], r["page_no"], r["allocator_id"])
            scores[key] = scores.get(key, 0.0) + 1.0 / (60 + i)
            keep[key] = r
    top = sorted(scores, key=scores.get, reverse=True)[:k]
    out = []
    for key in top:
        r = keep[key]
        text = r["text"]
        pos = text.lower().find(q.lower().split()[0]) if q.split() else -1
        start = max(0, pos - 120) if pos >= 0 else 0
        out.append({**{c: r[c] for c in cols if c != "text"}, "snippet": text[start:start + 320]})
    return out


def all_payloads():
    ldb = _db()
    if TABLE not in ldb.table_names():
        return
    tbl = ldb.open_table(TABLE).to_arrow().select(["text", "title"])
    for batch in tbl.to_batches(max_chunksize=1000):
        for row in batch.to_pylist():
            yield row["text"]
            yield row["title"]
