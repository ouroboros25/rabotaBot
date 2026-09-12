"""Telegram card rendering and push, used by workers.

The interaction shape is triage, not authoring: a handful of cards a day, each a
five-second accept or skip, usually on a phone. Everything on a card exists to
make that decision in five seconds or to explain why the bot ranked it there.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Application, Draft, JobCluster, Score, SkipFeedback, Source,
)
from app.services import notify, queue as queue_service

logger = logging.getLogger(__name__)

TRACK_LABEL = {
    "fte": "найм", "outstaff": "контракт", "freelance": "фриланс", "equity": "стартап",
}


def esc(value) -> str:
    """Everything from a posting is third-party text and gets escaped."""
    return html.escape(str(value or ""), quote=False)


def _age(dt: datetime | None) -> str:
    if dt is None:
        return "возраст неизвестен"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return "только что"
    if hours < 48:
        return f"{int(hours)} ч назад"
    return f"{delta.days} дн назад"


def _comp(row: dict) -> str:
    lo, hi, cur = row.get("comp_min"), row.get("comp_max"), row.get("comp_currency") or ""
    if not lo and not hi:
        return "вилка не указана"
    if lo and hi:
        return f"{int(lo):,}-{int(hi):,} {cur}".replace(",", " ")
    return f"{int(hi or lo):,} {cur}".replace(",", " ")


def _signal_line(row: dict) -> str:
    bits = [f"источник {esc(row.get('source_key'))} ({row.get('source_prior', 0):.0%})"]
    fan = row.get("fan_out", 1)
    # Fan-out is shown as a warning, not a badge: a job every scanner found is a
    # more crowded auction, which measurably lowers the reply rate.
    bits.append(f"на {fan} площадк{'е' if fan == 1 else 'ах'}"
                + (" ⚠ толпа" if fan >= 4 else ""))
    ghost = row.get("ghost_risk") or 0
    if ghost >= 0.3:
        bits.append(f"⚠ риск фейка {ghost:.0%}")
    return " · ".join(bits)


def render_card(row: dict) -> str:
    lines = [
        f"<b>{row['priority']:.0f}</b> · {esc(row['title'])}",
        f"{esc(row.get('company') or 'компания не указана')} · "
        f"{TRACK_LABEL.get(row.get('track'), row.get('track'))}",
        f"💰 {esc(_comp(row))}   🌍 {esc(row.get('remote_policy'))}   "
        f"🕐 {_age(row.get('posted_at'))}",
        _signal_line(row),
    ]
    if row.get("llm_fit") is not None:
        lines.append(f"оценка судьи: {row['llm_fit']:.0f}/10")
    for item in (row.get("evidence") or [])[:2]:
        quote = esc(item.get("quote", ""))[:140]
        lines.append(f"✓ «{quote}»")
    for risk in (row.get("risks") or [])[:2]:
        lines.append(f"⚠ {esc(risk)}")
    if row.get("injection_suspected"):
        lines.append(
            "🛑 <b>в тексте вакансии есть указания, адресованные модели.</b> "
            "Позиция понижена, читать вручную."
        )
    if row.get("apply_url"):
        lines.append(f'<a href="{esc(row["apply_url"])}">открыть вакансию</a>')
    return "\n".join(lines)


def _card_keyboard(cluster_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✍️ Черновик", "callback_data": f"draft:{cluster_id}"},
                {"text": "👀 Почему", "callback_data": f"why:{cluster_id}"},
            ],
            [
                {"text": "⏭ Пропустить", "callback_data": f"skip:{cluster_id}"},
                {"text": "🚫 Компания", "callback_data": f"block:{cluster_id}"},
            ],
        ]
    }


def push_digest(db: Session, rows: list[dict]) -> int:
    remaining = queue_service.remaining_this_week(db)
    header = (
        f"📋 <b>Дайджест</b> · {len(rows)} позиций\n"
        f"Лимит отправок на неделе: осталось {remaining}.\n"
        f"<i>Лимит это фича: на измеренных данных конверсия падает втрое, "
        f"когда объём растёт.</i>"
    )
    notify.send_message(header)
    sent = 0
    for row in rows:
        if notify.send_message(render_card(row), _card_keyboard(row["cluster_id"])):
            sent += 1
    return sent


def push_draft_card(db: Session, draft_id: int) -> bool:
    draft = db.get(Draft, draft_id)
    if draft is None:
        return False
    version = queue_service.latest_version(db, draft)
    if version is None:
        return False
    cluster = db.get(JobCluster, draft.cluster_id)
    posting = queue_service._canonical(db, cluster) if cluster else None

    checks = list(version.checks)
    failed = [c for c in checks if not c.passed]
    status = "✅ все проверки пройдены" if not failed else (
        "⚠️ не прошли: " + ", ".join(esc(c.check) for c in failed)
    )

    body = esc(version.body)
    if len(body) > 2600:
        body = body[:2600] + "…"

    text = "\n".join([
        f"✍️ <b>Черновик готов</b> · {esc(posting.title if posting else '')}",
        f"{esc(posting.company_name if posting else '')} · "
        f"{esc(draft.template)} · {version.word_count} слов",
        "─" * 20,
        body,
        "─" * 20,
        status,
        f"факты: {', '.join(esc(k) for k in (version.fact_keys or [])) or '—'}",
    ])
    if version.open_questions:
        text += "\nвопросы: " + esc("; ".join(version.open_questions[:2]))

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Одобрить", "callback_data": f"approve:{draft.id}"},
                {"text": "🔁 Перегенерировать", "callback_data": f"regen:{draft.id}"},
            ],
            [
                {"text": "📤 Отправил", "callback_data": f"sent:{draft.id}"},
                {"text": "❌ Удалить", "callback_data": f"discard:{draft.id}"},
            ],
        ]
    }
    if posting and posting.apply_url:
        keyboard["inline_keyboard"].insert(
            0, [{"text": "🔗 Открыть форму", "url": posting.apply_url}]
        )
    return notify.send_message(text, keyboard)


def push_retro(db: Session) -> dict:
    """Weekly retro. This is where the feedback loop becomes visible."""
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    surfaced = db.execute(
        select(func.count(Score.id)).where(Score.scored_at >= week_ago)
    ).scalar_one()
    drafted = db.execute(
        select(func.count(Draft.id)).where(Draft.created_at >= week_ago)
    ).scalar_one()
    sent = db.execute(
        select(func.count(Application.id)).where(Application.sent_at >= week_ago)
    ).scalar_one()
    replied = db.execute(
        select(func.count(Application.id)).where(
            Application.replied.is_(True), Application.replied_at >= week_ago
        )
    ).scalar_one()

    skips = db.execute(
        select(SkipFeedback.reason_code, func.count(SkipFeedback.id))
        .where(SkipFeedback.at >= week_ago)
        .group_by(SkipFeedback.reason_code)
        .order_by(func.count(SkipFeedback.id).desc())
    ).all()

    lines = [
        "📊 <b>Итоги недели</b>",
        f"оценено {surfaced} → черновиков {drafted} → отправлено {sent}",
        f"ответов {replied}" + (f" ({replied / sent:.0%})" if sent else ""),
    ]

    if skips:
        lines.append("\n<b>Почему вы отклоняли:</b>")
        for code, count in skips:
            lines.append(f"  {esc(code)}: {count}")
        # Three of the same reason is the threshold at which a pattern is worth
        # turning into a hard gate rather than a repeated manual decision.
        repeated = [f"{c} ({n})" for c, n in skips if n >= 3]
        if repeated:
            lines.append(
                "\n💡 Повторяющиеся причины: " + ", ".join(esc(r) for r in repeated)
                + "\nСтоит добавить правило в <code>config/rubric.yaml</code> → "
                "<code>gates</code>, чтобы это отсеивалось до показа."
            )

    if sent == 0:
        lines.append(
            "\n<i>За неделю ничего не отправлено. Если так и дальше, проблема "
            "не в скоринге, а в том, что очередь не доходит до отправки.</i>"
        )
    elif replied == 0 and sent >= 10:
        lines.append(
            "\n<i>10+ отправок без ответа. Это сигнал проверить текст черновиков "
            "и реестр фактов, а не поднимать объём.</i>"
        )

    notify.send_message("\n".join(lines))
    return {"surfaced": surfaced, "drafted": drafted, "sent": sent, "replied": replied}
