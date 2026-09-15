"""String constants instead of DB enums.

DB-level enums make every future value a migration. These are validated in the
application layer where the error message can actually be useful.
"""
from __future__ import annotations

TRACKS = ("fte", "outstaff", "freelance", "equity")

# Application pipeline. Order matters for progress display.
STAGES = (
    "discovered",
    "scored",
    "queued",
    "drafted",
    "approved",
    "sent",
    "acknowledged",
    "screen",
    "tech",
    "final",
    "offer",
    "accepted",
)
TERMINAL_STAGES = (
    "rejected",
    "ghosted",
    "withdrawn",
    "knocked_out",
    "declined",
)
ALL_STAGES = STAGES + TERMINAL_STAGES

# Hard-gate rejection reasons. Repeated codes are what the rule miner feeds on.
GATE_CODES = (
    "GEO_FENCED",
    "TZ_MISMATCH",
    "HYBRID_ONSITE",
    "CLEARANCE",
    "EMPLOYMENT_MISMATCH",
    "SENIORITY_OUT",
    "COMP_FLOOR",
    "STACK_EXCLUDE",
    "STALE",
    "EXPIRED",
    "BLOCKLIST",
    "EVERGREEN",
    "DUPLICATE_OF",
    # user-editable filters from the UI
    "KEYWORD_EXCLUDE",
    "KEYWORD_MISSING",
    "TITLE_EXCLUDE",
    "COMPANY_EXCLUDE",
    "TOO_OLD",
    "NOT_FULL_REMOTE",
)

DRAFT_TEMPLATES = (
    "ats_cover",      # T1 full-time application
    "upwork_proposal",  # T2 freelance
    "cold_email",     # T3 contract / outstaff outreach
    "cofounder_pitch",  # T4 equity
    "referral_ask",   # A1
    "follow_up",      # A2
    "agency_overflow",  # A3
)
