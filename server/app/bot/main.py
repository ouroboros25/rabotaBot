"""Telegram bot process (long polling).

Why long polling and not a webhook: there is no public ingress, no TLS
termination, no CSRF surface and no endpoint to brute-force. For a single-user
tool the strongest access control available is not having an access-controlled
surface at all.

Authorisation is one rule, enforced on every update: the chat id must equal
TELEGRAM_CHAT_ID. An unset chat id means the bot answers nobody.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application as TgApplication, CallbackQueryHandler, CommandHandler, ContextTypes,
)

from app.config import settings
from app.logging_conf import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def authorised(update: Update) -> bool:
    chat = update.effective_chat
    if chat is None or not settings.TELEGRAM_CHAT_ID:
        return False
    return str(chat.id) == str(settings.TELEGRAM_CHAT_ID)


def guard(handler):
    """Refuse everything from any chat but the configured one, and say so once."""

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not authorised(update):
            logger.warning(
                "rejected update from unauthorised chat %s",
                update.effective_chat.id if update.effective_chat else "unknown",
            )
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Этот бот приватный и работает только со своим владельцем."
                )
            return
        return await handler(update, context)

    return wrapper


async def _reply(update: Update, text: str, **kwargs) -> None:
    target = update.effective_message
    if target is not None:
        await target.reply_text(
            text[:4096], parse_mode=ParseMode.HTML,
            disable_web_page_preview=True, **kwargs
        )


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

@guard
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, (
        "<b>rabotaBot</b>\n"
        "Собирает вакансии, отсеивает мусор, ранжирует и пишет черновики.\n"
        "<b>Ничего не отправляет сам.</b> Отправляете вы.\n\n"
        "/queue — очередь\n"
        "/stats — состояние конвейера\n"
        "/drafts — черновики\n"
        "/applications — трекер откликов\n"
        "/budget — расход LLM и лимит отправок\n"
        "/sources — источники\n"
        "/pause, /resume — остановить и вернуть сбор"
    ))


@guard
async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.bot.cards import render_card, _card_keyboard
    from app.db import session_scope
    from app.services.queue import digest_candidates

    def work():
        with session_scope() as db:
            return digest_candidates(db, limit=5)

    rows = await asyncio.to_thread(work)
    if not rows:
        await _reply(update, "Очередь пуста. Либо всё отработано, либо сбор ещё не прошёл.")
        return
    for row in rows:
        from telegram import InlineKeyboardMarkup

        await update.effective_message.reply_text(
            render_card(row)[:4096],
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup.de_json(
                _card_keyboard(row["cluster_id"]), context.bot
            ),
        )


@guard
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.db import session_scope
    from app.services.pipeline import pipeline_stats
    from app.services.queue import remaining_this_week, sends_this_week

    def work():
        with session_scope() as db:
            return pipeline_stats(db), sends_this_week(db), remaining_this_week(db)

    stats, sent, remaining = await asyncio.to_thread(work)
    await _reply(update, (
        f"<b>Конвейер</b>\n"
        f"вакансий: {stats['postings']}\n"
        f"уникальных (после дедупа): {stats['clusters']}\n"
        f"отсеяно жёсткими фильтрами: {stats['gated_postings']}\n"
        f"оценено: {stats['scored']}\n"
        f"проверено судьёй: {stats['judged']}\n\n"
        f"отправлено на этой неделе: {sent}, осталось {remaining}"
    ))


@guard
async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.db import session_scope
    from app.services.llm import calls_today, health
    from app.services.queue import remaining_this_week

    def work():
        with session_scope() as db:
            return remaining_this_week(db)

    remaining = await asyncio.to_thread(work)
    gw = health()
    await _reply(update, (
        f"<b>Бюджет</b>\n"
        f"LLM сегодня: {calls_today()} / {settings.LLM_DAILY_CALL_CAP}\n"
        f"гейтвей: {'доступен' if gw.get('reachable') else 'НЕДОСТУПЕН'}\n"
        f"отправок осталось на неделе: {remaining}"
    ))


@guard
async def cmd_drafts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.db import session_scope
    from app.models import Draft
    from sqlalchemy import select

    def work():
        with session_scope() as db:
            rows = db.execute(
                select(Draft).where(Draft.status.in_(("draft", "approved")))
                .order_by(Draft.created_at.desc()).limit(10)
            ).scalars().all()
            return [(d.id, d.status, d.template, d.cluster_id) for d in rows]

    rows = await asyncio.to_thread(work)
    if not rows:
        await _reply(update, "Открытых черновиков нет.")
        return
    lines = ["<b>Черновики</b>"]
    for did, status, template, cluster_id in rows:
        lines.append(f"#{did} · {status} · {template} · кластер {cluster_id}")
    lines.append("\n/draft &lt;id&gt; — показать текст")
    await _reply(update, "\n".join(lines))


@guard
async def cmd_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.bot.cards import push_draft_card
    from app.db import session_scope

    if not context.args:
        await _reply(update, "Использование: /draft &lt;id&gt;")
        return
    try:
        draft_id = int(context.args[0])
    except ValueError:
        await _reply(update, "id должен быть числом")
        return

    def work():
        with session_scope() as db:
            return push_draft_card(db, draft_id)

    if not await asyncio.to_thread(work):
        await _reply(update, "Черновик не найден.")


@guard
async def cmd_applications(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.db import session_scope
    from app.models import Application, JobCluster
    from app.services.queue import _canonical
    from sqlalchemy import select

    def work():
        with session_scope() as db:
            rows = db.execute(
                select(Application).where(Application.closed_at.is_(None))
                .order_by(Application.stage_entered_at.desc()).limit(15)
            ).scalars().all()
            out = []
            for row in rows:
                cluster = db.get(JobCluster, row.cluster_id)
                posting = _canonical(db, cluster) if cluster else None
                out.append((row.id, row.stage, posting.title if posting else "?",
                            posting.company_name if posting else ""))
            return out

    rows = await asyncio.to_thread(work)
    if not rows:
        await _reply(update, "Открытых откликов нет.")
        return
    lines = ["<b>Отклики в работе</b>"]
    for aid, stage, title, company in rows:
        lines.append(f"#{aid} · {stage} · {title[:44]} · {company[:24]}")
    lines.append("\n/outcome &lt;id&gt; &lt;stage&gt; — обновить статус")
    await _reply(update, "\n".join(lines))


@guard
async def cmd_outcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.db import session_scope
    from app.models import Application
    from app.models.enums import ALL_STAGES
    from app.services.queue import advance

    if len(context.args) < 2:
        await _reply(update, "Использование: /outcome &lt;id&gt; &lt;stage&gt;\n"
                             f"stage: {', '.join(ALL_STAGES)}")
        return
    try:
        app_id = int(context.args[0])
    except ValueError:
        await _reply(update, "id должен быть числом")
        return
    stage = context.args[1]
    if stage not in ALL_STAGES:
        await _reply(update, f"Неизвестный stage. Допустимые: {', '.join(ALL_STAGES)}")
        return

    def work():
        with session_scope() as db:
            row = db.get(Application, app_id)
            if row is None:
                return False
            advance(db, row, stage, actor="human")
            return True

    ok = await asyncio.to_thread(work)
    await _reply(update, f"Отклик #{app_id} → {stage}" if ok else "Отклик не найден.")


@guard
async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from app.db import session_scope
    from app.models import Source
    from sqlalchemy import select

    def work():
        with session_scope() as db:
            rows = db.execute(select(Source).order_by(Source.key)).scalars().all()
            return [(s.key, s.enabled, s.consecutive_failures, s.last_ok_at) for s in rows]

    rows = await asyncio.to_thread(work)
    healthy = [r for r in rows if r[1] and r[2] == 0]
    broken = [r for r in rows if not r[1] or r[2] > 0]
    lines = [f"<b>Источники</b>: {len(healthy)} в порядке, {len(broken)} с проблемами"]
    for key, enabled, failures, _ in broken[:15]:
        state = "выключен" if not enabled else f"{failures} ошибок подряд"
        lines.append(f"  ⚠ {key}: {state}")
    await _reply(update, "\n".join(lines))


@guard
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_paused(update, True)


@guard
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_paused(update, False)


async def _set_paused(update: Update, paused: bool) -> None:
    from app.db import session_scope
    from app.models import Setting

    def work():
        with session_scope() as db:
            row = db.get(Setting, "paused")
            if row is None:
                row = Setting(key="paused", value={"paused": paused})
                db.add(row)
            else:
                row.value = {"paused": paused}

    await asyncio.to_thread(work)
    await _reply(
        update,
        "⏸ Сбор остановлен. /resume чтобы вернуть."
        if paused else "▶️ Сбор возобновлён.",
    )


# --------------------------------------------------------------------------
# Inline buttons
# --------------------------------------------------------------------------

SKIP_REASONS = [
    ("wrong_stack", "не тот стек"), ("geo", "география"), ("comp", "деньги"),
    ("too_junior", "слишком джун"), ("too_senior", "слишком сеньор"),
    ("company", "компания"), ("gut", "просто нет"),
]


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    if not authorised(update):
        await query.answer("Недоступно.", show_alert=True)
        return
    await query.answer()

    action, _, raw_id = (query.data or "").partition(":")
    try:
        entity_id = int(raw_id)
    except ValueError:
        return

    handlers = {
        "draft": _cb_draft, "why": _cb_why, "skip": _cb_skip_menu,
        "reason": None, "block": _cb_block, "approve": _cb_approve,
        "sent": _cb_sent, "discard": _cb_discard, "regen": _cb_regen,
    }
    if action == "reason":
        return  # handled by the reason: prefix below
    if (query.data or "").startswith("reason:"):
        return

    handler = handlers.get(action)
    if handler is not None:
        await handler(update, context, entity_id)


async def on_reason_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not authorised(update):
        return
    await query.answer()
    _, _, payload = (query.data or "").partition(":")
    code, _, raw_id = payload.partition(":")
    try:
        cluster_id = int(raw_id)
    except ValueError:
        return

    from app.db import session_scope
    from app.services.queue import skip

    def work():
        with session_scope() as db:
            skip(db, cluster_id, code)

    await asyncio.to_thread(work)
    await query.edit_message_text(
        f"⏭ Пропущено ({code}). Три одинаковые причины подряд попадут "
        f"в воскресный отчёт как предложение нового фильтра."
    )


async def _cb_draft(update, context, cluster_id: int) -> None:
    from app.bot.cards import push_draft_card
    from app.db import session_scope
    from app.services.queue import create_draft

    await update.callback_query.edit_message_reply_markup(reply_markup=None)
    await _reply(update, "Генерирую черновик…")

    def work():
        with session_scope() as db:
            draft = create_draft(db, cluster_id)
            if draft is None:
                return None
            db.commit()
            push_draft_card(db, draft.id)
            return draft.id

    draft_id = await asyncio.to_thread(work)
    if draft_id is None:
        await _reply(update, "Не получилось: гейтвей недоступен или исчерпан дневной лимит.")


async def _cb_why(update, context, cluster_id: int) -> None:
    from app.db import session_scope
    from app.models import Score
    from sqlalchemy import select

    def work():
        with session_scope() as db:
            score = db.execute(
                select(Score).where(Score.cluster_id == cluster_id)
                .order_by(Score.priority.desc()).limit(1)
            ).scalar_one_or_none()
            return score.explain if score else None

    explain = await asyncio.to_thread(work)
    await _reply(update, f"<pre>{explain}</pre>" if explain else "Разбора нет.")


async def _cb_skip_menu(update, context, cluster_id: int) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    buttons = [
        [InlineKeyboardButton(label, callback_data=f"reason:{code}:{cluster_id}")]
        for code, label in SKIP_REASONS
    ]
    await update.callback_query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def _cb_block(update, context, cluster_id: int) -> None:
    from app.db import session_scope
    from app.models import Company, JobCluster
    from app.services.queue import skip

    def work():
        with session_scope() as db:
            cluster = db.get(JobCluster, cluster_id)
            name = None
            if cluster and cluster.company_id:
                company = db.get(Company, cluster.company_id)
                if company:
                    company.blocklisted = True
                    name = company.name
            skip(db, cluster_id, "company")
            return name

    name = await asyncio.to_thread(work)
    await update.callback_query.edit_message_text(
        f"🚫 {name or 'Компания'} в чёрном списке, её вакансии больше не показываются."
    )


async def _cb_approve(update, context, draft_id: int) -> None:
    from app.db import session_scope
    from app.models import Draft
    from app.services.queue import approve

    def work():
        with session_scope() as db:
            draft = db.get(Draft, draft_id)
            if draft is None:
                return False
            approve(db, draft)
            return True

    ok = await asyncio.to_thread(work)
    await _reply(update, (
        "✅ Одобрено. Токен выдан на час.\n"
        "<b>Бот не отправляет.</b> Откройте форму, вставьте текст, отправьте сами, "
        "затем нажмите «Отправил»."
    ) if ok else "Черновик не найден.")


async def _cb_sent(update, context, draft_id: int) -> None:
    from app.db import session_scope
    from app.models import Draft
    from app.services.queue import mark_sent

    def work():
        with session_scope() as db:
            draft = db.get(Draft, draft_id)
            if draft is None:
                return "not_found"
            try:
                mark_sent(db, draft)
            except PermissionError as exc:
                return str(exc)
            except ValueError as exc:
                return str(exc)
            return "ok"

    result = await asyncio.to_thread(work)
    if result == "ok":
        await _reply(update, "📤 Записал. Напомню на 4-5 день, закрою на 10-й.")
    elif result == "not_found":
        await _reply(update, "Черновик не найден.")
    else:
        await _reply(update, f"Не записал: {result}")


async def _cb_discard(update, context, draft_id: int) -> None:
    from datetime import datetime, timezone

    from app.db import session_scope
    from app.models import Draft

    def work():
        with session_scope() as db:
            draft = db.get(Draft, draft_id)
            if draft is not None:
                draft.status = "discarded"
                draft.discarded_at = datetime.now(timezone.utc)

    await asyncio.to_thread(work)
    await update.callback_query.edit_message_text("❌ Черновик удалён.")


async def _cb_regen(update, context, draft_id: int) -> None:
    from app.bot.cards import push_draft_card
    from app.db import session_scope
    from app.models import Draft
    from app.services.queue import create_draft

    def work():
        with session_scope() as db:
            old = db.get(Draft, draft_id)
            if old is None:
                return None
            old.status = "discarded"
            draft = create_draft(db, old.cluster_id, old.template)
            if draft is None:
                return None
            db.commit()
            push_draft_card(db, draft.id)
            return draft.id

    new_id = await asyncio.to_thread(work)
    if new_id is None:
        await _reply(update, "Перегенерировать не вышло: гейтвей недоступен.")


def build_application() -> TgApplication:
    app = TgApplication.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("budget", cmd_budget))
    app.add_handler(CommandHandler("drafts", cmd_drafts))
    app.add_handler(CommandHandler("draft", cmd_draft))
    app.add_handler(CommandHandler("applications", cmd_applications))
    app.add_handler(CommandHandler("outcome", cmd_outcome))
    app.add_handler(CommandHandler("sources", cmd_sources))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CallbackQueryHandler(on_reason_callback, pattern=r"^reason:"))
    app.add_handler(CallbackQueryHandler(on_callback))
    return app


def main() -> None:
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set; the bot cannot start")
        raise SystemExit(1)
    if not settings.TELEGRAM_CHAT_ID:
        logger.error(
            "TELEGRAM_CHAT_ID is not set. Refusing to start: without it the bot "
            "would accept commands from anyone who finds it."
        )
        raise SystemExit(1)
    logger.info("telegram bot starting (long polling)")
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
