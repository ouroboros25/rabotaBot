"""Celery tasks. Thin wrappers: all logic lives in app.services."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import settings
from app.db import session_scope
from app.models import Application, JobCluster, Score, SkipFeedback, Source
from app.services import ingest, notify, pipeline, queue as queue_service, slugs
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="rabota.ingest.sweep")
def ingest_sweep() -> dict:
    totals = {"sources": 0, "seen": 0, "new": 0, "errors": 0}
    with session_scope() as db:
        sources = ingest.due_sources(db)
        for source in sources:
            run = ingest.run_source(db, source)
            db.commit()
            totals["sources"] += 1
            totals["seen"] += run.items_seen or 0
            totals["new"] += run.items_new or 0
            totals["errors"] += 1 if run.error else 0
        slugs.promote_ready(db)
    return totals


@celery_app.task(name="rabota.ingest.gates")
def ingest_gates() -> dict:
    """Gate whatever arrived since the last run.

    Bounded per run by the watermark, so a large backlog drains over a few ticks
    instead of blocking one long transaction.
    """
    with session_scope() as db:
        return ingest.apply_gates(db)


@celery_app.task(name="rabota.score.batch")
def score_batch() -> dict:
    with session_scope() as db:
        return pipeline.score_batch(db)


@celery_app.task(name="rabota.judge.top")
def judge_top() -> dict:
    with session_scope() as db:
        return pipeline.judge_top(db)


@celery_app.task(name="rabota.draft.create")
def draft_create(cluster_id: int, template: str | None = None) -> dict:
    with session_scope() as db:
        draft = queue_service.create_draft(db, cluster_id, template)
        if draft is None:
            return {"ok": False, "reason": "generation failed or gateway unavailable"}
        db.commit()
        from app.bot.cards import push_draft_card

        push_draft_card(db, draft.id)
        return {"ok": True, "draft_id": draft.id}


@celery_app.task(name="rabota.digest.daily")
def digest_daily() -> dict:
    from app.bot.cards import push_digest

    with session_scope() as db:
        rows = queue_service.digest_candidates(db)
        if not rows:
            notify.send_message("Сегодня нечего показать: очередь пуста.")
            return {"sent": 0}
        push_digest(db, rows)
        return {"sent": len(rows)}


@celery_app.task(name="rabota.followups.check")
def followups_check() -> dict:
    """Nudge at day 4-5 and close out at day 10.

    Both numbers come from the same fact: the median time to archive a
    non-interviewed candidate is about six days. A reminder has to land BEFORE
    that, and silence past ten days is functionally a rejection, so the tracker
    should stop pretending otherwise.
    """
    now = datetime.now(timezone.utc)
    nudged = closed = 0
    with session_scope() as db:
        due = db.execute(
            select(Application).where(
                Application.stage == "sent",
                Application.replied.is_(False),
                Application.follow_up_due_at <= now,
                Application.follow_up_sent_at.is_(None),
            ).limit(20)
        ).scalars().all()
        for app_row in due:
            cluster = db.get(JobCluster, app_row.cluster_id)
            posting = queue_service._canonical(db, cluster) if cluster else None
            title = posting.title if posting else f"application {app_row.id}"
            company = (posting.company_name if posting else "") or ""
            notify.send_message(
                f"⏰ <b>Пора напомнить о себе</b>\n{title} · {company}\n"
                f"Отправлено {app_row.sent_at:%d.%m}. Медиана архивации неотобранных "
                f"кандидатов 6 дней, так что напоминание имеет смысл именно сейчас.\n"
                f"/followup {app_row.id} — сгенерировать текст"
            )
            app_row.follow_up_sent_at = now
            nudged += 1

        dead = db.execute(
            select(Application).where(
                Application.stage == "sent",
                Application.replied.is_(False),
                Application.presumed_dead_at <= now,
            ).limit(50)
        ).scalars().all()
        for app_row in dead:
            queue_service.advance(db, app_row, "ghosted", actor="auto",
                                  note="no reply within the configured window")
            closed += 1
    return {"nudged": nudged, "auto_closed": closed}


@celery_app.task(name="rabota.retro.weekly")
def retro_weekly() -> dict:
    from app.bot.cards import push_retro

    with session_scope() as db:
        return push_retro(db)


@celery_app.task(name="rabota.housekeeping.nightly")
def housekeeping() -> dict:
    with session_scope() as db:
        purged = ingest.purge_expired_raw(db)
        stale = _archive_stale_clusters(db)
        return {"raw_purged": purged, "clusters_archived": stale}


def _archive_stale_clusters(db, days: int = 45) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(
        select(JobCluster).where(
            JobCluster.last_seen_at < cutoff,
            JobCluster.status.in_(("new", "scored", "gated")),
        ).limit(2000)
    ).scalars().all()
    for cluster in rows:
        cluster.status = "archived"
    return len(rows)
