"""FastAPI application.

Auth model, stated plainly because it is a deliberate trade-off: this app has no
internal login. It is single-user and sits behind Traefik's ``server-auth``
ForwardAuth middleware, which is Telegram 2FA with a 30-day session. Publishing
either router without that middleware exposes the CV, third-party contact data
and the whole pipeline to the internet. The startup check below refuses to stay
quiet about it.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    applications, dashboard, drafts, healthz, jobs, profile, sources,
)
from app.config import settings
from app.logging_conf import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="rabotaBot",
    version="1.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# The SPA is served same-origin through Traefik in production. CORS is only
# loosened for the Vite dev server on localhost.
if settings.APP_ENV != "production":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5186", "http://127.0.0.1:5186"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(healthz.router, tags=["health"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
app.include_router(drafts.router, prefix="/drafts", tags=["drafts"])
app.include_router(applications.router, prefix="/applications", tags=["applications"])
app.include_router(profile.router, prefix="/profile", tags=["profile"])
app.include_router(sources.router, prefix="/sources", tags=["sources"])


@app.on_event("startup")
def _startup() -> None:
    logger.info("rabotaBot api starting (env=%s)", settings.APP_ENV)
    if settings.APP_ENV == "production":
        logger.warning(
            "this service has no internal authentication; it MUST be published "
            "only behind the server-auth@file Traefik middleware"
        )
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.warning("telegram is not configured; digests and cards are disabled")
