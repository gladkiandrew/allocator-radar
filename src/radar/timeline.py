"""'When' for every allocator: what is happening, how far it has got, and the next dated step (read-only).

Facts come from cited signals and plan facts; anything computed (stage when DATA has not recorded one, tier expiry)
is labeled computed by the template. Missing dates are None and render as "not published".

DATA's R10 columns are used when present: signals.stage, signals.next_step, signals.next_step_date,
signals.mandate_size_usd. Until then the stage comes from the signal type and the next step from a scheduled
search date or the plan's next published meeting.
"""

from __future__ import annotations

from datetime import date, timedelta

STAGES = [  # (key, label, signal types that reach it); keys match DATA's timeline_searches stage columns (D-B37)
    ("opened", "Search opened", {"SEARCH_OPENED", "SEARCH_SCHEDULED", "RFP_ISSUED"}),
    ("finalists", "Finalists named", {"FINALISTS_NAMED"}),
    ("interviews", "Interviews", set()),
    ("fee_contract", "Fees / contract", set()),
    ("awarded", "Awarded", {"MANDATE_AWARDED"}),
    ("funded", "Funded", {"MANDATE_FUNDED"}),
]
STAGE_LABEL = {k: label for k, label, _ in STAGES}
DEPLOYMENT_TYPES = ("MANDATE_AWARDED", "MANDATE_FUNDED", "COMMITMENT_MADE")
COUNTING = ("approved", "auto_approved")


def stage_of(s: dict) -> str | None:
    if s.get("stage") in STAGE_LABEL:
        return s["stage"]
    if s.get("signal_type") in ("SEARCH_SCHEDULED", "SEARCH_OPENED") and "interview" in (s.get("summary") or "").lower():
        return "interviews"
    for key, _, types in STAGES:
        if s.get("signal_type") in types:
            return key
    return None


def _window(tier: str, cfg: dict) -> int | None:
    for t in cfg["tiers"]:
        if t["tier"] == tier:
            return t["window_days"]
    return None


def when(a: dict, signals: list[dict], today: date | None = None, cfg: dict | None = None) -> dict:
    """a: an allocator_table / allocator_brief row (readiness, next_meeting, upcoming_doc_date).
    signals: the plan's signals (already verified-only in public builds)."""
    from radar.settings import readiness_config

    today = today or date.today()
    cfg = cfg or readiness_config()
    r = a["readiness"]
    live = [s for s in signals if s.get("review_status") in COUNTING]
    lead = r.reasons[0] if r.reasons else None

    # next published meeting: a cited NEXT_MEETING fact, else an agenda already posted for a future date
    nm = a.get("next_meeting")
    nm = nm.get("as_of") if isinstance(nm, dict) else nm
    next_meeting = {"date": nm, "basis": "published meeting date"} if nm else (
        {"date": a.get("upcoming_doc_date"), "basis": "agenda posted"} if a.get("upcoming_doc_date") else None)

    # last deployment: newest award, funding or commitment on file
    from radar.web.present import strategy_label

    deps = sorted((s for s in live if s["signal_type"] in DEPLOYMENT_TYPES
                   and strategy_label(s)["text"] not in ("service provider", "total plan")),  # capital, not an auditor
                  key=lambda s: s["event_date"], reverse=True)
    last = deps[0] if deps else None

    # next step: DATA's stated next step, else a scheduled search date ahead, else the next published meeting
    next_step = None
    if lead is not None:
        if lead.get("next_step"):
            next_step = {"text": lead["next_step"], "date": lead.get("next_step_date"), "stated": True, "signal": lead}
        elif lead["signal_type"] == "SEARCH_SCHEDULED" and lead["event_date"] >= today:
            next_step = {"text": "scheduled on the agenda", "date": lead["event_date"], "stated": True, "signal": lead}
        elif next_meeting:
            next_step = {"text": "next board meeting", "date": next_meeting["date"], "stated": False, "signal": None}

    # stage tracker for the lead search: stages reached by signals in the tier window with the same strategy
    tracker = []
    if lead is not None and r.tier == "DEPLOYING":
        lab = strategy_label(lead)["text"]
        since = today - timedelta(days=365)
        same = [s for s in live if s["event_date"] >= since and stage_of(s) and strategy_label(s)["text"] == lab]
        for key, label, _ in STAGES:
            hits = sorted((s for s in same if stage_of(s) == key), key=lambda s: s["event_date"])
            tracker.append({"key": key, "label": label, "reached": bool(hits),
                            "date": hits[-1]["event_date"] if hits else None, "signal": hits[-1] if hits else None})

    # DATA's timeline tables (D-B37) win where they have a value: they read the documents across all signals.
    by_id = {x["signal_id"]: x for x in signals if x.get("signal_id")}
    tl, searches = a.get("tl"), a.get("tl_searches") or []
    if tl:
        if tl.get("next_meeting_date"):
            next_meeting = {"date": tl["next_meeting_date"], "basis": tl.get("next_meeting_source") or "published"}
        if tl.get("last_deploy_date") and by_id.get(tl.get("last_deploy_signal_id")):
            sig = by_id[tl["last_deploy_signal_id"]]
            last = {**sig, "event_date": tl["last_deploy_date"], "manager_name": tl.get("last_deploy_firm") or sig.get("manager_name"),
                    "amount_usd": tl.get("last_deploy_amount") if tl.get("last_deploy_amount") is not None else sig.get("amount_usd"),
                    "bucket": tl.get("last_deploy_asset_class") or sig.get("bucket")}
    search = None
    if lead is not None and r.tier == "DEPLOYING" and searches:
        open_ = [x for x in searches if x.get("status") == "open"] or searches
        lead_ids = {x["signal_id"] for x in r.reasons}
        search = next((x for x in open_ if lead_ids & set((x.get("signal_ids") or "").split(","))), None) or \
            next((x for x in open_ if x.get("asset_class") == lead.get("bucket")), None)
    if search:
        tracker = []
        for key, label, _ in STAGES:
            d = search.get(f"{key}_date")
            tracker.append({"key": key, "label": label, "reached": d is not None, "date": d, "signal": None})
        if search.get("next_step_text"):
            next_step = {"text": search["next_step_text"], "date": search.get("next_step_date"), "stated": True,
                         "signal": by_id.get(search.get("next_step_signal_id"))}

    expiry = None
    win = _window(r.tier, cfg) if r.reasons else None
    if win is not None:
        expiry = max(s["event_date"] for s in r.reasons) + timedelta(days=win)

    size = None
    if lead is not None:
        size = lead.get("mandate_size_usd") or (lead.get("amount_usd") if lead["signal_type"] in
                                                ("MANDATE_AWARDED", "MANDATE_FUNDED", "COMMITMENT_MADE",
                                                 "TARGET_ALLOCATION_CHANGED") else None)
    if tl and tl.get("tier_expires") and r.tier == tl.get("tier"):
        expiry = tl["tier_expires"]
    stage = (search or {}).get("stage_reached") if search else (stage_of(lead) if lead is not None else None)
    return {
        "tier": r.tier,
        "search": search,
        "lead": lead,
        "stage": stage,
        "stage_label": STAGE_LABEL.get(stage, "early hint" if stage == "hint" else None),
        "stage_computed": not search,
        "next_step": next_step,
        "next_meeting": next_meeting,
        "last_deployment": last,
        "mandate_size": size,
        "tier_expiry": expiry,
        "tracker": tracker,
    }
