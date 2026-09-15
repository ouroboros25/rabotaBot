"""Telegram card rendering.

The card is the product surface people actually use, and every one of these
cases showed up as something unreadable in a real message.
"""
from datetime import datetime, timedelta, timezone

from app.bot.cards import _age, _money, _plural, render_card


def _row(**kw):
    base = dict(
        cluster_id=1, title="Senior Backend Engineer", company="Acme",
        track="fte", priority=210.0, source_key="himalayas",
        posted_at=datetime.now(timezone.utc) - timedelta(hours=5),
        comp_min=None, comp_max=None, comp_currency=None,
        fan_out=1, ghost_risk=0.0, llm_fit=None, evidence=[], risks=[],
        injection_suspected=False, keyword_hits=[], keyword_total=0,
        apply_url="https://example.com/job",
    )
    base.update(kw)
    return base


def test_russian_plurals():
    """Counts read wrong without this and it shows on every message."""
    assert _plural(1, "вакансия", "вакансии", "вакансий") == "1 вакансия"
    assert _plural(2, "вакансия", "вакансии", "вакансий") == "2 вакансии"
    assert _plural(5, "вакансия", "вакансии", "вакансий") == "5 вакансий"
    assert _plural(11, "вакансия", "вакансии", "вакансий") == "11 вакансий"
    assert _plural(21, "вакансия", "вакансии", "вакансий") == "21 вакансия"


def test_money_formatting():
    assert _money(_row(comp_min=60000, comp_max=90000, comp_currency="USD")) == "$60k–$90k"
    assert _money(_row(comp_max=120000, comp_currency="EUR")) == "€120k"
    assert _money(_row()) is None


def test_age_is_human():
    now = datetime.now(timezone.utc)
    assert _age(now) == "только что"
    assert _age(now - timedelta(hours=5)) == "5 ч назад"
    assert _age(now - timedelta(days=1)) == "вчера"
    assert _age(now - timedelta(days=3)) == "3 дн назад"
    assert _age(None) == "дата неизвестна"


def test_matched_keywords_are_the_headline_reason():
    card = render_card(_row(keyword_hits=["python", "azure"], keyword_total=4))
    assert "совпало 2 из 4" in card
    assert "python, azure" in card


def test_full_match_says_so():
    card = render_card(_row(keyword_hits=["python", "azure"], keyword_total=2))
    assert "совпало всё" in card


def test_quiet_card_carries_no_warnings():
    """A card that always shows six metrics teaches the reader to skip all six."""
    card = render_card(_row())
    for noise in ("👻", "👥", "🛑", "⚠️"):
        assert noise not in card


def test_warnings_appear_only_when_earned():
    card = render_card(_row(fan_out=7, ghost_risk=0.5, injection_suspected=True))
    assert "7 площадках" in card
    assert "👻" in card
    assert "🛑" in card


def test_third_party_text_is_escaped():
    """Job titles are attacker-controllable and the card is HTML."""
    card = render_card(_row(
        title="<b>Engineer</b> <script>alert(1)</script>",
        company="<i>Evil</i>",
    ))
    assert "<script>" not in card
    assert "&lt;script&gt;" in card
    assert "<b>Engineer" not in card.replace("<b>&lt;b&gt;", "")


def test_new_card_is_marked_as_new():
    assert render_card(_row(), is_new=True).startswith("🆕")
    assert not render_card(_row()).startswith("🆕")


def test_verdict_and_quote_are_shown_when_present():
    card = render_card(_row(
        llm_fit=8.0,
        evidence=[{"claim": "stack", "quote": "We use Python and Azure daily"}],
    ))
    assert "8/10" in card
    assert "We use Python and Azure daily" in card
