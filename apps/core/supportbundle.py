"""Diagnostics a customer can hand us without handing us their data.

When an on-prem install misbehaves we cannot look at it. The alternative to a
support bundle is a fortnight of "what does the log say?", so this collects the
things we always end up asking for, in one archive.

The redaction rule is the whole design: **allowlist, never blocklist.** A
blocklist ("strip anything called PASSWORD") fails the first time someone names
a variable something else, and the failure is silent and permanent — the
customer has already emailed us the file. So every field here is named
explicitly, and settings values are summarised (present/absent, host, length)
rather than copied.

Nothing in the bundle is tenant report data: no query results, no built output,
no datasource credentials.
"""
from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone as dt_tz
from urllib.parse import urlparse

from django.conf import settings

#: Settings safe to report verbatim: operational knobs, never secrets.
SAFE_SETTINGS = (
    "TRELLUM_STORAGE_BACKEND",
    "TRELLUM_REPORTS_BUCKET",
    "TRELLUM_RUNNER_ROLE",
    "TRELLUM_RUNNER_POOLS",
    "TRELLUM_RUNNER_MEMORY_BUDGET_MB",
    "TRELLUM_DEFAULT_JOB_MEMORY_MB",
    "TRELLUM_JOB_MEMORY_HEADROOM",
    "TRELLUM_JOB_MEMORY_ENFORCE",
    "TRELLUM_IMPERSONATION_MINUTES",
    "TRELLUM_ORG_SELF_SIGNUP",
    "TRELLUM_QUOTAS_ENABLED",
    "TRELLUM_IMPERSONATION_ENABLED",
    "TRELLUM_SSO_DOMAIN_VERIFICATION",
    "WORKER_MAX_CONCURRENT",
    "WORKER_CATCHUP",
    "WORKER_HEARTBEAT_SECONDS",
    "WORKER_REAP_SECONDS",
    "TRELLUM_VERSION_SHA",
    "TRELLUM_VERSION_BRANCH",
    "TRELLUM_VERSION_BUILD_TIME",
    "DEBUG",
    "DB_CONN_MAX_AGE",
    "TIME_ZONE",
)

#: Settings whose *presence* matters but whose value must never leave the site.
SECRET_SETTINGS = (
    "SESSION_SECRET_KEY",
    "SECRET_ENCRYPTION_KEY",
    "EMAIL_URL",
)


def _redact_url(raw: str) -> str:
    """Keep the shape of a URL, drop the credentials.

    Which host and port the portal talks to is exactly what we need; the
    password is exactly what we must not receive.
    """
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return "(unparseable)"
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    user = f"{parsed.username}@" if parsed.username else ""
    return f"{parsed.scheme}://{user}{host}{port}{parsed.path}"


def collect_settings() -> dict:
    out = {}
    for name in SAFE_SETTINGS:
        value = getattr(settings, name, None)
        out[name] = value if isinstance(value, (str, int, float, bool, list, type(None))) else str(value)

    for name in SECRET_SETTINGS:
        value = getattr(settings, name, "") or ""
        out[name] = {"set": bool(value), "length": len(str(value))}

    db = settings.DATABASES.get("default", {})
    out["DATABASE"] = {
        "engine": db.get("ENGINE", ""),
        "host": db.get("HOST", ""),
        "port": db.get("PORT", ""),
        "name": db.get("NAME", ""),
        # The password lives in DATABASES["default"]["PASSWORD"]; it is not
        # read here and must not be added.
    }
    out["ALLOWED_HOSTS"] = list(getattr(settings, "ALLOWED_HOSTS", []))
    out["PORTAL_BASE_URL"] = _redact_url(getattr(settings, "PORTAL_BASE_URL", ""))
    out["DATA_DIR"] = str(getattr(settings, "DATA_DIR", ""))
    return out


def collect_platform() -> dict:
    info = {
        "python": sys.version,
        "platform": platform.platform(),
        "processor_count": None,
    }
    try:
        import os

        info["processor_count"] = os.cpu_count()
    except Exception:  # noqa: BLE001
        pass
    try:
        import shutil

        usage = shutil.disk_usage(str(settings.DATA_DIR))
        info["data_volume"] = {
            "total_gb": round(usage.total / 1e9, 1),
            "used_gb": round(usage.used / 1e9, 1),
            "free_gb": round(usage.free / 1e9, 1),
        }
    except Exception as exc:  # noqa: BLE001
        info["data_volume"] = f"unavailable: {exc}"
    return info


def collect_fleet() -> dict:
    from apps.runner.models import WorkerHeartbeat

    return {
        "workers": [
            {
                "worker_id": w.worker_id,
                "role": w.role,
                "running": w.running_count,
                "max_concurrent": w.max_concurrent,
                "memory_budget_mb": w.memory_budget_mb,
                "reserved_memory_mb": w.reserved_memory_mb,
                "last_beat_at": w.last_beat_at.isoformat(),
            }
            for w in WorkerHeartbeat.alive().order_by("role", "worker_id")
        ]
    }


def collect_runs(limit: int = 100) -> dict:
    """Recent run outcomes, without their logs.

    stdout/stderr tails are tenant output and can contain query results, so
    they are summarised by status and never copied.
    """
    from collections import Counter

    from apps.runner.models import Run

    recent = list(
        Run.objects.select_related("studio", "studio__org").order_by("-created_at")[:limit]
    )
    return {
        "status_counts": dict(Counter(r.status for r in recent)),
        "runs": [
            {
                "org": r.studio.org.slug,
                "studio": r.studio.slug,
                "report": r.slug,
                "status": r.status,
                "trigger": r.trigger,
                "pool": r.pool,
                "created_at": r.created_at.isoformat(),
                "duration_seconds": r.duration_seconds,
                "exit_code": r.exit_code,
                "memory_limit_mb": r.memory_limit_mb,
                "peak_memory_mb": r.peak_memory_mb,
                "worker_id": r.worker_id,
            }
            for r in recent
        ],
    }


def collect_tenancy() -> dict:
    """Shape of the install: counts only, never names of people."""
    from django.contrib.auth import get_user_model
    from django.db.models import Count

    from apps.orgs.models import Organization
    from apps.reports.models import Report
    from apps.studios.models import Studio

    return {
        "organizations": [
            {
                "slug": o.slug,
                "is_active": o.is_active,
                "studios": o.studio_count,
                "members": o.member_count,
            }
            for o in Organization.objects.annotate(
                studio_count=Count("studios", distinct=True),
                member_count=Count("memberships", distinct=True),
            ).order_by("slug")
        ],
        "totals": {
            "organizations": Organization.objects.count(),
            "studios": Studio.objects.count(),
            "reports": Report.objects.filter(present_in_scan=True).count(),
            "active_users": get_user_model().objects.filter(is_active=True).count(),
        },
    }


def collect_repos() -> dict:
    """Git sync state. URLs are kept (we need them); tokens never are."""
    from apps.studios.models import StudioRepo

    def _has_token(repo) -> bool | str:
        # Reading the field decrypts it. A wrong SECRET_ENCRYPTION_KEY is a
        # top reason to collect a bundle, so failing to decrypt is an answer,
        # not an error.
        try:
            return bool(repo.token)
        except Exception as exc:  # noqa: BLE001
            return f"undecryptable: {type(exc).__name__}"

    return {
        "repos": [
            {
                "studio": f"{r.studio.org.slug}/{r.studio.slug}",
                "repo_url": _redact_url(r.repo_url),
                "branch": r.branch,
                "sync_interval_minutes": r.sync_interval_minutes,
                "auto_run_changed": r.auto_run_changed,
                "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None,
                "last_synced_sha": r.last_synced_sha,
                "last_error": r.last_error[:500],
                "has_token": _has_token(r),
            }
            for r in StudioRepo.objects.select_related("studio", "studio__org")
        ]
    }


def _safely(name: str, fn):
    """Run one collector, turning a failure into data.

    A support bundle is collected precisely when the install is broken — a
    misconfigured encryption key, an unreachable database, a half-applied
    migration. A collector that raises would deny us the diagnostics at the one
    moment they matter, so every section is allowed to fail on its own.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def build(limit_runs: int = 100) -> dict:
    """The whole bundle as one JSON-serialisable dict. Never raises."""
    from apps.core.health import run_checks

    return {
        "generated_at": datetime.now(dt_tz.utc).isoformat(),
        "schema": 3,
        "health": _safely("health", run_checks),
        "settings": _safely("settings", collect_settings),
        "platform": _safely("platform", collect_platform),
        "fleet": _safely("fleet", collect_fleet),
        "tenancy": _safely("tenancy", collect_tenancy),
        "runs": _safely("runs", lambda: collect_runs(limit_runs)),
        "repos": _safely("repos", collect_repos),
    }


def to_json(bundle: dict) -> str:
    return json.dumps(bundle, indent=2, sort_keys=True, default=str)
