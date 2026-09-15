"""Celery app and beat schedule.

Cadence rationale, not arbitrary numbers:
  ingest   every 20 min  - source-level cadence is enforced separately in
                           due_sources(), so this is just the tick
  score    every 30 min  - cheap, deterministic, no external calls
  judge    hourly        - the only LLM-heavy job; capped by JUDGE_TOP_K
  digest   once a day    - the product is one considered list, not a firehose
  retro    weekly        - where the learning loop actually surfaces
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import settings
from app.logging_conf import setup_logging

setup_logging()

celery_app = Celery("rabota", broker=settings.redis_url, backend=settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=settings.TZ,
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=1800,
    task_soft_time_limit=1500,
    result_expires=3600,
    task_default_queue="default",
    task_routes={
        "rabota.ingest*": {"queue": "ingest"},
        "rabota.score*": {"queue": "score"},
        "rabota.judge*": {"queue": "score"},
        "rabota.draft*": {"queue": "draft"},
        "rabota.digest*": {"queue": "notify"},
        "rabota.notify*": {"queue": "notify"},
        "rabota.retro*": {"queue": "notify"},
        "rabota.followups*": {"queue": "notify"},
    },
    beat_schedule={
        "ingest-due-sources": {
            "task": "rabota.ingest.sweep",
            "schedule": crontab(minute="*/20"),
        },
        "apply-gates": {
            "task": "rabota.ingest.gates",
            "schedule": crontab(minute="5,35"),
        },
        "score-batch": {
            "task": "rabota.score.batch",
            "schedule": crontab(minute="10,40"),
        },
        "judge-top": {
            "task": "rabota.judge.top",
            "schedule": crontab(minute="25"),
        },
        "push-new-matches": {
            "task": "rabota.notify.new",
            "schedule": crontab(minute="15,45"),
        },
        "daily-digest": {
            "task": "rabota.digest.daily",
            "schedule": crontab(hour=settings.TELEGRAM_DIGEST_HOUR, minute=30),
        },
        "follow-ups": {
            "task": "rabota.followups.check",
            "schedule": crontab(hour="9,17", minute=0),
        },
        "weekly-retro": {
            "task": "rabota.retro.weekly",
            "schedule": crontab(
                day_of_week=settings.TELEGRAM_RETRO_WEEKDAY,
                hour=settings.TELEGRAM_RETRO_HOUR,
                minute=0,
            ),
        },
        "housekeeping": {
            "task": "rabota.housekeeping.nightly",
            "schedule": crontab(hour=3, minute=15),
        },
    },
)

# Import side effect: registers every task with the app.
from app.workers import tasks  # noqa: E402,F401
