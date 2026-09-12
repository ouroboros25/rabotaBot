# rabotaBot — repository rules

Semi-automatic job and project hunting for one developer. Collects postings from
free public sources, filters hard, ranks, drafts outreach, and stops. A human
sends. Read this before changing anything.

## The three constraints that shape every decision

1. **The bot never transmits.** No ATS submission, no email send, no LinkedIn
   action, no Upwork proposal. Not a policy: there is no candidate-side
   credential for Greenhouse or Lever apply endpoints, and a silently malformed
   application burns the employer relationship permanently. The action broker
   produces artifacts (`.docx`, clipboard text, a deep link) and nothing else.
2. **Volume is a penalty, not a lever.** Measured conversion falls sharply as
   application count rises, and mass-apply tools land at 0.4-0.6% against a
   2-3% market baseline. `weekly_send_cap` is the product, not a limitation.
   If you find yourself raising it to "get more coverage", stop.
3. **Nothing is claimed that cannot be sourced.** The drafting engine may only
   select, order and rephrase facts from the ledger. This is enforced by
   `services/factguard.py` after generation, never by asking the model nicely.

## Layout

```
config/           sources.yaml, rubric.yaml, profile.example.yaml  (the tunables)
server/app/
  connectors/     7 protocol families. A new SOURCE is a YAML row, not code.
  services/       the pipeline: ingest -> dedup -> gates -> scoring -> drafting
  api/            FastAPI routers consumed by the SPA
  bot/            Telegram long-polling process (cards + handlers)
  workers/        Celery tasks and beat schedule
web/src/          React SPA mounted at /rabota/
```

## Hard rules

- **Never add a tier-4 source.** LinkedIn (any access), Indeed/Glassdoor/Upwork
  UI scraping, CAPTCHA-solving services, rotating residential proxies, prompt
  injection in the CV, purchased contact data. `configload.load_sources` raises
  on `legal_tier: 4` deliberately. The reasons are legal, not squeamish:
  DMCA 1201 damages run $200-2500 per act of circumvention, and LinkedIn's UA
  bans automation in four separate clauses.
- **Job descriptions are untrusted input.** Into a prompt only via
  `llm.wrap_untrusted`. Into the UI only as text, never as HTML. A posting that
  addresses the model sets `injection_suspected`, which demotes it rather than
  hiding it.
- **Every outbound URL goes through `text.is_public_http_url`** (SSRF guard:
  scheme allowlist, RFC1918 / link-local / metadata blocked).
- **Model-supplied numbers are clamped** via `scoring.clamp01`. Free models
  answer a 0..1 field with `3.0` regularly; unclamped that silently reorders the
  whole digest. This was a real bug, not a hypothetical.
- **Pin no model.** `LLM_MODEL_*` default to `auto`. Naming a provider inherits
  its cooldown and produces 503 "No available providers"; the gateway exists to
  route around that.
- **The approval token is the security boundary.** `mark_sent` requires a live,
  unconsumed HMAC token whose hash matches the current draft bytes. Editing
  invalidates it. There is no bulk-approve and there must never be one.
- **No internal auth by design.** The app sits behind Traefik `server-auth@file`
  (Telegram 2FA). Never publish a router without that middleware.
- **TDD on bugs.** Every bug found here gets a failing test first. The suite in
  `server/tests/` is almost entirely regressions from live runs; keep it that way.

## Working on it

```bash
docker compose up -d --build
docker compose exec rabota-api alembic upgrade head
docker compose exec rabota-api python -m app.cli seed
docker compose exec rabota-api python -m app.cli selftest   # gateway + one live source
docker compose exec rabota-api python -m pytest -q
```

CLI verbs: `seed ingest gates score judge digest draft stats selftest`.

Schema changes: edit the models, then
`alembic revision --autogenerate -m "..."`. Never hand-write the initial tables.

## Deployment

`~/apps/rabotaBot` on the mini server, served at **https://yefrix.uk/rabota/**
(the duckdns name resolves to the same address and the routers match both, but
`yefrix.uk` is the canonical host). See the root `README.md`. The prod
overlay adds Traefik labels, drops host ports, and runs migrations plus `seed`
on start, so a new source in `sources.yaml` is live after a redeploy.

## Calibration constants worth knowing

- `scoring.SEMANTIC_FULL_MATCH = 0.30` — TF-IDF cosine, not embedding cosine.
  Derived from a 5,349-posting corpus. Re-derive if the similarity function changes.
- `priors.SEED_RATES` — per-board reply rates seeded from measured
  application-to-interview data, so day-one ranking is informed rather than flat.
- `gates.stale_after_days = 45`, `follow_up_after_days = 4`,
  `presumed_dead_after_days = 10` — the last two come from the same fact: median
  time-to-archive for a non-interviewed candidate is about six days.
