"""Shared fixtures for the Django test suite."""
import pytest
from django.contrib.auth import get_user_model

from apps.core import roles
from apps.orgs.models import (
    Organization,
    OrgMembership,
    PermissionGroup,
    PermissionGroupGrant,
    PermissionGroupMembership,
)
from apps.studios.models import Studio, StudioMembership

User = get_user_model()


@pytest.fixture
def org(db):
    return Organization.objects.create(slug="demo", name="Demo")


@pytest.fixture
def other_org(db):
    return Organization.objects.create(slug="rival", name="Rival Corp")


@pytest.fixture
def studio(org):
    return Studio.objects.create(org=org, slug="casino", name="Casino Studio")


@pytest.fixture
def studio2(org):
    return Studio.objects.create(org=org, slug="arcade", name="Arcade Studio")


@pytest.fixture
def other_studio(other_org):
    return Studio.objects.create(org=other_org, slug="secret", name="Secret Studio")


@pytest.fixture
def make_user(db):
    def _make(email, *, org=None, org_role=roles.ORG_MEMBER, password="pw-Str0ng-pw"):
        user = User.objects.create_user(email=email, password=password)
        if org is not None:
            OrgMembership.objects.create(user=user, org=org, role=org_role)
        return user

    return _make


@pytest.fixture
def org_admin(make_user, org):
    return make_user("admin@demo.example", org=org, org_role=roles.ORG_ADMIN)


@pytest.fixture
def member(make_user, org):
    return make_user("member@demo.example", org=org)


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(email="root@portal.local", password="pw-Str0ng-pw")


@pytest.fixture
def make_group(org):
    def _make(name, *, org_=None, org_role="", default_studio_role="", grants=()):
        group = PermissionGroup.objects.create(
            org=org_ or org,
            name=name,
            org_role=org_role,
            default_studio_role=default_studio_role,
        )
        for studio_obj, role in grants:
            PermissionGroupGrant.objects.create(group=group, studio=studio_obj, role=role)
        return group

    return _make


@pytest.fixture
def attach_group(db):
    def _attach(user, group):
        return PermissionGroupMembership.objects.create(user=user, group=group)

    return _attach


@pytest.fixture
def grant_studio(db):
    def _grant(user, studio_obj, role):
        return StudioMembership.objects.create(user=user, studio=studio_obj, role=role)

    return _grant


@pytest.fixture
def login(client):
    def _login(user, password="pw-Str0ng-pw"):
        assert client.login(username=user.email, password=password)
        return client

    return _login


# ── Studio-on-disk fixtures (runner + reports suites) ───────────────────────

@pytest.fixture(autouse=True)
def data_dir(settings, tmp_path):
    """Keep every test's studio files separate, including view-created studios.

    Database rollback does not remove directories. Tests that do not request
    ``studio_tree`` can still write files through views, so they must also get
    a fresh data root. pytest's temporary paths are separate for xdist workers.
    """
    settings.DATA_DIR = tmp_path
    return tmp_path


@pytest.fixture
def studio_tree(studio, data_dir):
    """Studio with its on-disk skeleton created under a tmp DATA_DIR."""
    studio.ensure_dirs()
    return studio


@pytest.fixture
def write_report(studio_tree):
    """Create reports/<slug>/report.yaml (+generator stub) in the studio."""

    def _write(slug: str, **cfg):
        import yaml

        report_dir = studio_tree.reports_dir / slug
        report_dir.mkdir(parents=True, exist_ok=True)
        config = {"slug": slug, "name": cfg.pop("name", slug.title()), **cfg}
        (report_dir / "report.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        (report_dir / "generator.py").write_text("# test stub\n", encoding="utf-8")
        return report_dir

    return _write


@pytest.fixture
def write_meta(studio_tree):
    """Write a minimal output/<slug>/_meta.json the registry will merge."""
    import json as _json

    def _write(slug: str, **meta):
        out = studio_tree.output_dir / slug
        out.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "slug": slug, **meta}
        (out / "_meta.json").write_text(_json.dumps(payload), encoding="utf-8")
        return out

    return _write


@pytest.fixture
def observe_meta(studio_tree, write_meta):
    """``write_meta`` plus the summary row a worker projects from it.

    The registry reads that row (never ``_meta.json``). ``observed`` is the
    ``has_output`` the worker saw: ``None`` leaves it unknown.
    """
    from datetime import datetime
    from datetime import timezone as _tz

    from django.utils import timezone

    from apps.reports.models import Report, ReportSourceSummary

    def _observe(slug: str, *, observed=None, **meta):
        out = write_meta(slug, **meta)
        report = Report.objects.get(studio=studio_tree, slug=slug)
        now = timezone.now()
        last_run = meta.get("last_run")
        at = datetime.fromisoformat(last_run.replace("Z", "+00:00")) if last_run else now
        runtime = {}
        if isinstance(meta.get("validation"), dict) and isinstance(meta["validation"].get("summary"), dict):
            runtime["validation"] = {"summary": meta["validation"]["summary"]}
        if meta.get("framework_version"):
            runtime["framework_version"] = meta["framework_version"]
        ReportSourceSummary.objects.update_or_create(report=report, defaults={
            "schema_version": 2, "state": "ready", "quality": "observed", "runtime_state": "ready",
            "observed_at": now, "attempt_at": at if at.tzinfo else at.replace(tzinfo=_tz.utc),
            "attempt_status": meta.get("last_status", "not_run"), "attempt_error": (meta.get("last_error") or "")[:500],
            "attempt_blocked_by": meta.get("blocked_by") or [], "runtime": runtime,
            "has_output": observed, "html_entry": "index.html" if observed else "",
        })
        return out

    return _observe


@pytest.fixture
def write_metrics_yaml(studio_tree):
    """Write <project_root>/metrics.yaml (semantic-layer Phase 2).

    ``entries`` is a list of metrics.yaml entry dicts; ``text`` (raw YAML)
    overrides it entirely -- for tests that need a deliberately malformed
    file. trellum.metrics.load_metrics_result also caches by path for the
    life of the process, so this invalidates that cache on the way out --
    otherwise a second write in the same test would still read the first.
    """
    import yaml

    def _write(entries=None, *, text=None, version=1):
        from trellum.metrics import invalidate_metrics_cache

        path = studio_tree.project_root / "metrics.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        if text is not None:
            path.write_text(text, encoding="utf-8")
        else:
            path.write_text(
                yaml.safe_dump({"version": version, "metrics": entries or []}),
                encoding="utf-8",
            )
        invalidate_metrics_cache()
        return path

    return _write


@pytest.fixture
def report_row(studio_tree, write_report):
    from apps.reports.models import Report
    from apps.reports.scan import sync_studio_registry

    write_report("player-overview", display={"priority": 5})
    sync_studio_registry(studio_tree)
    return Report.objects.get(studio=studio_tree, slug="player-overview")


# ── Subprocess-spawn fixtures (runner + core seam suites) ───────────────────

class FakeProc:
    """Stands in for subprocess.Popen."""

    def __init__(self, cmd=None, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.pid = 4242
        self.returncode = None
        self.signals: list = []
        self.killed = False

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)

    def kill(self):
        self.killed = True

    def finish(self, code: int):
        self.returncode = code


@pytest.fixture
def fake_popen(monkeypatch):
    from apps.runner import executor as executor_mod

    holder: dict = {}

    def _popen(cmd, **kwargs):
        proc = FakeProc(cmd, **kwargs)
        holder.setdefault("procs", []).append(proc)
        return proc

    monkeypatch.setattr(executor_mod.subprocess, "Popen", _popen)
    return holder


@pytest.fixture
def run_threads_inline(monkeypatch):
    """apps.reports.views' sample-send endpoints spawn a real daemon thread
    to do the actual send off the request (see _run_sample_send) -- tests
    that need the send to have finished before asserting on mail.outbox
    replace `threading` in that module with a fake whose Thread.start()
    just calls the target synchronously, in-process. Deterministic, no
    sleep/join needed, and doesn't touch the real stdlib threading module
    (only the name bound inside apps.reports.views)."""
    import apps.reports.views as views_mod

    class _ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):  # noqa: ARG002
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    class _FakeThreadingModule:
        Thread = _ImmediateThread

    monkeypatch.setattr(views_mod, "threading", _FakeThreadingModule)


@pytest.fixture
def queued_run(report_row, org_admin):
    from apps.runner.models import Run

    return Run.objects.create(
        report=report_row,
        studio=report_row.studio,
        slug=report_row.slug,
        status=Run.STARTING,
        requested_by=org_admin,
    )


# ---------------------------------------------------------------------------
# Heavy tests: opt-in, excluded from the ordinary run (see CONTRIBUTING.md).
#
# Marked ``heavy`` here, at collection time, so the parametrize decorators stay
# untouched and every case still exists:
#   * Browser matrices keep ONE smoke case per test function and other
#     parameters: chromium, desktop viewport, smallest scale. Other engines,
#     viewports and sizes are heavy.
#   * Scale/performance tests that build 200 to 100,000 rows.
#   * Real-process lifecycle tests (supervised children, SIGKILL).
# Correctness and permission tests are never marked here.
# ---------------------------------------------------------------------------
_ENGINE_AXES = {"engine", "metrics_browser"}
_VIEWPORT_AXES = {"width", "size", "mobile"}
_SCALE_AXES = {"experiments_server", "analytics_server", "count"}
_DESKTOP_WIDTH = 1024

# Whole tests (node id fragments) that are heavy regardless of parameters.
_HEAVY_TESTS = (
    "test_operations_routes.py::test_whole_request_sql_and_bytes_stay_bounded_as_reports_grow",
    "test_metric_workspace_browser.py::test_large_offpage_focused_workspace_readiness",
    "test_operator_probe_process.py::",
)
# Integer ``size`` values above the smallest are scale cases in these modules.
_SCALE_SIZE_MODULES = ("test_catalog_bounded.py",)
# Browser tests whose file name does not end in ``_browser.py``.
_BROWSER_MODULES = (
    "test_portal_mobile_runtime.py", "test_report_display_runtime.py",
    "test_native_page_feedback.py", "test_navigation_foundation.py",
)


def _viewport_rank(value):
    if isinstance(value, bool):
        return (0, 0) if not value else (1, 0)  # mobile=False is desktop
    width = value[0] if isinstance(value, tuple) else value
    return (0, width) if width >= _DESKTOP_WIDTH else (1, width)


def _matrix_key(item):
    """Return (group, rank) for a matrix item, or None when it has no matrix axes."""
    params = getattr(getattr(item, "callspec", None), "params", None)
    if not params:
        return None
    module = item.nodeid.split("::", 1)[0]
    scale_size = module.endswith(_SCALE_SIZE_MODULES)
    browser = module.endswith(("_browser.py",) + _BROWSER_MODULES)
    engine = [v for k, v in params.items() if browser and k in _ENGINE_AXES]
    viewport = [
        _viewport_rank(v) for k, v in params.items()
        if browser and k in _VIEWPORT_AXES
    ]
    scale = [v for k, v in params.items() if (browser and k in _SCALE_AXES) or (k == "size" and scale_size)]
    if not (engine or viewport or scale):
        return None
    axes = _ENGINE_AXES | _VIEWPORT_AXES | _SCALE_AXES
    others = tuple(sorted(
        (k, repr(v)) for k, v in params.items()
        if k not in axes
    ))
    group = (module, item.originalname, others)
    rank = (
        tuple(0 if e == "chromium" else 1 for e in engine),
        tuple(viewport),
        tuple(scale),
    )
    return group, rank


def pytest_collection_modifyitems(config, items):
    heavy = pytest.mark.heavy
    best = {}
    keyed = []
    for item in items:
        if any(fragment in item.nodeid for fragment in _HEAVY_TESTS):
            item.add_marker(heavy)
            continue
        found = _matrix_key(item)
        if found is None:
            continue
        group, rank = found
        keyed.append((item, group, rank))
        if group not in best or rank < best[group]:
            best[group] = rank
    for item, group, rank in keyed:
        if rank != best[group]:
            item.add_marker(heavy)
