"""Evidence validation.

The judge's output is untrusted input like any other model output. A free model
will drift from the requested schema, and an unhandled shape here crashed a whole
production judge batch.
"""
from app.services.judge import _validate_evidence

BODY = "We need a senior engineer with strong Kubernetes and Azure experience."


def test_quote_present_in_the_body_is_kept():
    out = _validate_evidence(
        {"fit_0_10": 8, "evidence": [
            {"claim": "needs k8s", "quote": "strong Kubernetes and Azure experience"},
        ]},
        BODY,
    )
    assert len(out["evidence"]) == 1
    assert out["evidence_dropped"] == 0


def test_invented_quote_is_dropped_and_the_verdict_is_downgraded():
    out = _validate_evidence(
        {"fit_0_10": 9, "evidence": [
            {"claim": "pays well", "quote": "salary is 300000 dollars per year"},
        ]},
        BODY,
    )
    assert out["evidence"] == []
    assert out["fit_0_10"] <= 4, "a verdict resting on invented quotes must not lead"
    assert any("verifiable" in r for r in out["risks"])


def test_bare_string_evidence_does_not_crash():
    """Regression: a model returned evidence as a list of strings and the
    AttributeError killed the whole judge batch."""
    out = _validate_evidence(
        {"fit_0_10": 7, "evidence": ["strong Kubernetes and Azure experience"]},
        BODY,
    )
    assert len(out["evidence"]) == 1
    assert out["evidence"][0]["quote"] == "strong Kubernetes and Azure experience"


def test_renamed_keys_are_tolerated():
    out = _validate_evidence(
        {"fit_0_10": 7, "evidence": [
            {"reason": "k8s", "text": "strong Kubernetes and Azure experience"},
        ]},
        BODY,
    )
    assert len(out["evidence"]) == 1


def test_evidence_of_the_wrong_type_is_ignored():
    out = _validate_evidence({"fit_0_10": 6, "evidence": "not a list"}, BODY)
    assert out["evidence"] == []
    out = _validate_evidence({"fit_0_10": 6, "evidence": [123, None, {}]}, BODY)
    assert out["evidence"] == []


def test_missing_evidence_key_is_fine():
    out = _validate_evidence({"fit_0_10": 5}, BODY)
    assert out["evidence"] == []
    assert out["evidence_dropped"] == 0
