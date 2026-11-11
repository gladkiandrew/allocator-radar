"""Reverse D-B21 on a copy of the store: restore person names in quotes, summaries, titles and officers.

Sources, in order: the pre-redaction snapshot (allocator-radar-B), then the scrubbed page text for rows newer
than the snapshot. Usage: python unredact.py TARGET.duckdb SNAPSHOT.duckdb
"""
import re
import sys
import uuid
import hashlib
from collections import defaultdict

import duckdb

PH = "[name]"
NAME = r"([A-Z][\w.'’\-]*(?:[ \t]+[\w.'’\-]+){0,3}?)"
target, snap = sys.argv[1], sys.argv[2]
con = duckdb.connect(target)
con.execute(f"ATTACH '{snap}' AS b (READ_ONLY)")
con.execute(f"ATTACH '{sys.argv[3]}' AS r (READ_ONLY)")  # the redacted store, before this script
stats = defaultdict(int)


def ws(s):  # escaped literal with flexible whitespace and quote/dash variants
    out = []
    for ch in s:
        if ch.isspace():
            out.append(r"\s+")
        elif ch in "'’‘":
            out.append("['’‘]")
        elif ch in '"“”':
            out.append('["“”]')
        elif ch in "-–—":
            out.append("[-–—]")
        else:
            out.append(re.escape(ch))
    return re.sub(r"(\\s\+)+", r"\\s+", "".join(out))


def fill(redacted, sources):
    """Find the redacted text in one of the source texts, with each [name] as a capture; return filled text."""
    parts = redacted.split(PH)
    pat = re.compile(NAME.join(ws(p) for p in parts))
    for src in sources:
        m = pat.search(src)
        if m:
            out = parts[0]
            for i, p in enumerate(parts[1:]):
                out += re.sub(r"\s+", " ", m.group(i + 1)) + p
            return out, [re.sub(r"\s+", " ", g) for g in m.groups()]
    return None, None


page_cache = {}


def page_text(sha, page):
    k = (sha, page)
    if k not in page_cache:
        if page is None:
            rows = con.execute("SELECT text_scrubbed FROM pages WHERE sha256 = ? ORDER BY page_no", [sha]).fetchall()
        else:
            rows = con.execute("SELECT text_scrubbed FROM pages WHERE sha256 = ? AND page_no = ?", [sha, page]).fetchall()
            if not rows:
                rows = con.execute("SELECT text_scrubbed FROM pages WHERE sha256 = ?", [sha]).fetchall()
        page_cache[k] = [r[0] for r in rows]
    return page_cache[k]


con.execute("BEGIN")

# 1. evidence quotes
unresolved_q = []
for eid, q, sha, page, bq in con.execute(
        """SELECT e.evidence_id, e.quote, e.document_sha256, e.page, o.quote FROM evidence e
           LEFT JOIN b.evidence o USING (evidence_id) WHERE e.quote LIKE '%[name]%'""").fetchall():
    if bq and PH not in bq:
        con.execute("UPDATE evidence SET quote = ? WHERE evidence_id = ?", [bq, eid]); stats["quote<-snapshot"] += 1
        continue
    new, _ = fill(q, page_text(sha, page))
    if not new:
        new, _ = fill(q, page_text(sha, None))
    if new:
        con.execute("UPDATE evidence SET quote = ? WHERE evidence_id = ?", [new, eid]); stats["quote<-page"] += 1
    else:
        unresolved_q.append((eid, q)); stats["quote unresolved"] += 1


def linked_quotes(kind, rid):
    return [r[0] for r in con.execute(
        """SELECT e.quote FROM row_evidence r JOIN evidence e USING (evidence_id)
           WHERE r.row_kind = ? AND r.row_id = ?""", [kind, rid]).fetchall()]


def linked_pages(kind, rid):
    out = []
    for sha, page in con.execute("""SELECT e.document_sha256, e.page FROM row_evidence r JOIN evidence e USING (evidence_id)
                                    WHERE r.row_kind = ? AND r.row_id = ?""", [kind, rid]).fetchall():
        out += page_text(sha, page)
    return out


def fill_short(redacted, sources):
    """For LLM-written text: fill each [name] using the words around it, found in the sources."""
    out = redacted
    while PH in out:
        i = out.index(PH)
        before = out[:i].split()[-2:]
        after = out[i + len(PH):].split()[:1]
        got = None
        for nb in (2, 1, 0):
            for na in ((1, 0) if nb else (1,)):
                ctx_b, ctx_a = " ".join(before[len(before) - nb:]) if nb else "", " ".join(after[:na]) if na else ""
                if not ctx_b and not ctx_a:
                    continue
                pat = (ws(ctx_b) + r"\s+" if ctx_b else r"\b") + NAME + (r"\s*" + ws(ctx_a) if ctx_a else r"\b")
                for s in sources:
                    m = re.search(pat, s)
                    if m and PH not in m.group(1):
                        got = re.sub(r"\s+", " ", m.group(1)); break
                if got:
                    break
            if got:
                break
        if not got:
            return None
        out = out[:i] + got + out[i + len(PH):]
    return out


# 2. signal summaries and 3. officer titles
for table, key, col, kind in [("signals", "signal_id", "summary", "signal"),
                              ("officers", "officer_id", "title_as_printed", "officer")]:
    for rid, v, bv in con.execute(f"""SELECT t.{key}, t.{col}, o.{col} FROM {table} t LEFT JOIN b.{table} o USING ({key})
                                      WHERE t.{col} LIKE '%[name]%'""").fetchall():
        if bv and PH not in bv:
            new = bv; stats[f"{table}.{col}<-snapshot"] += 1
        else:
            new = fill_short(v, linked_quotes(kind, rid) + linked_pages(kind, rid))
            stats[f"{table}.{col}<-quotes" if new else f"{table}.{col} unresolved"] += 1
        if new:
            con.execute(f"UPDATE {table} SET {col} = ? WHERE {key} = ?", [new, rid])
        else:
            print(f"UNRESOLVED {table}.{col} {rid}: {v}")

# manual fixes, read from the page text by hand
for eid, q in [("8c8b9aaa-b69d-424b-b24c-44e48f70d9cc", ["Tim Anderson"]),
               ("0d095783-7506-45dd-b94b-44168c85da70", ["Pat Bentley", "Pedro Herrera", "James Parsons"])]:
    (cur,) = con.execute("SELECT quote FROM evidence WHERE evidence_id = ?", [eid]).fetchone()
    for n in q:
        cur = cur.replace(PH, n, 1)
    con.execute("UPDATE evidence SET quote = ? WHERE evidence_id = ?", [cur, eid]); stats["quote<-manual"] += 1
con.execute("UPDATE signals SET summary = replace(summary, '[name]', 'John') WHERE signal_id = 'f1df5670-21ea-448f-b1db-439721bf1461'")
con.execute("UPDATE officers SET title_as_printed = 'Office Coordinator' WHERE officer_id IN ('534f66c8-121e-4153-a67e-acac9417d2b9','78bad29f-f4e0-4fdc-a1ae-b65d93d9ca2a') AND title_as_printed = '[name]'")
MANUAL = {  # quote -> names, for list-shaped quotes the rules cannot split
    "DISCUSSION WITH LAWRENCE WATTS OF NYHART RE FLORIDA": ["Lawrence Watts"],
    "seats currently occupied by JP Kearney of the fire department and Jason Palant of the Police Department":
        {"Trustee (fire department seat)": ["JP Kearney"], "Trustee (Police Department seat)": ["Jason Palant"]},
    "James Boyd Lynn Bernstein Jason Casanova Mark Taxis": ["James Boyd", "Lynn Bernstein", "Jason Casanova", "Mark Taxis"],
    "Clarence Graves Corey L. Taylor": ["Clarence Graves", "Corey L. Taylor"],
    "Edgard Hernandez Ronald A. Silver, Esq.": {"Pension Administrator": ["Edgard Hernandez"], "Legal Counsel": ["Ronald A. Silver"]},
    "Nelsia King Southeastern Advisory Services, Inc.": ["Nelsia King"],
    "Fortuna Levy Cavanaugh Macdonald Consulting, LLC": ["Fortuna Levy"],
    "Jennifer Borregard and Shelly Jones of Gabriel Roeder Smith": ["Jennifer Borregard", "Shelly Jones"],
}

# 4. officers: one row per person again
snap_edges = defaultdict(list)  # evidence_id -> snapshot officer rows linked to it
for oid, aid, name, title, role, as_of, eid in con.execute(
        """SELECT o.officer_id, o.allocator_id, o.name_as_printed, o.title_as_printed, o.role_type, o.as_of, r.evidence_id
           FROM b.officers o JOIN b.row_evidence r ON r.row_kind = 'officer' AND r.row_id = o.officer_id""").fetchall():
    snap_edges[eid].append((oid, aid, name, title, role, as_of))


CRED = re.compile(r"(?i)(CFP|CFA|CVA|CPA|EA|ESQ|AIF|CIMA|CAIA|JD|PHD|MBA|ASA|FSA|MAAA|JR|SR)")
FIRM = re.compile(r"\b(inc|llc|lp|llp|cpa'?s|associates?|assoc|group|partners|services|advisors?|&|pa)\b", re.I)


def bare_name(quote, title):
    """A quote that is a name with a title or firm beside it: the segment that looks like a person's name."""
    for seg in re.split(r"\s*[;–—(]\s*|\s+-\s*|,\s*", quote):
        seg = seg.strip(" .,-")
        w = seg.split()
        if 2 <= len(w) <= 4 and not FIRM.search(seg) and not CRED.fullmatch(seg.replace(".", "")) and seg.lower() not in title.lower() \
                and all(x[0].isupper() for x in w if x[0].isalpha()) and not re.search(r"\d", seg):
            return seg.title() if seg.isupper() else seg
    return None


def key(aid, name, title, d):  # extractor._dedupe_key on main
    return hashlib.sha256("|".join(str(p).lower() for p in (aid, name, title, d)).encode()).hexdigest()[:24]


new_rows = {}  # dedupe key -> (officer_id, aid, name, title, role, as_of)
new_edges = set()
unnamed = []
role_rows = con.execute("SELECT officer_id, allocator_id, title_as_printed, role_type, as_of FROM officers").fetchall()
for oid, aid, title, role, as_of in role_rows:
    edges = con.execute("""SELECT r.evidence_id, e.quote FROM row_evidence r JOIN evidence e USING (evidence_id)
                           WHERE r.row_kind = 'officer' AND r.row_id = ?""", [oid]).fetchall()
    for eid, quote in edges:
        hits = [s for s in snap_edges.get(eid, []) if s[1] == aid and s[3].lower() == title.lower() and s[4] == role
                and s[5] == as_of]
        if hits:
            people = [(s[0], s[2], s[3]) for s in hits]; stats["officer edge<-snapshot"] += 1
        else:
            # newer than the snapshot: the name the redactor removed from this officer's quote, else the quote's name
            red = con.execute("SELECT quote FROM r.evidence WHERE evidence_id = ?", [eid]).fetchone()[0]
            name = None
            names = fill(red, [quote])[1] if PH in red else None
            if names:
                people = [(None, n.strip(" ,.;"), title) for n in names]; stats["officer edge<-redaction slot"] += 1
                for soid, nm, t in people:
                    k = key(aid, nm, t, as_of)
                    if k not in new_rows:
                        new_rows[k] = (str(uuid.uuid4()), aid, nm, t, role, as_of)
                    new_edges.add((new_rows[k][0], eid))
                continue
            m = re.search(ws(title) + r"[\s,:\-–—]+" + NAME + r"\b", quote) or \
                re.search(NAME + r"[\s,:\-–—]+(?:the\s+)?" + ws(title) + r"\b", quote)
            if m:
                name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.")
                if CRED.fullmatch(name.replace(".", "")):
                    name = None
            if not name:
                name = bare_name(quote, title)
            man = MANUAL.get(quote.strip())
            if man is not None:
                man = man.get(title, []) if isinstance(man, dict) else man
                for nm in man:
                    t = "Gabriel Roeder Smith" if title == "[name]" else title
                    k = key(aid, nm, t, as_of)
                    if k not in new_rows:
                        new_rows[k] = (str(uuid.uuid4()), aid, nm, t, role, as_of)
                    new_edges.add((new_rows[k][0], eid))
                stats["officer edge<-manual"] += 1
                continue
            if not name:
                unnamed.append((oid, title, quote)); stats["officer edge unresolved"] += 1
                continue
            people = [(None, name, title)]; stats["officer edge<-quote"] += 1
        for soid, name, t in people:
            k = key(aid, name, t, as_of)
            if k not in new_rows:
                new_rows[k] = (soid or str(uuid.uuid4()), aid, name, t, role, as_of)
            new_edges.add((new_rows[k][0], eid))

con.execute("DELETE FROM row_evidence WHERE row_kind = 'officer'")
con.execute("DROP TABLE officers")
con.execute("""CREATE TABLE officers (officer_id VARCHAR PRIMARY KEY, allocator_id VARCHAR NOT NULL,
               name_as_printed VARCHAR NOT NULL, title_as_printed VARCHAR NOT NULL, role_type VARCHAR NOT NULL,
               as_of DATE NOT NULL, dedupe_key VARCHAR)""")
for k, (oid, aid, name, title, role, as_of) in new_rows.items():
    con.execute("""INSERT INTO officers (officer_id, allocator_id, name_as_printed, title_as_printed, role_type, as_of, dedupe_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""", [oid, aid, name[:120], title[:160], role, as_of, k])
con.executemany("INSERT INTO row_evidence VALUES ('officer', ?, ?) ON CONFLICT DO NOTHING", list(new_edges))
stats["officers rows"] = len(new_rows)
con.execute("COMMIT")

for k, v in sorted(stats.items()):
    print(f"{k}: {v}")
for oid, t, q in unnamed[:15]:
    print("UNNAMED officer", t, "|", q[:120])
for eid, q in unresolved_q[:10]:
    print("UNRESOLVED quote", q[:120])

# post-fix applied by hand on 2026-11-11: the one officer whose title had been redacted to [name]
# UPDATE officers SET title_as_printed='Gabriel Roeder Smith' WHERE title_as_printed='[name]'; then recompute its dedupe_key
