"""The correctness control. A failure here is a fabricated claim reaching an employer."""
from types import SimpleNamespace

from app.services import factguard


def _facts():
    return [
        SimpleNamespace(
            key="F001", type="role", active=True, verifiable_by="references",
            payload={"org": "Varyence", "title": "Principal Engineer",
                     "stack": ["dotnet", "azure"]},
        ),
        SimpleNamespace(
            key="F014", type="metric", active=True, verifiable_by=None,
            payload={"claim": "cut p95 latency from 820ms to 190ms"},
        ),
    ]


JD = "We need a .NET engineer to work on Azure and Kubernetes at Contoso."


def test_grounded_draft_passes():
    body = "I cut p95 latency from 820ms to 190ms on Azure at Varyence."
    assert factguard.fact_guard(body, _facts(), JD).passed


def test_invented_number_is_caught():
    body = "I cut p95 latency from 820ms to 190ms and saved 4200 engineer-hours."
    result = factguard.fact_guard(body, _facts(), JD)
    assert not result.passed
    assert "4200" in " ".join(result.detail["unverified_numbers"])


def test_invented_employer_is_caught():
    body = "At Netflix I ran the 820ms to 190ms migration."
    result = factguard.fact_guard(body, _facts(), JD)
    assert not result.passed
    assert "Netflix" in result.detail["unverified_entities"]


def test_entity_quoted_from_the_posting_is_allowed():
    body = "I have shipped Kubernetes on Azure, as Contoso needs, at Varyence."
    assert factguard.fact_guard(body, _facts(), JD).passed


def test_banlist_catches_ai_tells():
    assert not factguard.banlist("I am excited to leverage my wealth of experience").passed
    assert factguard.banlist("I shipped the migration in nine months.").passed


def test_length_cap_is_per_template():
    long_body = "word " * 200
    assert not factguard.length_cap(long_body, "cold_email").passed
    assert factguard.length_cap(long_body, "ats_cover").passed


def test_novelty_rejects_a_near_copy():
    body = "I cut p95 latency from 820ms to 190ms on Azure at Varyence."
    assert not factguard.novelty(body, [body]).passed
    assert factguard.novelty(body, ["Something else entirely about Kubernetes"]).passed


def test_jd_hook_requires_a_detail_from_this_posting():
    assert factguard.jd_hook("I have deep Kubernetes experience.", JD).passed
    assert not factguard.jd_hook("I am a generalist who ships quickly.", JD).passed


def test_number_from_the_posting_is_not_a_licence_to_claim_it():
    """Regression: a live draft claimed "5-10 engineers" because "10" appeared
    somewhere in the job description. A number in a cover letter is a claim about
    the candidate, so only the ledger can source it."""
    jd = "You will lead a team of 10 engineers and own 3 services."
    body = "At Varyence I guided 10 engineers through the migration."
    result = factguard.fact_guard(body, _facts(), jd)
    assert not result.passed
    assert "10" in " ".join(result.detail["unverified_numbers"])


def test_entities_from_the_posting_remain_allowed():
    """Naming the employer's stack is the point of a tailored letter."""
    jd = "We run Kubernetes on Azure at Contoso."
    body = "I have shipped Kubernetes on Azure, which Contoso runs, at Varyence."
    assert factguard.fact_guard(body, _facts(), jd).passed


def test_small_numbers_are_checked_too():
    body = "I shipped 4 platforms at Varyence."
    assert not factguard.fact_guard(body, _facts(), "").passed


def test_ledger_keys_must_not_leak_into_prose():
    """Regression: a live draft wrote "as stated in F001" into the letter."""
    assert not factguard.no_ledger_keys("As stated in F001, I led the migration.").passed
    assert not factguard.no_ledger_keys("body", subject="Senior role (F010)").passed
    assert factguard.no_ledger_keys("At Varyence I led the migration.").passed


def test_subject_is_held_to_the_same_evidence_rules():
    checks = factguard.run_all(
        body="At Varyence I cut p95 latency from 820ms to 190ms.",
        template="ats_cover", facts=_facts(), jd_text=JD, previous_bodies=[],
        subject="Senior .NET engineer with 8 years of experience",
    )
    factcheck = next(c for c in checks if c.name == "factguard")
    assert not factcheck.passed
    assert "8" in " ".join(factcheck.detail["unverified_numbers"])


def test_hyphenated_form_of_a_grounded_term_is_allowed():
    """Regression: "Azure-native" was flagged although "azure" is in the ledger."""
    body = "At Varyence I built Azure-native services and Kubernetes-based tooling."
    jd = "We run Kubernetes."
    assert factguard.fact_guard(body, _facts(), jd).passed


def test_genuinely_new_compound_entity_is_still_caught():
    body = "At Varyence I built Snowflake-native pipelines."
    assert not factguard.fact_guard(body, _facts(), "").passed


def test_specificity_does_not_demand_a_number_an_empty_ledger_cannot_supply():
    """fact_guard forbids unsourced digits, so an unconditional number
    requirement would make a metric-free ledger permanently unsatisfiable."""
    from types import SimpleNamespace

    no_metrics = [SimpleNamespace(
        key="F001", type="role", active=True, verifiable_by=None,
        payload={"org": "Varyence", "stack": ["azure"]},
    )]
    result = factguard.specificity("I built Azure services at Varyence.", no_metrics)
    assert result.passed
    assert result.detail["ledger_has_numbers"] is False
    assert "hint" in result.detail

    # With metrics available, a number is expected again.
    result = factguard.specificity("I built Azure services at Varyence.", _facts())
    assert not result.passed


def test_employer_name_from_the_posting_header_is_allowed():
    """Regression: "Talent Sam" was flagged because the company name appears in
    the posting title rather than inside the description body."""
    body = "The Back-End Developer role at Talent Sam matches my work at Varyence."
    result = factguard.fact_guard(
        body, _facts(), jd_text="We need a backend developer.",
        context="Back-End Developer Talent Sam",
    )
    assert result.passed


def test_invented_tenure_is_caught():
    """Models compute "2 years of experience" from a start date. That arithmetic
    is not theirs to do and is usually wrong."""
    body = "I have 2 years of experience at Varyence."
    result = factguard.fact_guard(body, _facts(), "", context="")
    assert not result.passed
    assert "2" in " ".join(result.detail["unverified_numbers"])
