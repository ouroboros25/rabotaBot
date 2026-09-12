"""Stage 0: hard gates.

Runs on every posting before any similarity or LLM call and kills 40-60% of the
corpus for free. Every rejection stores its code AND the span that triggered it,
because an auditable rejection is what lets the weekly retro propose a sharper
rule instead of a vaguer one.

GEO_FENCED is the single highest-value filter for a global-remote profile: only
about a third of "fully remote" US roles are even open to all 50 states, let
alone to another country.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from app.models import JobPosting, Profile
from app.services import geo, search_filters
from app.services.configload import load_rubric


@dataclass(frozen=True)
class GateHit:
    code: str
    pattern: str | None = None
    span: str | None = None


def _compile(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


_CACHE: dict[str, list[re.Pattern[str]]] = {}


def reset_rule_cache() -> None:
    """Drop compiled patterns so an edited rubric.yaml takes effect.

    Both this cache and ``load_rubric``'s lru_cache live for the life of the
    process, which is correct for a hot path but means a rule edit is invisible
    to a long-running API worker until something clears them. The rescan
    endpoint does exactly that, so "re-check everything" also means "re-read the
    rules" rather than silently re-applying the old ones.
    """
    _CACHE.clear()


def _rules(name: str) -> list[re.Pattern[str]]:
    if name not in _CACHE:
        cfg = load_rubric().get("gates", {}).get(name, {})
        _CACHE[name] = _compile(cfg.get("patterns", []))
    return _CACHE[name]


def _first_match(patterns: list[re.Pattern[str]], text: str) -> tuple[str, str] | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            start = max(0, m.start() - 60)
            end = min(len(text), m.end() + 60)
            return pat.pattern, text[start:end].replace("\n", " ").strip()
    return None


_SENIORITY_RANK = {
    "intern": 0, "junior": 1, "entry": 1, "associate": 2, "mid": 3, "midlevel": 3,
    "intermediate": 3, "senior": 4, "staff": 5, "lead": 5, "principal": 6,
    "director": 7, "vp": 8, "executive": 8, "cto": 8,
}

_TITLE_SENIORITY = re.compile(
    r"\b(intern|junior|jr|entry[- ]level|associate|mid[- ]level|senior|sr|staff|lead|"
    r"principal|director|head of|vp|chief)\b", re.IGNORECASE
)


def _seniority_rank(posting: JobPosting) -> int | None:
    raw = (posting.seniority or "").lower().strip()
    for word, rank in _SENIORITY_RANK.items():
        if word in raw:
            return rank
    m = _TITLE_SENIORITY.search(posting.title or "")
    if m:
        token = m.group(1).lower().replace("-", "").replace(" ", "")
        aliases = {"jr": "junior", "sr": "senior", "entrylevel": "entry",
                   "midlevel": "mid", "headof": "director", "chief": "executive"}
        token = aliases.get(token, token)
        return _SENIORITY_RANK.get(token)
    return None


def evaluate(
    posting: JobPosting, profile: Profile, variant=None, filters=None,
) -> list[GateHit]:
    """Return every gate the posting trips. Empty list means it survives.

    ``filters`` carries the user's own keyword rules from the UI. They are
    evaluated here rather than later so an excluded posting never costs an
    embedding or an LLM call.
    """
    gates_cfg = load_rubric().get("gates", {})
    hits: list[GateHit] = []
    text = f"{posting.title or ''}\n{posting.body_text or ''}"
    now = datetime.now(timezone.utc)

    # --- structured eligibility, when the source actually gave it to us ---
    # Codes are already normalised to ISO-2 at ingest time; a None verdict means
    # "unknown", which must never be treated as a rejection.
    verdict = geo.eligible(posting.countries_allowed, profile.countries_eligible)
    if verdict is False:
        hits.append(GateHit(
            "GEO_FENCED", "countries_allowed",
            ", ".join(posting.countries_allowed or [])[:200],
        ))
    elif verdict is None:
        # No structured field: boards very often encode the fence in the title
        # alone ("Senior SRE - AMER"), and a title-blind gate sends exactly those
        # to the top of the ranking.
        inferred = geo.infer_from_title(posting.title)
        if geo.eligible(inferred, profile.countries_eligible) is False:
            hits.append(GateHit(
                "GEO_FENCED", "title_region", f"{posting.title} -> {', '.join(inferred or [])}"[:200],
            ))

    if posting.remote_policy in ("hybrid", "onsite"):
        hits.append(GateHit("HYBRID_ONSITE", "remote_policy", posting.remote_policy))

    # --- regex gates over free text ---
    for code, rule_name in (
        ("GEO_FENCED", "geo_fenced"),
        ("CLEARANCE", "clearance"),
        ("HYBRID_ONSITE", "hybrid_onsite"),
        ("EMPLOYMENT_MISMATCH", "employment_mismatch"),
    ):
        if any(h.code == code for h in hits):
            continue
        found = _first_match(_rules(rule_name), text)
        if found:
            hits.append(GateHit(code, found[0], found[1]))

    # Evergreen uses two pattern sets. The title set is broad, because a label
    # there means the requisition is a pipeline. The body set is narrow: matching
    # "evergreen" or bare "pipeline" in a description would gate most data and
    # platform roles, since CI/CD and data pipelines are in nearly all of them.
    found = _first_match(_rules("evergreen_title"), posting.title or "")
    if not found:
        found = _first_match(_rules("evergreen_body"), posting.body_text or "")
    if found:
        hits.append(GateHit("EVERGREEN", found[0], found[1]))

    # --- seniority ---
    rank = _seniority_rank(posting)
    if rank is not None and rank <= 2:
        hits.append(GateHit("SENIORITY_OUT", "seniority", posting.seniority or posting.title))

    # --- compensation floor ---
    floor = profile.comp_floor_annual
    if floor and posting.comp_max:
        annual = _to_annual(posting.comp_max, posting.comp_period)
        if annual is not None and annual < floor * 0.85:  # 15% tolerance for FX/banding
            hits.append(GateHit("COMP_FLOOR", "comp_max", f"{posting.comp_max} {posting.comp_currency or ''}"))

    # --- excluded stack ---
    if variant is not None:
        for skill in variant.exclude_skills or []:
            if re.search(rf"\b{re.escape(str(skill))}\b", text, re.IGNORECASE):
                hits.append(GateHit("STACK_EXCLUDE", skill, skill))
                break

    # --- user keyword filters ---
    if filters is not None:
        hits.extend(_keyword_hits(posting, filters, text, now))

    # --- freshness / expiry ---
    stale_days = int(gates_cfg.get("stale_after_days", 45))
    published = posting.first_published_at
    if published and published < now - timedelta(days=stale_days):
        if not posting.updated_at_source or posting.updated_at_source <= published:
            hits.append(GateHit("STALE", "first_published_at", published.isoformat()))
    if posting.expires_at and posting.expires_at < now:
        hits.append(GateHit("EXPIRED", "expires_at", posting.expires_at.isoformat()))
    if posting.liveness_ok is False:
        hits.append(GateHit("EXPIRED", "liveness", "liveness check failed"))

    return hits


def _keyword_hits(posting: JobPosting, filters, text: str, now: datetime) -> list[GateHit]:
    """Apply the filters the user typed in the UI."""
    out: list[GateHit] = []
    title = posting.title or ""

    term = search_filters.first_match(filters.exclude_title, title)
    if term:
        out.append(GateHit("TITLE_EXCLUDE", term, title[:200]))

    term = search_filters.first_match(filters.exclude, text)
    if term:
        out.append(GateHit("KEYWORD_EXCLUDE", term, _context(text, term)))

    # An empty require list means "no requirement", not "require nothing", so the
    # gate only fires when the user actually asked for something.
    if filters.require_any and not search_filters.first_match(filters.require_any, text):
        out.append(GateHit(
            "KEYWORD_MISSING", "require_any",
            "none of: " + ", ".join(filters.require_any[:12]),
        ))

    company = posting.company_name or ""
    if company:
        term = next(
            (c for c in filters.exclude_companies if c in company.lower()), None
        )
        if term:
            out.append(GateHit("COMPANY_EXCLUDE", term, company[:200]))

    if filters.max_age_days and posting.first_published_at:
        published = posting.first_published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = (now - published).days
        if age_days > filters.max_age_days:
            out.append(GateHit(
                "TOO_OLD", "max_age_days", f"{age_days}d > {filters.max_age_days}d",
            ))

    return out


def _context(text: str, term: str, window: int = 60) -> str:
    lowered = text.lower()
    idx = lowered.find(term.lower())
    if idx < 0:
        return term
    start = max(0, idx - window)
    end = min(len(text), idx + len(term) + window)
    return text[start:end].replace("\n", " ").strip()


_PERIOD_MULTIPLIER = {
    "year": 1, "yearly": 1, "annual": 1, "annually": 1, None: 1, "": 1,
    "month": 12, "monthly": 12,
    "week": 52, "weekly": 52,
    "day": 220, "daily": 220,
    "hour": 1800, "hourly": 1800,
}


def _to_annual(amount: float | None, period: str | None) -> float | None:
    if amount is None:
        return None
    key = (period or "").lower().strip() or None
    mult = _PERIOD_MULTIPLIER.get(key)
    if mult is None:
        # Unknown period: infer from magnitude rather than guess a multiplier.
        return amount if amount > 5000 else amount * 1800
    return amount * mult
