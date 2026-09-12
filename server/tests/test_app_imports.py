"""Import smoke test.

A duplicated decorator got through review and took the production API down: the
module only fails at import time, and nothing in the suite imported it. This is
the cheapest possible guard against that whole class of mistake.
"""


def test_app_imports_and_registers_its_routes():
    import app.main
    from fastapi.routing import APIRoute

    paths = {r.path for r in app.main.app.routes if isinstance(r, APIRoute)}
    for expected in (
        "/healthz", "/dashboard", "/jobs", "/jobs/facets", "/jobs/reapply",
        "/drafts", "/applications", "/profile", "/profile/search-filters",
        "/sources",
    ):
        assert expected in paths, f"route disappeared: {expected}"


def test_every_module_imports():
    """Catches syntax and import-time errors anywhere in the package."""
    import importlib
    import pkgutil

    import app

    failures = []
    for mod in pkgutil.walk_packages(app.__path__, prefix="app."):
        # The bot opens a long-polling client at import of __main__ only, so the
        # module itself is safe to import; nothing here starts a network client.
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # noqa: BLE001 - reporting is the point
            failures.append(f"{mod.name}: {type(exc).__name__}: {exc}")
    assert not failures, "modules failed to import:\n" + "\n".join(failures)


def test_celery_tasks_register():
    from app.workers.celery_app import celery_app

    names = set(celery_app.tasks)
    for expected in (
        "rabota.ingest.sweep", "rabota.ingest.gates", "rabota.score.batch",
        "rabota.judge.top", "rabota.digest.daily", "rabota.followups.check",
    ):
        assert expected in names, f"task not registered: {expected}"
