"""What this instance actually is, and whether its schema matches its code.

Three questions an operator asks during an upgrade, in one place:

* **Which version am I running?** Until now the answer lived only in
  ``TRELLUM_VERSION_BRANCH`` — the release tag was passed to the image build as
  ``GIT_BRANCH``, so ``/api/version`` reported a release under a key named
  "branch" and nothing in the code knew its own version.
* **Which framework built my reports?** The portal and framework share one
  product version and release tag.
* **Did the migration actually apply?** ``migrate`` runs in its own one-shot
  container. Nothing downstream noticed when it failed: a web container serving
  against a stale schema reported perfectly healthy.

``schema_state()`` is the answer to the third, in one word, so ``upgrade.sh``
and any external monitor can assert on it.
"""
from __future__ import annotations

from django.conf import settings

import trellum_portal
from trellum.licensing import source_url as framework_source_url

#: Schema states, in the order an upgrade moves through them.
SCHEMA_OK = "ok"
SCHEMA_PENDING = "pending"
SCHEMA_ERROR = "error"


def framework_tag() -> str:
    """Compatibility alias for clients that still read ``framework.tag``."""
    return f"v{trellum_portal.__version__}"


def source_url() -> str:
    return framework_source_url(
        trellum_portal.__version__,
        override=getattr(settings, "TRELLUM_SOURCE_URL", ""),
    )


def meta_schema_version():
    """The framework's output-format version.

    This is the number that decides whether already-built report output can
    still be read, so it belongs next to the tag rather than buried in the
    framework health check.
    """
    try:
        from trellum.meta import META_SCHEMA_VERSION

        return META_SCHEMA_VERSION
    except Exception:  # noqa: BLE001
        return None


def unapplied_migrations() -> list[str]:
    """``["app.0004_name", ...]`` for every migration the database lacks."""
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connections["default"])
    targets = executor.loader.graph.leaf_nodes()
    return [f"{m.app_label}.{m.name}" for m, _backwards in executor.migration_plan(targets)]


def schema_state() -> tuple[str, list[str]]:
    """``(state, unapplied)``. Never raises — an unreachable database is an
    answer ("error"), not a traceback on a status endpoint."""
    try:
        pending = unapplied_migrations()
    except Exception:  # noqa: BLE001
        return SCHEMA_ERROR, []
    return (SCHEMA_PENDING if pending else SCHEMA_OK), pending


def version_info(*, detailed: bool = False) -> dict:
    """Identity for ``/api/version`` (public) and ``/system`` (operators).

    ``/api/version`` is unauthenticated — the compose healthcheck and built
    report pages depend on its exact path — so the public payload is identity
    only. Migration *names* describe the shape of the schema and are of no use
    to anyone entitled to less than the operator page, so they stay behind
    ``detailed``.
    """
    state, pending = schema_state()
    info = {
        "version": trellum_portal.__version__,
        "sha": settings.TRELLUM_VERSION_SHA,
        "build_time": settings.TRELLUM_VERSION_BUILD_TIME,
        "branch": settings.TRELLUM_VERSION_BRANCH,
        "source_url": source_url(),
        "framework": {"tag": framework_tag(), "meta_schema": meta_schema_version()},
        "schema": state,
    }
    if detailed:
        info["unapplied_migrations"] = pending
    return info
