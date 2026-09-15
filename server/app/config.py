"""Application settings.

Everything tunable lives here or in ``config/*.yaml``. Nothing tunable lives in
code, because the ranking criteria must be demonstrably the user's own (see
``config/rubric.yaml`` for why that matters contractually).
"""
from __future__ import annotations

import functools
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "development"
    APP_NAME: str = "rabotaBot"
    APP_PUBLIC_BASE: str = "/rabota"
    TZ: str = "Europe/Warsaw"
    LOG_LEVEL: str = "INFO"

    POSTGRES_HOST: str = "rabota-db"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "rabota"
    POSTGRES_USER: str = "rabota"
    POSTGRES_PASSWORD: str = "rabota"

    REDIS_HOST: str = "rabota-redis"
    REDIS_PORT: int = 6379

    # --- Smart AI Gateway (local, free, OpenAI-compatible) ---
    LLM_GATEWAY_URL: str = "http://gateway-nginx:80/v1"
    LLM_GATEWAY_API_KEY: str = ""
    LLM_MODEL_SCREEN: str = "auto"
    LLM_MODEL_JUDGE: str = "auto"
    LLM_MODEL_DRAFT: str = "auto"
    LLM_TIMEOUT_S: float = 90.0
    LLM_MAX_CONCURRENCY: int = 2
    # The gateway is shared with other apps on this server and runs on free
    # provider tiers. This cap is about being a good neighbour, not about money.
    LLM_DAILY_CALL_CAP: int = 400

    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_DIGEST_HOUR: int = 8
    # Push new matches as they are found, not only in the morning digest.
    PUSH_NEW_MATCHES: bool = True
    PUSH_MIN_PRIORITY: float = 0.0
    # Per run, so a big intake does not arrive as forty separate pings.
    PUSH_MAX_PER_RUN: int = 5
    # Local hours when nothing is pushed. Anything found during the quiet window
    # waits and goes out afterwards rather than being dropped.
    PUSH_QUIET_FROM: int = 23
    PUSH_QUIET_TO: int = 8
    TELEGRAM_RETRO_WEEKDAY: int = 6  # 0=Mon .. 6=Sun
    TELEGRAM_RETRO_HOUR: int = 18

    WEEKLY_SEND_CAP: int = 18
    DAILY_DIGEST_SIZE: int = 12
    JUDGE_TOP_K: int = 40
    DRAFT_MIN_PRIORITY: int = 520

    # Identified User-Agent with a reachable contact URL: the point of it is that
    # a publisher whose feed we poll can find out who we are, so it must carry
    # the canonical host rather than an alias.
    HTTP_USER_AGENT: str = (
        "rabotaBot/1.0 (+https://yefrix.uk/rabota; personal job search)"
    )
    HTTP_TIMEOUT_S: float = 25.0
    RESPECT_ROBOTS: bool = True

    ARTIFACT_DIR: str = "/var/lib/rabota/artifacts"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
