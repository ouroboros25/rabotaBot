"""Full-remote classification.

This is now the primary gate for the user's stated requirement ("only fully
remote"), so both directions matter: letting an office job through wastes an
application, and rejecting a real remote job hides it with no trace the user
would notice.
"""
import pytest

from app.services.remote import GLOBAL, HYBRID, ONSITE, UNKNOWN, classify, is_full_remote


def policy(**kw):
    return classify(**kw)[0]


# ------------------------------------------------------------ full remote

@pytest.mark.parametrize("text", [
    "This is a 100% remote position.",
    "We are a fully-remote company.",
    "Remote-first team across 14 countries.",
    "You can work from anywhere in the world.",
    "We are fully distributed.",
    "Remote only, no offices.",
])
def test_explicit_full_remote(text):
    assert policy(title="Backend Engineer", body=text) == GLOBAL


def test_remote_in_the_location_field():
    assert policy(title="Backend Engineer", location="Remote") == GLOBAL
    assert policy(title="Backend Engineer", location="Remote - EMEA") == GLOBAL
    assert policy(title="Backend Engineer", location="Anywhere") == GLOBAL


def test_remote_in_the_title():
    assert policy(title="Senior Backend Engineer (Remote)") == GLOBAL


def test_structured_source_field_is_trusted():
    assert policy(title="Backend Engineer", declared="remote") == GLOBAL
    assert policy(title="Backend Engineer", declared="hybrid") == HYBRID
    assert policy(title="Backend Engineer", declared="onsite") == ONSITE


# ------------------------------------------------------------ not remote

@pytest.mark.parametrize("text", [
    "Hybrid: 3 days a week in the office.",
    "You will work 2 days in-office each week.",
    "This role is based in our Berlin office.",
    "Relocation assistance is provided.",
    "Candidates must be in the office on Mondays.",
])
def test_office_requirements_are_rejected(text):
    assert policy(title="Backend Engineer", body=text) in (HYBRID, ONSITE)


def test_silence_about_remote_is_not_remote():
    """Strict by design: most postings that never say remote are not remote."""
    assert policy(
        title="Senior Backend Engineer",
        location="San Francisco, CA",
        body="Join our team building payments infrastructure.",
    ) == UNKNOWN
    assert is_full_remote(UNKNOWN) is False


def test_explicit_remote_beats_an_optional_office():
    """"Fully remote, with an optional Berlin office" is a remote job."""
    assert policy(
        title="Backend Engineer",
        body="We are fully remote. Our Berlin office is open if you want a desk.",
    ) == GLOBAL


def test_onsite_interview_is_not_an_office_requirement():
    assert policy(
        title="Backend Engineer (Remote)",
        body="The final stage is an onsite interview in London.",
    ) == GLOBAL


def test_remote_perk_deep_in_the_benefits_does_not_make_it_remote():
    """A benefits section at the bottom saying "remote work stipend" appears on
    plenty of office jobs; only the first part of the body is read."""
    body = "Work from our Warsaw office.\n" + ("Details. " * 1500) + "Remote work stipend."
    assert policy(title="Backend Engineer", body=body) in (ONSITE, HYBRID, UNKNOWN)


def test_remote_only_boards_are_trusted():
    """RemoteOK and We Work Remotely only list remote work, and the connectors
    record that as the policy. A listing from one of them should not be rejected
    merely because its prose never repeats the word."""
    assert policy(
        title="Senior Backend Engineer",
        body="Build our payments API in Go.",
        declared="global",
    ) == GLOBAL


def test_a_remote_board_listing_that_says_hybrid_is_still_rejected():
    """The description wins over the board's blanket claim."""
    assert policy(
        title="Senior Backend Engineer",
        body="Hybrid: 2 days a week in our Amsterdam office.",
        declared="global",
    ) in (HYBRID, ONSITE)
