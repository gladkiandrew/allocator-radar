"""THE WALL, extraction layer: the scrubber.

Every text payload passes through scrub() before it reaches an LLM prompt, the vector
store, or any table. Contact details are replaced with [REDACTED-CONTACT].

Two passes:
  1. Regex (always on): emails, phone/fax numbers, street addresses, PO boxes,
     state+ZIP tails, person social-profile URLs, "Contact:" blocks, mailto links.
  2. GLiNER PII model (flag_gliner, on by default when installed): catches address and
     contact spans the regex misses. Names of people are NOT redacted: officers are
     recorded as printed, role-only (AGENDA §1.1).

detect() runs the regex pass without replacing and is what tests/wall sweeps with.
"""

from __future__ import annotations

import functools
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

REDACTED = "[REDACTED-CONTACT]"

_STREET_SUFFIX = (
    r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Way|Court|Ct|"
    r"Place|Pl|Terrace|Ter|Parkway|Pkwy|Highway|Hwy|Circle|Cir|Trail|Trl|Plaza|Plz|"
    r"Square|Sq|Causeway|Cswy)"
)

PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+\-]+\s?(?:@|\(at\)|\[at\])\s?[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "mailto": re.compile(r"mailto:\S+", re.I),
    "phone": re.compile(
        r"(?<![\d$.,])(?:\+?1[\s.\-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.\-])\d{3}[\s.\-]\d{4}"
        r"(?:\s?(?:x|ext\.?|extension)\s?\d{1,5})?(?![\d,])",
        re.I,
    ),
    "phone_label": re.compile(
        r"\b(?:tel|telephone|phone|ph|fax|facsimile|cell|mobile)\b\.?\s*[:#]?\s*[\d()+][\d()\s.\-]{6,}\d",
        re.I,
    ),
    "street": re.compile(
        r"\b\d{1,6}(?:\s+[NSEW]\.?|\s+(?:North|South|East|West|NE|NW|SE|SW))?"
        r"(?:\s+[A-Z0-9][A-Za-z0-9'\.\-]*){1,5}\s+" + _STREET_SUFFIX + r"\b\.?"
        r"(?:,?\s*(?:Suite|Ste\.?|Unit|Floor|Fl\.?|#)\s*[A-Za-z0-9\-]+)?",
    ),
    "po_box": re.compile(r"\bP\.?\s?O\.?\s?Box\s+\d+\b", re.I),
    "state_zip": re.compile(r"\b(?:FL|Florida|NY|New York)\.?,?\s+\d{5}(?:-\d{4})?\b"),
    "social_url": re.compile(
        r"(?:https?://)?(?:www\.)?(?:linkedin\.com/in/|x\.com/|twitter\.com/|instagram\.com/|facebook\.com/)[^\s)>\]]*",
        re.I,
    ),
    "contact_block": re.compile(r"(?im)^[ \t]*(?:contact|contact person|contact info(?:rmation)?)\s*:.*$"),
}


@dataclass(frozen=True)
class Hit:
    kind: str
    start: int
    end: int
    text: str


def detect(text: str) -> list[Hit]:
    """Regex detector. Returns every contact-shaped span. Used by tests/wall."""
    hits: list[Hit] = []
    for kind, pat in PATTERNS.items():
        for m in pat.finditer(text):
            if m.group(0).strip() == REDACTED:
                continue
            hits.append(Hit(kind, m.start(), m.end(), m.group(0)))
    return hits


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _apply(text: str, spans: list[tuple[int, int]]) -> str:
    parts, last = [], 0
    for s, e in _merge(spans):
        parts.append(text[last:s])
        parts.append(REDACTED)
        last = e
    parts.append(text[last:])
    return "".join(parts)


_GLINER_LABELS = ["email", "phone number", "fax number", "street address", "postal address"]


@functools.lru_cache(maxsize=1)
def _gliner():
    try:
        from gliner import GLiNER  # type: ignore[import-not-found]
    except ImportError:
        log.warning("GLiNER not installed; scrubber runs regex only")
        return None
    return GLiNER.from_pretrained("urchade/gliner_multi_pii-v1")  # CPU: batched it matches the Apple GPU, and is thread-safe


# GLiNER only looks at windows with a contact cue; the regex pass has already covered all text.
# A contact detail cannot appear without one of these (an @, a street/suite word, a contact label, or a phone shape).
CONTACT_CUE = re.compile(
    r"(@|\(at\)|\b(suite|ste|street|avenue|ave|blvd|boulevard|road|drive|lane|parkway|highway|court|floor|"
    r"p\.?\s?o\.?\s?box|tel|telephone|phone|fax|email|e-mail|address|contact|mailing|mail to|zip)\b|"
    r"\d{3}[\s.)\-]+\d{3}[\s.\-]\d{4}|\b\d{1,6}\s+[NSEW]\.?\s+\w+|\bFL\s+\d{5})", re.I)
_GLINER_LOCK = __import__("threading").Lock()


def _gliner_spans(text: str) -> list[tuple[int, int]]:
    model = _gliner()
    if model is None or not text.strip():
        return []
    # Windows stay under GLiNER's 384-token limit and overlap so no span is cut in half.
    window, step = 450, 380
    offs = [o for o in range(0, max(len(text), 1), step) if CONTACT_CUE.search(text[o:o + window])]
    if not offs:
        return []
    chunks = [text[o:o + window] for o in offs]
    with _GLINER_LOCK:
        results = model.inference(chunks, _GLINER_LABELS, threshold=0.5, batch_size=16)
    spans = []
    for o, ents in zip(offs, results):
        spans += [(o + e["start"], o + e["end"]) for e in ents]
    return spans


@dataclass(frozen=True)
class ScrubResult:
    text: str
    redactions: int


def scrub(text: str, *, use_gliner: bool | None = None) -> ScrubResult:
    if not text:
        return ScrubResult(text or "", 0)
    if use_gliner is None:
        from radar.settings import get_settings

        use_gliner = get_settings().flag_gliner
    spans = [(h.start, h.end) for h in detect(text)]
    if use_gliner:
        spans += _gliner_spans(text)
    merged = _merge(spans)
    out = _apply(text, merged)
    # Second regex pass over the output: replacements can expose new adjacent matches.
    again = [(h.start, h.end) for h in detect(out)]
    if again:
        out = _apply(out, again)
        merged += again
    return ScrubResult(out, len(merged))


def scrub_text(text: str, **kw) -> str:
    return scrub(text, **kw).text
