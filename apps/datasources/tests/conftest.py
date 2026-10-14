import pytest

from apps.datasources import probe_child
from apps.datasources.probe import ProbeResult


@pytest.fixture(autouse=True)
def inprocess_probe(request, monkeypatch):
    """Run the probe body in-process so tests can fake drivers by monkeypatching.

    The real child process, deadline and kill paths are covered by
    test_probe_deadline.py, which opts out.
    """
    if request.module.__name__.endswith("test_probe_deadline"):
        return

    def run(kind, info, *, timeout, argv=None):  # noqa: ARG001
        try:
            ok, detail = probe_child._probe(kind, info)
        except Exception as exc:  # noqa: BLE001 - mirrors the child's reporting
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        return ProbeResult(ok, detail)

    monkeypatch.setattr("apps.datasources.testing.run_probe", run)
