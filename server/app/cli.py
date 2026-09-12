"""Small operational CLI: python -m app.cli <command>

Commands
  seed       seed sources + profile from config/ (idempotent, runs on prod start)
  ingest     run every due source once, synchronously
  gates      apply hard gates to ungated postings
  score      run the deterministic scorer
  judge      run the LLM judge over the top of the queue
  digest     print what the next digest would contain
  draft [id] generate a draft for one cluster (default: top of the queue)
  stats      pipeline counters
  selftest   end-to-end check of gateway, telegram and one live source
"""
from __future__ import annotations

import json
import sys

from app.db import session_scope
from app.logging_conf import setup_logging


def _seed() -> None:
    from app.services.bootstrap import run

    with session_scope() as db:
        print(json.dumps(run(db), indent=2))


def _ingest() -> None:
    from app.services.ingest import due_sources, run_source

    with session_scope() as db:
        sources = due_sources(db)
        print(f"{len(sources)} source(s) due")
        for source in sources:
            run = run_source(db, source)
            db.commit()
            print(f"  {source.key}: seen={run.items_seen} new={run.items_new} "
                  f"err={run.error or '-'}")


def _gates() -> None:
    from app.services.ingest import apply_gates

    with session_scope() as db:
        print(json.dumps(apply_gates(db), indent=2))


def _score() -> None:
    from app.services.pipeline import score_batch

    with session_scope() as db:
        print(json.dumps(score_batch(db), indent=2))


def _judge() -> None:
    from app.services.pipeline import judge_top

    with session_scope() as db:
        print(json.dumps(judge_top(db), indent=2))


def _digest() -> None:
    from app.services.queue import digest_candidates

    with session_scope() as db:
        for row in digest_candidates(db):
            print(f"{row['priority']:7.0f}  {row['title'][:60]:60}  "
                  f"{(row['company'] or '-')[:24]:24}  {row['source_key']}")


def _draft() -> None:
    """python -m app.cli draft [cluster_id] - defaults to the top of the queue."""
    from app.services.queue import create_draft, digest_candidates, latest_version

    cluster_id = int(sys.argv[2]) if len(sys.argv) > 2 else None
    with session_scope() as db:
        if cluster_id is None:
            rows = digest_candidates(db, limit=1)
            if not rows:
                print("queue is empty")
                return
            cluster_id = rows[0]["cluster_id"]
            print(f"top of queue: cluster {cluster_id} - {rows[0]['title']}")
        draft = create_draft(db, cluster_id)
        if draft is None:
            print("draft generation failed (gateway unavailable or no profile)")
            return
        db.commit()
        version = latest_version(db, draft)
        print(f"\n--- draft {draft.id} v{version.version} "
              f"({draft.template}, {version.word_count} words, "
              f"model={version.model}) ---")
        if version.subject:
            print(f"Subject: {version.subject}")
        print(version.body)
        print("\n--- checks ---")
        for check in version.checks:
            mark = "PASS" if check.passed else "FAIL"
            print(f"  [{mark}] {check.check}: {check.detail}")
        print(f"\nfact keys cited: {version.fact_keys}")
        print(f"open questions: {version.open_questions}")


def _stats() -> None:
    from app.services.pipeline import pipeline_stats

    with session_scope() as db:
        print(json.dumps(pipeline_stats(db), indent=2))


def _selftest() -> None:
    from app.connectors import build
    from app.models import Source
    from app.services import llm, notify
    from sqlalchemy import select

    print("gateway:", json.dumps(llm.health(), indent=2))
    print("telegram:", "configured" if notify._enabled() else "NOT configured")
    with session_scope() as db:
        source = db.execute(
            select(Source).where(Source.key == "himalayas")
        ).scalar_one_or_none()
        if source is None:
            print("source himalayas not seeded; run `seed` first")
            return
        result = build(source).fetch()
        print(f"himalayas: {len(result.items)} items, {result.bytes_fetched} bytes")
        if result.items:
            first = result.items[0]
            print(f"  sample: {first.title!r} @ {first.company_name!r} "
                  f"geo={first.countries_allowed}")


COMMANDS = {
    "seed": _seed, "ingest": _ingest, "gates": _gates, "score": _score,
    "judge": _judge, "digest": _digest, "draft": _draft, "stats": _stats,
    "selftest": _selftest,
}


def main() -> int:
    setup_logging()
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        return 1
    COMMANDS[sys.argv[1]]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
