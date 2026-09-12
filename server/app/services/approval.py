"""HMAC approval tokens.

The artifact writer refuses to run without a valid, unexpired, unconsumed token
whose body hash matches the draft byte for byte. That single check buys:

  - a prompt-injection payload cannot manufacture an approval;
  - a scheduler bug cannot mass-emit;
  - editing a draft after approval invalidates the token and forces re-approval.

There is deliberately no bulk-approve path. Bulk approval would defeat both the
purpose and the defensibility: the whole design rests on a qualified human
reviewing each item before it leaves.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApprovalToken, DraftVersion, Setting

TOKEN_TTL_MINUTES = 60
_SERVER_KEY_SETTING = "approval_hmac_key"


def _server_key(db: Session) -> bytes:
    """Per-installation key, generated on first use and stored in the DB.

    Kept out of .env on purpose: rotating the app config must not silently
    invalidate pending approvals, and this key never leaves the database.
    """
    row = db.get(Setting, _SERVER_KEY_SETTING)
    if row is None:
        row = Setting(key=_SERVER_KEY_SETTING, value={"key": secrets.token_hex(32)})
        db.add(row)
        db.flush()
    return bytes.fromhex(row.value["key"])


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def issue(db: Session, version: DraftVersion, via: str = "telegram") -> ApprovalToken:
    nonce = secrets.token_hex(16)
    bh = body_hash(version.body)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=TOKEN_TTL_MINUTES)
    mac = hmac.new(
        _server_key(db),
        f"{version.id}|{bh}|{nonce}|{expires.isoformat()}".encode(),
        hashlib.sha256,
    ).hexdigest()

    token = ApprovalToken(
        draft_version_id=version.id,
        token_hmac=mac,
        body_sha256=bh,
        nonce=nonce,
        issued_at=now,
        expires_at=expires,
        issued_via=via,
    )
    db.add(token)
    db.flush()
    return token


def verify_and_consume(db: Session, version: DraftVersion) -> ApprovalToken:
    """Raise unless a live token matches the CURRENT body of this version."""
    token = db.execute(
        select(ApprovalToken)
        .where(
            ApprovalToken.draft_version_id == version.id,
            ApprovalToken.consumed_at.is_(None),
        )
        .order_by(ApprovalToken.issued_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if token is None:
        raise PermissionError("no approval token: this draft was never approved")

    now = datetime.now(timezone.utc)
    expires = token.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < now:
        raise PermissionError("approval token expired; approve again")

    expected = hmac.new(
        _server_key(db),
        f"{version.id}|{token.body_sha256}|{token.nonce}|{expires.isoformat()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, token.token_hmac):
        raise PermissionError("approval token signature mismatch")

    if token.body_sha256 != body_hash(version.body):
        raise PermissionError("draft text changed after approval; approve again")

    token.consumed_at = now
    return token
