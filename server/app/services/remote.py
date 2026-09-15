"""Full-remote classification.

Promoted to a first-class gate because "remote" on a job board means four
different things: genuinely location-independent, remote inside one country,
hybrid with an office requirement, and onsite with a remote-sounding title.

The default stance is strict: a posting counts as full remote only on positive
evidence. Silence is treated as onsite, because most postings that never mention
remote are onsite, and the cost of a false positive here is a wasted application
while the cost of a false negative is one missed listing out of thousands.

Geography is handled separately by the geo gate. A role that is fully remote but
US-only is full remote AND geo-fenced; both statements are true and they gate for
different reasons.
"""
from __future__ import annotations

import re

# Unambiguous statements of location independence.
_STRONG_REMOTE = [
    r"\b100%\s*remote\b",
    r"\bfully[- ]remote\b",
    r"\bremote[- ]first\b",
    r"\bremote[- ]only\b",
    r"\bwork from anywhere\b",
    r"\banywhere in the world\b",
    r"\bfrom anywhere\b",
    r"\blocation[- ]independent\b",
    r"\bglobally distributed\b",
    r"\bfully distributed\b",
    r"\bdistributed[- ]first\b",
    r"\bwork remotely from\b",
    r"\bremote \(worldwide\)\b",
    r"\bworldwide remote\b",
]

# "Remote" said plainly. Counts only when nothing contradicts it.
_WEAK_REMOTE = [
    r"\bremote\b",
    r"\btelecommut\w*\b",
    r"\bwork from home\b",
    r"\bhome[- ]based\b",
    r"\bwfh\b",
]

# Statements that an office is part of the job.
_ONSITE = [
    r"\bhybrid\b",
    r"\b\d+\s*(?:\+\s*)?days?\s*(?:a|per)?\s*week\s*(?:in|at|from)\s*(?:the\s*)?office\b",
    r"\b\d+\s*days?\s*(?:in[- ]office|onsite|on[- ]site)\b",
    r"\bin[- ]office\b",
    r"\bon[- ]?site\b(?!\s*(?:visits?|travel|interview))",
    r"\bin our [a-z .'-]{2,30} office\b",
    r"\bbased in (?:our )?[a-z .'-]{2,30} office\b",
    r"\brelocation (?:is )?(?:required|assistance|package)\b",
    r"\bwilling to relocate\b",
    r"\bcommut\w+\b",
    r"\battend the office\b",
    r"\boffice[- ]based\b",
    r"\bcome into the office\b",
    r"\b(?:must|expected to) be (?:in|at) the office\b",
]

# Location values that assert remoteness on their own.
_REMOTE_LOCATION = re.compile(
    r"^\s*(?:remote|anywhere|worldwide|global|distributed|remote\s*[-–—/(].*)\s*$",
    re.IGNORECASE,
)

_STRONG_RE = [re.compile(p, re.IGNORECASE) for p in _STRONG_REMOTE]
_WEAK_RE = [re.compile(p, re.IGNORECASE) for p in _WEAK_REMOTE]
_ONSITE_RE = [re.compile(p, re.IGNORECASE) for p in _ONSITE]

GLOBAL = "global"
HYBRID = "hybrid"
ONSITE = "onsite"
UNKNOWN = "unknown"


def _first(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return None


def classify(
    *,
    title: str | None = None,
    body: str | None = None,
    location: str | None = None,
    declared: str | None = None,
) -> tuple[str, str | None]:
    """Return (policy, evidence).

    ``declared`` is a structured field from the source (Ashby ``isRemote``,
    4dayweek ``work_arrangement``). It is trusted over prose when it is explicit,
    because a structured field is the employer's own answer rather than our
    reading of their marketing copy.
    """
    title = title or ""
    location = location or ""
    # Only the first part of the body: benefits sections at the bottom say
    # "remote work stipend" on plenty of onsite jobs.
    body = (body or "")[:6000]
    haystack = f"{title}\n{location}\n{body}"

    declared_norm = (declared or "").strip().lower()
    if declared_norm in ("hybrid",):
        return HYBRID, "source field: hybrid"
    if declared_norm in ("onsite", "on-site", "in office", "in_office"):
        return ONSITE, "source field: onsite"

    onsite_hit = _first(_ONSITE_RE, haystack)
    strong_hit = _first(_STRONG_RE, haystack)

    # An explicit full-remote statement beats an office mention: "fully remote,
    # optional access to our Berlin office" is a remote job.
    if strong_hit:
        return GLOBAL, strong_hit
    if onsite_hit:
        return HYBRID if re.search(r"hybrid", onsite_hit, re.I) else ONSITE, onsite_hit

    # Boards that only list remote work (RemoteOK, We Work Remotely, Himalayas)
    # assert remoteness for every row, and the connectors record that. Trusted
    # here, but only after the prose checks above: a remote board can still carry
    # a listing whose own description says "hybrid", and the description wins.
    if declared_norm in (
        "remote", "fully remote", "remote_first", "global", "worldwide",
        "anywhere", "true", "yes",
    ):
        return GLOBAL, f"source says: {declared_norm}"

    if location and _REMOTE_LOCATION.match(location):
        return GLOBAL, f"location: {location.strip()[:80]}"

    weak_hit = _first(_WEAK_RE, f"{title}\n{location}")
    if weak_hit:
        return GLOBAL, f"title/location: {weak_hit}"

    weak_body = _first(_WEAK_RE, body[:2500])
    if weak_body:
        # "Remote" appearing only in the description is weaker evidence, but it
        # is still the employer saying the word about this job.
        return GLOBAL, f"description: {weak_body}"

    return UNKNOWN, None


def is_full_remote(policy: str | None) -> bool:
    return policy == GLOBAL
