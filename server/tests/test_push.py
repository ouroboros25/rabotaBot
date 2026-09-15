"""New-match push scheduling.

The failure mode here is not a crash, it is a bot people mute: repeating jobs
already sent, or arriving at 4am.
"""
import pytest

from app.config import settings
from app.workers.tasks import _in_quiet_hours


@pytest.fixture
def quiet(monkeypatch):
    def _set(start, end):
        monkeypatch.setattr(settings, "PUSH_QUIET_FROM", start)
        monkeypatch.setattr(settings, "PUSH_QUIET_TO", end)
    return _set


def _at(monkeypatch, hour):
    import datetime as real_datetime

    class FrozenDatetime(real_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime.datetime(2026, 9, 15, hour, 0, tzinfo=tz)

    monkeypatch.setattr("app.workers.tasks.datetime", FrozenDatetime)


def test_quiet_window_wrapping_midnight(monkeypatch, quiet):
    quiet(23, 8)
    for hour, expected in [(23, True), (2, True), (7, True), (8, False),
                           (14, False), (22, False)]:
        _at(monkeypatch, hour)
        assert _in_quiet_hours() is expected, f"hour {hour}"


def test_quiet_window_inside_one_day(monkeypatch, quiet):
    quiet(13, 15)
    _at(monkeypatch, 14)
    assert _in_quiet_hours() is True
    _at(monkeypatch, 16)
    assert _in_quiet_hours() is False


def test_equal_bounds_disable_quiet_hours(monkeypatch, quiet):
    quiet(0, 0)
    _at(monkeypatch, 3)
    assert _in_quiet_hours() is False
