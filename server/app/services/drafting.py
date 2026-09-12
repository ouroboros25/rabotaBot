"""Draft generation, grounded in the fact ledger and verified after the fact.

The contract with the model is narrow on purpose: it receives the ledger and may
only select, order and rephrase. Anything it emits then goes through
``factguard.run_all``. A failed draft is regenerated once with the violations fed
back, and a second failure escalates to the human with the offending spans
highlighted. A failed draft is never silently published.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.config import settings
from app.services import factguard
from app.services.configload import load_rubric
from app.services.llm import LLMUnavailable, chat_json, wrap_untrusted

logger = logging.getLogger(__name__)


@dataclass
class DraftResult:
    body: str
    subject: str | None
    paragraphs: list[dict]
    fact_keys: list[str]
    jd_hooks: list[str]
    open_questions: list[str]
    model: str | None
    checks: list[factguard.CheckResult]
    attempts: int

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


# Per-template instructions. Each is a different product, not the same letter
# with a swapped noun.
TEMPLATE_BRIEFS: dict[str, str] = {
    "ats_cover": (
        "Write a cover letter for a full-time remote application.\n"
        "Structure: (1) one sentence naming the exact posting title verbatim plus "
        "the single strongest match, carrying a number from the ledger; "
        "(2) two claim-then-evidence paragraphs answering the posting's stated "
        "must-haves in the posting's own vocabulary; "
        "(3) one short paragraph on the remote operating model, timezone overlap "
        "and contracting structure; "
        "(4) one line stating availability. No pleasantries, no sign-off essay.\n"
        "The eligibility paragraph is not filler: a non-local candidate applying to "
        "a remote role now meets an identity-verification regime built to catch "
        "offshore fraud. State verifiable identity facts plainly and move on."
    ),
    "upwork_proposal": (
        "Write a freelance proposal.\n"
        "Structure: (1) restate their problem in one sentence using their nouns; "
        "(2) the closest analogous thing actually shipped, with a number and a link "
        "if the ledger has one; (3) one concrete first step for week one; "
        "(4) one clarifying question that proves the brief was read; "
        "(5) rate and availability. No greeting block, no CV recital."
    ),
    "cold_email": (
        "Write a cold email to a hiring manager or founder about contract work.\n"
        "Structure: (1) the trigger, named and specific (what they posted, what they "
        "shipped, what they announced); (2) the inference about their problem; "
        "(3) one proof with a number; (4) one low-friction ask. "
        "No signature essay. Short beats polished."
    ),
    "cofounder_pitch": (
        "Write a message to a non-technical founder looking for a technical co-founder.\n"
        "This is the one channel where the candidate is scarce rather than abundant, "
        "so do not sell, qualify.\n"
        "Structure: (1) what has actually been built and shipped, not what is wanted; "
        "(2) the domain wedge already understood; (3) the honest time commitment; "
        "(4) one thing wanted from a co-founder; (5) links."
    ),
    "referral_ask": (
        "Write a short message asking a specific contact for a referral.\n"
        "Name the role, say why it fits in one line with one number, make the ask "
        "trivially easy to act on, and give them a clean exit if it is awkward."
    ),
    "follow_up": (
        "Write a follow-up on an application sent a few days ago.\n"
        "Frame it as a reply in the same thread, not a formal new letter. "
        "Add one piece of new information, do not merely 'check in'."
    ),
    "agency_overflow": (
        "Write to a small software agency that is advertising for this exact stack, "
        "pitching availability as overflow capacity on their existing client work. "
        "Be concrete about rate, notice period and how you invoice."
    ),
}

SYSTEM = """You draft job-search correspondence for one specific candidate.

ABSOLUTE CONSTRAINTS:
- You may only SELECT, ORDER and REPHRASE facts from the FACT LEDGER given below,
  plus details quoted from the job posting.
- You may NOT introduce any claim, number, percentage, date, company name,
  product name or technology that is not in the ledger or the posting.
- EVERY DIGIT you write must come from the FACT LEDGER. Not from the posting,
  not from plausible inference. If the ledger has no number for a point, make
  the point without a number. "Led the migration" is fine; "led 5-10 engineers"
  is a fabrication unless the ledger says so.
- NEVER state a duration you computed yourself. The ledger gives start dates,
  not totals. Write "at Varyence since 2021-03", never "with 2 years of
  experience" - that is arithmetic you were not asked to do and it is usually
  wrong.
- You may state relevance and capability. You may NOT assert emotional states.
  Never write that the candidate is excited, thrilled, passionate or delighted.
- Write like a competent engineer emailing another engineer. Plain sentences.
  No marketing register, no triads of adjectives, no "I would love the opportunity".

Answer ONLY with JSON of exactly this shape:
{"subject": str|null,
 "paragraphs": [{"text": str, "fact_keys": [str]}],
 "jd_hooks": [str],
 "open_questions": [str]}

Every paragraph must cite at least one fact_key from the ledger, except a closing
availability line which may cite none.

THE FACT KEYS ARE METADATA. They go in the "fact_keys" array and NOWHERE else.
Never write "F001", "as stated in F010" or any ledger key inside "text" or
"subject" - the reader has never seen your ledger and it reads as a machine
artefact. Say "at Varyence", not "as stated in F001".

The subject line is held to the same evidence rules as the body: no number in it
unless the ledger has that number."""


def _ledger_json(facts) -> str:
    return json.dumps(
        [
            {"key": f.key, "type": f.type, "payload": f.payload,
             "verifiable_by": f.verifiable_by}
            for f in facts if f.active
        ],
        ensure_ascii=False, sort_keys=True,
    )


def _build_user_prompt(
    *, template: str, posting, profile, variant, facts, judge_verdict: dict | None,
    violations: list[str] | None = None,
) -> str:
    limits = load_rubric().get("draft_limits", {})
    cap = {
        "cold_email": limits.get("cold_email_words", 80),
        "upwork_proposal": limits.get("upwork_proposal_words", 150),
        "ats_cover": limits.get("ats_cover_words", 250),
        "cofounder_pitch": limits.get("cofounder_pitch_words", 180),
    }.get(template, 200)

    hooks = (judge_verdict or {}).get("tailoring_hooks") or []
    unknowns = (judge_verdict or {}).get("unknowns") or []

    parts = [
        f"TASK: {TEMPLATE_BRIEFS.get(template, TEMPLATE_BRIEFS['ats_cover'])}",
        f"HARD LIMIT: {cap} words total across all paragraphs.",
        f"CANDIDATE POSITIONING: {variant.headline}",
        f"FACT LEDGER (the only source of claims):\n{_ledger_json(facts)}",
        f"ROLE: {posting.title} at {posting.company_name or 'the company'}",
    ]
    if hooks:
        parts.append("SPECIFIC DETAILS FROM THIS POSTING WORTH REFERENCING: "
                     + "; ".join(str(h) for h in hooks[:6]))
    if unknowns:
        parts.append("WHAT THE POSTING DOES NOT SAY (turn one of these into a "
                     "genuine question): " + "; ".join(str(u) for u in unknowns[:4]))
    parts.append(wrap_untrusted(posting.body_text or "", "posting", str(posting.id)))
    if violations:
        parts.append(
            "YOUR PREVIOUS ATTEMPT FAILED THESE AUTOMATED CHECKS. Fix every one:\n- "
            + "\n- ".join(violations)
        )
    return "\n\n".join(parts)


def generate(
    *, template: str, posting, profile, variant, facts,
    judge_verdict: dict | None = None, previous_bodies: list[str] | None = None,
) -> DraftResult | None:
    """Generate, verify, retry once on failure. Returns None if the gateway is down."""
    previous_bodies = previous_bodies or []
    violations: list[str] | None = None
    last: DraftResult | None = None

    for attempt in (1, 2):
        prompt = _build_user_prompt(
            template=template, posting=posting, profile=profile, variant=variant,
            facts=facts, judge_verdict=judge_verdict, violations=violations,
        )
        try:
            raw = chat_json(
                system=SYSTEM, user=prompt,
                model=settings.LLM_MODEL_DRAFT, max_tokens=1400, temperature=0.4,
            )
        except LLMUnavailable as exc:
            logger.warning("draft generation failed (attempt %s): %s", attempt, exc)
            return last

        paragraphs = [p for p in (raw.get("paragraphs") or []) if p.get("text")]
        body = "\n\n".join(str(p["text"]).strip() for p in paragraphs).strip()
        if not body:
            violations = ["produced no paragraphs"]
            continue

        fact_keys = sorted({
            str(k) for p in paragraphs for k in (p.get("fact_keys") or [])
        })
        # The subject carries claims too ("with 8 years of .NET"), so it is
        # verified alongside the body rather than trusted.
        subject = (raw.get("subject") or "").strip()
        checks = factguard.run_all(
            body=body, template=template, facts=facts,
            jd_text=posting.body_text or "", previous_bodies=previous_bodies,
            subject=subject,
            context=f"{posting.title or ''} {posting.company_name or ''}",
        )
        last = DraftResult(
            body=body,
            subject=subject or None,
            paragraphs=paragraphs,
            fact_keys=fact_keys,
            jd_hooks=[str(h) for h in (raw.get("jd_hooks") or [])],
            open_questions=[str(q) for q in (raw.get("open_questions") or [])],
            model=raw.get("_model"),
            checks=checks,
            attempts=attempt,
        )
        if last.passed:
            return last

        violations = [
            f"{c.name}: {json.dumps(c.detail, ensure_ascii=False)}"
            for c in checks if not c.passed
        ]
        logger.info("draft attempt %s failed checks: %s",
                    attempt, [c.name for c in checks if not c.passed])

    # Second failure: hand it to the human with the violations attached rather
    # than quietly shipping something that failed verification.
    return last
