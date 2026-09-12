from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Draft(Base, TimestampMixin):
    __tablename__ = "draft"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("job_cluster.id", ondelete="CASCADE"), index=True
    )
    score_id: Mapped[int | None] = mapped_column(ForeignKey("score.id", ondelete="SET NULL"))
    template: Mapped[str] = mapped_column(String(32), nullable=False)
    track: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False, index=True)
    # draft | approved | sent | discarded
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    versions: Mapped[list["DraftVersion"]] = relationship(
        back_populates="draft", cascade="all, delete-orphan", order_by="DraftVersion.version"
    )


class DraftVersion(Base):
    __tablename__ = "draft_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("draft.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    paragraphs: Mapped[list | None] = mapped_column(JSONB)
    fact_keys: Mapped[list | None] = mapped_column(ARRAY(String(16)))
    jd_hooks: Mapped[list | None] = mapped_column(JSONB)
    open_questions: Mapped[list | None] = mapped_column(JSONB)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str | None] = mapped_column(String(120))
    passed_all_checks: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    edited_by_human: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    draft: Mapped[Draft] = relationship(back_populates="versions")
    checks: Mapped[list["DraftCheck"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )


class DraftCheck(Base):
    """Deterministic post-generation gate.

    factguard | banlist | novelty | keywords | length | specificity | jd_hook
    """

    __tablename__ = "draft_check"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_version_id: Mapped[int] = mapped_column(
        ForeignKey("draft_version.id", ondelete="CASCADE"), index=True
    )
    check: Mapped[str] = mapped_column(String(32), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)

    version: Mapped[DraftVersion] = relationship(back_populates="checks")


class ApprovalToken(Base):
    """HMAC token bound to the exact bytes the human approved.

    The artifact writer refuses to emit anything without a valid, unexpired,
    unconsumed token whose hash matches the current draft text. Consequences:
    a prompt-injection payload cannot manufacture an approval, a scheduler bug
    cannot mass-emit, and editing after approval forces re-approval.
    There is deliberately no bulk-approve path.
    """

    __tablename__ = "approval_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_version_id: Mapped[int] = mapped_column(
        ForeignKey("draft_version.id", ondelete="CASCADE"), index=True
    )
    token_hmac: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[str] = mapped_column(String(32), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_via: Mapped[str] = mapped_column(String(16), default="telegram", nullable=False)
