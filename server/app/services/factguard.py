"""Deterministic post-generation checks on a draft.

This is a correctness control, not a style control. Every AI resume tool on the
market has been caught inventing job titles and numbers, and the candidate
personally carries the misrepresentation risk. So the model is allowed to select,
order and rephrase facts from the ledger, and nothing else, and that constraint
is verified by code rather than requested in a prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.services.configload import load_rubric

_NUMBER = re.compile(r"\b\d[\d.,]*\s*(?:%|k|m|bn|x|ms|s|gb|tb)?\b", re.IGNORECASE)
_PROPER = re.compile(r"\b[A-Z][A-Za-z0-9.+#/-]{2,}\b")

# Words that start a sentence or are ordinary English, not entity claims.
_PROPER_ALLOW = frozenset("""
I We Our The This That These Those A An And Or But If Then When While For With From
To In On At By As Is Are Was Were Be Been Being Have Has Had Do Does Did Will Would
Can Could Should May Might Must Shall You Your They Their It Its He She Them Us Me My
Hi Hello Dear Best Regards Thanks Thank Sincerely Yours Available Happy Glad Sure
Senior Staff Principal Lead Head Engineer Developer Architect Manager Director
Monday Tuesday Wednesday Thursday Friday Saturday Sunday
January February March April May June July August September October November December
""".split())


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: dict = field(default_factory=dict)


def _ledger_corpus(facts) -> str:
    """Text of the ledger, WITHOUT the fact keys.

    Keys like "F014" are identifiers, not evidence. Including them let "14" and
    "4" count as sourced numbers, which is how a fabricated headcount slipped
    past this check on the first live draft.
    """
    parts = []
    for f in facts:
        parts.append(str(f.payload))
        if f.verifiable_by:
            parts.append(str(f.verifiable_by))
        if getattr(f, "evidence_url", None):
            parts.append(str(f.evidence_url))
    return " ".join(parts)


def _number_tokens(text: str) -> set[str]:
    """Exact set of number tokens, normalised to digits.

    Set membership, not substring search: concatenating every digit in the ledger
    into one string makes "10" match inside "...0 1 0...", which is a silent
    false negative on exactly the claims this check exists to catch.
    """
    return {
        digits
        for m in _NUMBER.finditer(text)
        if (digits := re.sub(r"[^\d]", "", m.group(0)))
    }


def fact_guard(body: str, facts, jd_text: str, context: str = "") -> CheckResult:
    """Numbers must come from the ledger. Entities may also come from the posting.

    The asymmetry is deliberate and was added after a live run: the model wrote
    "guiding 5-10 engineers", which passed because "10" happened to appear
    somewhere in the job description. A number in a cover letter is a claim about
    the candidate, so quoting one out of the employer's own text proves nothing.
    Naming the employer's stack, on the other hand, is exactly what a tailored
    letter should do, so entities stay allowed from both sources.
    """
    ledger = _ledger_corpus(facts).lower()
    ledger_numbers = _number_tokens(ledger)
    # ``context`` carries the posting title and company name: they are things
    # the letter is supposed to say, and they are not always repeated inside
    # the description body.
    entity_corpus = " ".join([ledger, (jd_text or "").lower(), context.lower()])

    bad_numbers = []
    for m in _NUMBER.finditer(body):
        digits = re.sub(r"[^\d]", "", m.group(0))
        if digits and digits not in ledger_numbers:
            bad_numbers.append(m.group(0).strip())

    bad_entities = []
    for m in _PROPER.finditer(checked := body):
        word = m.group(0)
        if word in _PROPER_ALLOW or word.lower() in entity_corpus:
            continue
        # "Azure-native" and "Kubernetes-based" are grammatical forms of a
        # grounded term, not new entities. Accept a compound whose parts are all
        # either grounded or ordinary words.
        parts = [p for p in re.split(r"[-/]", word) if p]
        if len(parts) > 1 and all(
            p.lower() in entity_corpus or p in _PROPER_ALLOW or p.islower()
            for p in parts
        ):
            continue
        bad_entities.append(word)

    passed = not bad_numbers and not bad_entities
    return CheckResult(
        "factguard", passed,
        {"unverified_numbers": sorted(set(bad_numbers))[:10],
         "unverified_entities": sorted(set(bad_entities))[:10]},
    )


def banlist(body: str) -> CheckResult:
    """Catch the phrases a third of hiring managers use to spot AI in 20 seconds."""
    patterns = load_rubric().get("banlist", [])
    hits = [p for p in patterns if re.search(p, body, re.IGNORECASE)]
    return CheckResult("banlist", not hits, {"hits": hits})


def length_cap(body: str, template: str) -> CheckResult:
    limits = load_rubric().get("draft_limits", {})
    cap = {
        "cold_email": limits.get("cold_email_words", 80),
        "upwork_proposal": limits.get("upwork_proposal_words", 150),
        "ats_cover": limits.get("ats_cover_words", 250),
        "cofounder_pitch": limits.get("cofounder_pitch_words", 180),
        "referral_ask": 120,
        "follow_up": 90,
        "agency_overflow": 120,
    }.get(template, 250)
    words = len(body.split())
    return CheckResult("length", words <= cap, {"words": words, "cap": cap})


def specificity(body: str, facts) -> CheckResult:
    """At least one named system, and a number when the ledger can source one.

    The number requirement is conditional on purpose. fact_guard forbids any
    digit that is not in the ledger, so demanding one unconditionally makes a
    ledger without metrics unsatisfiable: the draft would fail forever, flipping
    between "no number" and "invented number". When the ledger has no numbers the
    honest outcome is a vaguer letter plus a visible prompt to add metrics.
    """
    ledger = _ledger_corpus(facts).lower()
    ledger_has_numbers = bool(_number_tokens(ledger))
    has_number = bool(re.search(r"\d", body))
    named = re.findall(r"\b[A-Za-z][A-Za-z0-9.+#/-]*\b", body)
    has_named = any(w.lower() in ledger and len(w) > 2 for w in named)

    passed = has_named and (has_number or not ledger_has_numbers)
    detail = {
        "has_number": has_number,
        "has_named_system": has_named,
        "ledger_has_numbers": ledger_has_numbers,
    }
    if not ledger_has_numbers:
        detail["hint"] = (
            "the fact ledger contains no measurable outcomes, so drafts cannot "
            "cite one; add a metric fact to get concrete letters"
        )
    return CheckResult("specificity", passed, detail)


def jd_hook(body: str, jd_text: str) -> CheckResult:
    """At least one concrete detail lifted from THIS posting."""
    if not jd_text:
        return CheckResult("jd_hook", True, {"skipped": "no jd text"})
    jd_tokens = {
        w.lower() for w in re.findall(r"\b[A-Za-z][A-Za-z0-9.+#/-]{3,}\b", jd_text)
    }
    body_tokens = {
        w.lower() for w in re.findall(r"\b[A-Za-z][A-Za-z0-9.+#/-]{3,}\b", body)
    }
    # Ignore the generic overlap; we want distinctive terms.
    generic = {"team", "role", "work", "remote", "engineer", "software", "product",
               "experience", "company", "developer", "years", "building", "systems"}
    shared = (jd_tokens & body_tokens) - generic
    return CheckResult("jd_hook", len(shared) >= 1, {"shared": sorted(shared)[:10]})


def novelty(body: str, previous_bodies: list[str]) -> CheckResult:
    """Not a near-copy of the last N approved drafts.

    Identical-template detection is an explicit enforcement signal on some
    platforms, and it is also just bad writing.
    """
    threshold = float(load_rubric().get("draft_limits", {}).get("novelty_max_similarity", 0.85))
    worst = 0.0
    for prev in previous_bodies:
        worst = max(worst, fuzz.token_set_ratio(body, prev) / 100.0)
    return CheckResult("novelty", worst < threshold,
                       {"max_similarity": round(worst, 3), "threshold": threshold})


# Ledger keys are metadata. Seeing one in the prose means the model treated the
# scaffolding as content, which reads as a machine artefact to any human.
_LEDGER_KEY = re.compile(r"\bF\d{3}\b")


def no_ledger_keys(body: str, subject: str = "") -> CheckResult:
    hits = sorted(set(_LEDGER_KEY.findall(f"{subject}\n{body}")))
    return CheckResult("no_ledger_keys", not hits, {"hits": hits})


def run_all(
    *, body: str, template: str, facts, jd_text: str, previous_bodies: list[str],
    subject: str = "", context: str = "",
) -> list[CheckResult]:
    # The subject is verified with the body: a claim is a claim wherever it sits.
    checked_text = f"{subject}\n{body}" if subject else body
    return [
        fact_guard(checked_text, facts, jd_text, context=context),
        no_ledger_keys(body, subject),
        banlist(body),
        length_cap(body, template),
        specificity(body, facts),
        jd_hook(body, jd_text),
        novelty(body, previous_bodies),
    ]
