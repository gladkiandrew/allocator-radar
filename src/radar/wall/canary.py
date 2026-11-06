"""The canary: a synthetic board packet full of FAKE contact blocks.

Every value is fake (example.invalid, 555-01xx numbers, a street that does not exist).
The nightly wall run pushes it through ingest + parse and requires that none of these
tokens survive into any table or vector payload. Built with a tiny hand-rolled PDF
writer so production needs no extra dependency.
"""

from __future__ import annotations

CANARY_LINES = [
    "CITY OF CANARY POLICE & FIRE PENSION FUND - BOARD OF TRUSTEES",
    "Regular Meeting Agenda - January 15, 2026",
    "Contact: Jane Canary, Plan Administrator",
    "Email: canary.trustee@example.invalid   Phone: 305-555-0177",
    "Mailing: 4471 Canary Lane, Suite 9, Miami, FL 33131",
    "Remit to PO Box 7731, Canary Springs",
    "Consultant rep: (954) 555-0142 ext. 12, canary-ops@example.invalid",
    "Fax 561.555.0199",
    "Profiles: https://www.linkedin.com/in/canary-trustee and x.com/canarytrustee",
    "",
    "4. NEW BUSINESS",
    "a. The Board voted to issue a Request for Proposals for a hedge fund of funds manager.",
    "b. The consultant presented the target allocation study; motion carried unanimously.",
]


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def canary_pdf() -> bytes:
    lines = ["BT", "/F1 10 Tf", "50 760 Td", "14 TL"]
    for ln in CANARY_LINES:
        lines.append(f"({_esc(ln)}) Tj T*")
    lines.append("ET")
    stream = "\n".join(lines).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
