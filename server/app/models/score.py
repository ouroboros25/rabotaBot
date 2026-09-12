from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Score(Base):
    __tablename__ = "score"
    __table_args__ = (UniqueConstraint("cluster_id", "track", name="uq_score_cluster_track"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("job_cluster.id", ondelete="CASCADE"), index=True
    )
    track: Mapped[str] = mapped_column(String(16), nullable=False)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Deterministic stage
    semantic: Mapped[float] = mapped_column(Float, default=0.0)
    skill_coverage: Mapped[float] = mapped_column(Float, default=0.0)
    title_fit: Mapped[float] = mapped_column(Float, default=0.0)
    freshness: Mapped[float] = mapped_column(Float, default=0.0)
    comp_fit: Mapped[float] = mapped_column(Float, default=0.0)
    source_prior: Mapped[float] = mapped_column(Float, default=0.0)
    ghost_risk: Mapped[float] = mapped_column(Float, default=0.0)
    crowding: Mapped[float] = mapped_column(Float, default=0.0)
    warm_path: Mapped[float] = mapped_column(Float, default=0.0)
    s_fast: Mapped[float] = mapped_column(Float, default=0.0, index=True)

    # LLM stage (nullable until the judge runs)
    llm_model: Mapped[str | None] = mapped_column(String(120))
    llm_fit: Mapped[float | None] = mapped_column(Float)
    llm_verdict: Mapped[dict | None] = mapped_column(JSONB)
    llm_evidence: Mapped[list | None] = mapped_column(JSONB)
    # The judge is told to flag anything in the posting that reads as an
    # instruction aimed at it. Job descriptions are attacker-controllable text.
    injection_suspected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    fit: Mapped[float] = mapped_column(Float, default=0.0)
    trust: Mapped[float] = mapped_column(Float, default=0.0)
    reach: Mapped[float] = mapped_column(Float, default=0.0)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    priority: Mapped[float] = mapped_column(Float, default=0.0, index=True)

    features: Mapped[dict | None] = mapped_column(JSONB)
    explain: Mapped[str | None] = mapped_column(Text)


class SourcePrior(Base):
    """Beta-Binomial reply-rate posterior per (source, track).

    Seeded from measured per-board interview rates so day-one priors are informed
    rather than uniform: source choice alone swings conversion by up to 50x, which
    is a larger effect than anything else we can model.
    """

    __tablename__ = "source_prior"
    __table_args__ = (UniqueConstraint("source_id", "track", name="uq_prior_source_track"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"))
    track: Mapped[str] = mapped_column(String(16), nullable=False)
    alpha: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    beta: Mapped[float] = mapped_column(Float, default=30.0, nullable=False)
    sends: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    replies: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def posterior_mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)
