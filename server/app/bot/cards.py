"""Telegram card rendering and push.

The card is read on a phone, in a gap, and the decision it supports is "open
this or not". So it leads with the job and the reason it matched, and keeps the
scoring internals out of the way: a person does not act on "source prior 0.31",
they act on "matched python, azure" and "posted 3 hours ago".

Warnings appear only when there is something to warn about. A card that always
shows six metrics teaches the reader to skip all six.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Application, Draft, JobCluster, Score, SkipFeedback,
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
        return "дата неизвестна"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    hours = delta.total_seconds() / 3600
    if hours < 2:
        return "только что"
    if hours < 24:
        return f"{int(hours)} ч назад"
    if delta.days == 1:
        return "вчера"
    if delta.days < 7:
        return f"{delta.days} дн назад"
    if delta.days < 30:
        return f"{delta.days // 7} нед назад"
    return f"{delta.days // 30} мес назад"


def _money(row: dict) -> str | None:
    lo, hi = row.get("comp_min"), row.get("comp_max")
    cur = (row.get("comp_currency") or "").upper()
    if not lo and not hi:
        return None
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get(cur, "")

    def fmt(value: float) -> str:
        if value >= 1000:
            return f"{symbol}{value / 1000:.0f}k" if symbol else f"{value / 1000:.0f}k {cur}"
        return f"{symbol}{value:.0f}" if symbol else f"{value:.0f} {cur}"

    if lo and hi and lo != hi:
        return f"{fmt(lo)}–{fmt(hi)}"
    return fmt(hi or lo)


def _match_line(row: dict) -> str | None:
    """Why this is here at all. The single most useful line on the card."""
    hits = row.get("keyword_hits") or []
    if not hits:
        return None
    total = row.get("keyword_total") or len(hits)
    shown = ", ".join(esc(h) for h in hits[:6])
    if len(hits) < total:
        return f"🔑 совпало {len(hits)} из {total}: {shown}"
    return f"🔑 совпало всё: {shown}"


def _warnings(row: dict) -> list[str]:
    out = []
    if row.get("injection_suspected"):
        out.append("🛑 в тексте есть указания, адресованные модели — читать глазами")
    if (row.get("ghost_risk") or 0) >= 0.35:
        out.append("👻 похоже на вечную вакансию, которую не закрывают")
    if (row.get("fan_out") or 1) >= 5:
        out.append(f"👥 висит на {row['fan_out']} площадках — откликов там много")
    for risk in (row.get("risks") or [])[:1]:
        out.append(f"⚠️ {esc(risk)}")
    return out


def render_card(row: dict, *, is_new: bool = False) -> str:
    """One opportunity, formatted for a five-second decision."""
    head = "🆕 " if is_new else ""
    lines = [f"{head}<b>{esc(row['title'])}</b>"]

    facts = [esc(row.get("company") or "компания не указана")]
    money = _money(row)
    if money:
        facts.append(money)
    facts.append("🌍 удалённо")
    facts.append(_age(row.get("posted_at")))
    lines.append(" · ".join(facts))

    match = _match_line(row)
    if match:
        lines.append("")
        lines.append(match)

    if row.get("llm_fit") is not None:
        verdict = int(row["llm_fit"])
        mark = "👍" if verdict >= 7 else "🤔" if verdict >= 5 else "👎"
        lines.append(f"{mark} оценка {verdict}/10")

    quote = next(
        (e.get("quote") for e in (row.get("evidence") or []) if e.get("quote")), None
    )
    if quote:
        lines.append(f"<i>«{esc(quote)[:150]}»</i>")

    warnings = _warnings(row)
    if warnings:
        lines.append("")
        lines.extend(warnings)

    lines.append("")
    lines.append(
        f"<i>{esc(row.get('source_key'))} · "
        f"{TRACK_LABEL.get(row.get('track'), row.get('track'))}</i>"
    )
    return "\n".join(lines)


def _card_keyboard(cluster_id: int, apply_url: str | None = None) -> dict:
    rows = [[
        {"text": "✍️ Написать отклик", "callback_data": f"draft:{cluster_id}"},
    ]]
    if apply_url:
        rows[0].append({"text": "🔗 Открыть", "url": apply_url})
    rows.append([
        {"text": "⏭ Не то", "callback_data": f"skip:{cluster_id}"},
        {"text": "👀 Почему", "callback_data": f"why:{cluster_id}"},
        {"text": "🚫 Компания", "callback_data": f"block:{cluster_id}"},
    ])
    return {"inline_keyboard": rows}


def push_digest(db: Session, rows: list[dict]) -> int:
    remaining = queue_service.remaining_this_week(db)
    notify.send_message(
        f"☀️ <b>Доброе утро. {_plural(len(rows), 'вакансия', 'вакансии', 'вакансий')} "
        f"на сегодня</b>\n"
        f"Отправок на неделе осталось {remaining}."
    )
    sent = 0
    for row in rows:
        if notify.send_message(
            render_card(row), _card_keyboard(row["cluster_id"], row.get("apply_url"))
        ):
            sent += 1
    return sent


def push_new(db: Session, rows: list[dict]) -> int:
    """Cards for things that appeared since the last check."""
    if not rows:
        return 0
    if len(rows) > 1:
        notify.send_message(
            f"🆕 <b>Нашлось новое: "
            f"{_plural(len(rows), 'вакансия', 'вакансии', 'вакансий')}</b>"
        )
    sent = 0
    for row in rows:
        if notify.send_message(
            render_card(row, is_new=True),
            _card_keyboard(row["cluster_id"], row.get("apply_url")),
        ):
            sent += 1
    return sent


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Russian counts read wrong without this and it shows on every message."""
    tail_100 = n % 100
    tail_10 = n % 10
    if 11 <= tail_100 <= 14:
        word = many
    elif tail_10 == 1:
        word = one
    elif 2 <= tail_10 <= 4:
        word = few
    else:
        word = many
    return f"{n} {word}"


def push_draft_card(db: Session, draft_id: int) -> bool:
    draft = db.get(Draft, draft_id)
    if draft is None:
        return False
    version = queue_service.latest_version(db, draft)
    if version is None:
        return False
    cluster = db.get(JobCluster, draft.cluster_id)
    posting = queue_service._canonical(db, cluster) if cluster else None

    failed = [c for c in version.checks if not c.passed]
    body = esc(version.body)
    if len(body) > 2500:
        body = body[:2500] + "…"

    lines = [
        f"✍️ <b>Черновик готов</b>",
        f"{esc(posting.title if posting else '')} · "
        f"{esc(posting.company_name if posting else '')}",
        "",
        body,
        "",
    ]
    if failed:
        lines.append(
            "⚠️ <b>Не прошло проверку:</b> " + ", ".join(_CHECK_RU.get(c.check, c.check)
                                                         for c in failed)
        )
        lines.append("<i>Показываю всё равно: решение ваше. Проверьте отмеченное.</i>")
    else:
        lines.append("✅ Все проверки пройдены, факты сверены с вашим реестром.")

    if version.open_questions:
        lines.append(f"❓ Стоит спросить: {esc(version.open_questions[0])}")

    lines.append("")
    lines.append("<i>Бот не отправляет. Откройте форму, вставьте текст, "
                 "отправьте сами, потом нажмите «Отправил».</i>")

    keyboard = {"inline_keyboard": [
        [{"text": "📤 Я отправил", "callback_data": f"sent:{draft.id}"}],
        [
            {"text": "🔁 Переписать", "callback_data": f"regen:{draft.id}"},
            {"text": "❌ Удалить", "callback_data": f"discard:{draft.id}"},
        ],
    ]}
    if posting and posting.apply_url:
        keyboard["inline_keyboard"].insert(
            0, [{"text": "🔗 Открыть форму", "url": posting.apply_url}]
        )
    return notify.send_message("\n".join(lines), keyboard)


_CHECK_RU = {
    "factguard": "обоснованность фактов",
    "no_ledger_keys": "служебные ключи в тексте",
    "banlist": "штампы",
    "length": "длина",
    "specificity": "конкретность",
    "jd_hook": "зацепка из вакансии",
    "novelty": "непохожесть на прошлые",
}


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
        f"Показано {surfaced} · черновиков {drafted} · отправлено {sent}",
        f"Ответов: {replied}" + (f" ({replied / sent:.0%})" if sent else ""),
    ]

    if skips:
        lines.append("\n<b>Почему вы отклоняли:</b>")
        for code, count in skips:
            lines.append(f"  {esc(code)} — {count}")
        repeated = [f"{c} ({n})" for c, n in skips if n >= 3]
        if repeated:
            lines.append(
                "\n💡 Одна причина повторяется: " + ", ".join(esc(r) for r in repeated)
                + ". Стоит добавить это в исключения на странице «Поиск», "
                "чтобы такое не доходило до вас вообще."
            )

    if sent == 0:
        lines.append(
            "\n<i>За неделю ничего не отправлено. Если так и дальше, проблема "
            "не в подборе, а в том, что очередь не доходит до отправки.</i>"
        )
    elif replied == 0 and sent >= 10:
        lines.append(
            "\n<i>10+ отправок без единого ответа. Это повод пересмотреть текст "
            "и реестр фактов, а не поднимать объём.</i>"
        )

    notify.send_message("\n".join(lines))
    return {"surfaced": surfaced, "drafted": drafted, "sent": sent, "replied": replied}
