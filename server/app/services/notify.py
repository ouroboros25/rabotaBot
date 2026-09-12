"""Outbound Telegram messaging used by workers.

The bot process owns interactive handlers; workers only push. Both refuse to
talk to any chat other than the configured one.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


def _enabled() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)


def send_message(
    text: str, reply_markup: dict | None = None, parse_mode: str | None = "HTML"
) -> bool:
    if not _enabled():
        logger.info("telegram disabled (missing token or chat id); message dropped")
        return False
    payload: dict = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text[:4096],
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                API.format(token=settings.TELEGRAM_BOT_TOKEN, method="sendMessage"),
                json=payload,
            )
        if resp.status_code >= 400:
            logger.warning("telegram sendMessage failed: HTTP %s", resp.status_code)
            return False
        return True
    except httpx.HTTPError as exc:
        logger.warning("telegram sendMessage error: %s", type(exc).__name__)
        return False


def send_document(filename: str, content: bytes, caption: str = "") -> bool:
    if not _enabled():
        return False
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                API.format(token=settings.TELEGRAM_BOT_TOKEN, method="sendDocument"),
                data={"chat_id": settings.TELEGRAM_CHAT_ID, "caption": caption[:1000]},
                files={"document": (filename, content)},
            )
        return resp.status_code < 400
    except httpx.HTTPError as exc:
        logger.warning("telegram sendDocument error: %s", type(exc).__name__)
        return False
