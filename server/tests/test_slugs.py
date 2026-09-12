"""Harvested-slug promotion.

Slug discovery is the real engineering problem in this design, so the promotion
rule gets tests: promoting a board twice doubles outbound traffic to a publisher
that is doing us a favour by exposing the endpoint at all.
"""
from types import SimpleNamespace

from app.services.slugs import _already_covered


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self._rows


class FakeDB:
    def __init__(self, sources):
        self._sources = sources

    def execute(self, stmt):
        # The query filters by family; approximate that by returning everything
        # and letting _already_covered compare the parameter.
        return FakeResult(self._sources)


def _source(family, params):
    return SimpleNamespace(family=family, params=params)


def test_manually_configured_board_blocks_promotion():
    db = FakeDB([_source("ashby_board", {"board": "openai"})])
    assert _already_covered(db, "ashby_board", "board", "openai") is True


def test_slug_comparison_is_case_insensitive():
    db = FakeDB([_source("greenhouse_board", {"board_token": "Stripe"})])
    assert _already_covered(db, "greenhouse_board", "board_token", "stripe") is True


def test_new_board_is_promotable():
    db = FakeDB([_source("ashby_board", {"board": "openai"})])
    assert _already_covered(db, "ashby_board", "board", "linear") is False


def test_missing_param_does_not_match():
    db = FakeDB([_source("ashby_board", {})])
    assert _already_covered(db, "ashby_board", "board", "openai") is False
