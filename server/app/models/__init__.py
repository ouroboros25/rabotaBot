"""SQLAlchemy models. Importing this package registers every table on Base."""
from app.models.base import Base, TimestampMixin
from app.models.application import (
    Application, ApplicationEvent, SkipFeedback,
)
from app.models.company import Company, CompanyAtsSlug
from app.models.draft import ApprovalToken, Draft, DraftCheck, DraftVersion
from app.models.posting import EligibilityFlag, JobCluster, JobPosting
from app.models.profile import Profile, ProfileFact, ProfileVariant, Setting
from app.models.score import Score, SourcePrior
from app.models.source import RawDocument, Source, SourceRun

__all__ = [
    "Base", "TimestampMixin",
    "Source", "SourceRun", "RawDocument",
    "Company", "CompanyAtsSlug",
    "JobPosting", "JobCluster", "EligibilityFlag",
    "Score", "SourcePrior",
    "Draft", "DraftVersion", "DraftCheck", "ApprovalToken",
    "Application", "ApplicationEvent", "SkipFeedback",
    "Profile", "ProfileVariant", "ProfileFact", "Setting",
]
