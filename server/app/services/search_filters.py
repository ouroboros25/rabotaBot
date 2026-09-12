"""User-editable search filters.

Everything here is a knob the user turns in the UI, stored as one JSON row in
``setting``. It deliberately sits apart from ``config/rubric.yaml``: the rubric
holds the scoring method, which changes rarely and belongs in git, while these
are the day-to-day "stop showing me WordPress" decisions that should not require
a redeploy.

The filters run inside stage 0, so an excluded posting costs no embedding and no
LLM call, and every rejection carries a reason code like every other gate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

from sqlalchemy.orm import Session

from app.models import Setting

SETTING_KEY = "search_filters"

# A single boost term is worth this much on the 0..1 fast score, capped in total
# so a long boost list cannot drown out fit and freshness.
BOOST_PER_TERM = 0.06
BOOST_CAP = 0.24


@dataclass
class SearchFilters:
    # Posting must contain at least one of these. Empty = no requirement.
    require_any: list[str] = field(default_factory=list)
    # Posting is rejected if any of these appears anywhere in title or body.
    exclude: list[str] = field(default_factory=list)
    # Rejected if the TITLE contains any of these. Separate from `exclude`
    # because "manager" in a body is normal and in a title is disqualifying.
    exclude_title: list[str] = field(default_factory=list)
    # Raise the score when present. Not a filter: a nudge.
    boost: list[str] = field(default_factory=list)
    # Company names to never show, matched as substrings, case-insensitive.
    exclude_companies: list[str] = field(default_factory=list)
    # Source keys to ignore without disabling the source itself.
    exclude_sources: list[str] = field(default_factory=list)
    # Hide anything published longer ago than this. 0 = no limit.
    max_age_days: int = 0
    # Hide anything scoring below this in the queue and the digest.
    min_priority: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean_terms(values: Any, limit: int = 80) -> list[str]:
    """Normalise a user-entered term list: trimmed, deduped, lowercase, bounded."""
    if not isinstance(values, (list, tuple)):
        return []
    seen: dict[str, None] = {}
    for raw in values:
        term = str(raw).strip().lower()
        if term and len(term) <= 80:
            seen.setdefault(term, None)
    return list(seen)[:limit]


def load(db: Session) -> SearchFilters:
    row = db.get(Setting, SETTING_KEY)
    if row is None or not isinstance(row.value, dict):
        return SearchFilters()
    data = {k: v for k, v in row.value.items() if k != "_previous"}
    return SearchFilters(
        require_any=_clean_terms(data.get("require_any")),
        exclude=_clean_terms(data.get("exclude")),
        exclude_title=_clean_terms(data.get("exclude_title")),
        boost=_clean_terms(data.get("boost")),
        exclude_companies=_clean_terms(data.get("exclude_companies")),
        exclude_sources=_clean_terms(data.get("exclude_sources")),
        max_age_days=max(0, int(data.get("max_age_days") or 0)),
        min_priority=max(0.0, float(data.get("min_priority") or 0.0)),
    )


def normalize(filters: SearchFilters) -> SearchFilters:
    """Apply the same cleaning on the way in as on the way out.

    Without this the stored row can differ from what the pipeline reads back,
    and the UI shows one thing while the gates apply another.
    """
    return SearchFilters(
        require_any=_clean_terms(filters.require_any),
        exclude=_clean_terms(filters.exclude),
        exclude_title=_clean_terms(filters.exclude_title),
        boost=_clean_terms(filters.boost),
        exclude_companies=_clean_terms(filters.exclude_companies),
        exclude_sources=_clean_terms(filters.exclude_sources),
        max_age_days=max(0, int(filters.max_age_days or 0)),
        min_priority=max(0.0, float(filters.min_priority or 0.0)),
    )


def save(db: Session, filters: SearchFilters) -> SearchFilters:
    """Store the filters, keeping the value they replaced.

    The previous value is kept in the same row under ``_previous``. It costs
    nothing and makes an accidental overwrite recoverable, which is not
    hypothetical: a filter set typed by hand was overwritten during testing and
    there was no way to get it back.
    """
    cleaned = normalize(filters)
    payload = cleaned.as_dict()
    row = db.get(Setting, SETTING_KEY)
    if row is None:
        db.add(Setting(key=SETTING_KEY, value=payload))
    else:
        previous = {k: v for k, v in (row.value or {}).items() if k != "_previous"}
        if previous and previous != payload:
            payload["_previous"] = previous
        row.value = payload
    db.flush()
    return cleaned


def previous(db: Session) -> dict | None:
    """The filter set that the current one replaced, if any."""
    row = db.get(Setting, SETTING_KEY)
    if row is None or not isinstance(row.value, dict):
        return None
    return row.value.get("_previous")


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def _pattern(term: str) -> re.Pattern[str]:
    """Word-boundary match that still works for .NET, C#, CI/CD and node.js.

    A plain \\b fails on a term ending in punctuation, so the boundary is only
    applied on the side where the term actually starts or ends with a word
    character.
    """
    escaped = re.escape(term)
    left = r"(?<![\w])" if term[:1].isalnum() else ""
    right = r"(?![\w])" if term[-1:].isalnum() else ""
    return re.compile(left + escaped + right, re.IGNORECASE)


_CACHE: dict[str, re.Pattern[str]] = {}


def matches(term: str, text: str) -> bool:
    if term not in _CACHE:
        _CACHE[term] = _pattern(term)
    return bool(_CACHE[term].search(text))


def first_match(terms: list[str], text: str) -> str | None:
    for term in terms:
        if matches(term, text):
            return term
    return None


def boost_score(filters: SearchFilters, text: str) -> tuple[float, list[str]]:
    """Bounded bonus for boost terms actually present in the posting."""
    if not filters.boost:
        return 0.0, []
    hits = [term for term in filters.boost if matches(term, text)]
    return min(BOOST_CAP, BOOST_PER_TERM * len(hits)), hits
