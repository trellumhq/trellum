"""Demo installs keep offline analyses and their evidence together."""

import json
import subprocess
import sys

import pytest

from trellum.demo import __main__ as demo


def test_installs_analysis_content_and_evidence_without_data(tmp_path, monkeypatch):
    source = tmp_path / "packaged-demo"
    analysis = source / "reports" / "northwind-finding"
    evidence = analysis / "evidence"
    evidence.mkdir(parents=True)
    (analysis / "report.yaml").write_text(
        "kind: analysis\nname: Where Northwind loses buyers\n"
        "studio: northwind\ncategory: Product\n",
        encoding="utf-8",
    )
    (analysis / "content.md").write_text("# Finding\n", encoding="utf-8")
    (evidence / "cart.png").write_bytes(b"synthetic image")
    (evidence / "cart.json").write_text('{"sessions": 42}', encoding="utf-8")
    monkeypatch.setattr(demo, "DEMO_ROOT", source)

    destination = tmp_path / "consumer"
    assert demo.main(["--dest", str(destination), "--no-data"]) == 0

    installed = destination / "reports" / "northwind-finding"
    assert (installed / "content.md").read_text(encoding="utf-8") == "# Finding\n"
    assert (installed / "evidence" / "cart.png").read_bytes() == b"synthetic image"
    assert json.loads((installed / "evidence" / "cart.json").read_text()) == {
        "sessions": 42,
    }
    assert (installed / "report.yaml").read_text(encoding="utf-8") == (
        "kind: analysis\nname: Where Northwind loses buyers\n"
        "studio: northwind\ncategory: Product\n"
    )


@pytest.mark.parametrize("slug", ["checkout-findings", "checkout-presentation"])
def test_packaged_analysis_installs_and_builds_offline(tmp_path, monkeypatch, slug):
    destination = tmp_path / "consumer"
    assert demo.main(["--dest", str(destination), "--no-data"]) == 0
    # A source checkout can already contain generated fixtures; a wheel never
    # carries them. Remove that optional copy before proving the offline build.
    (destination / "data-sources" / "demo.sqlite").unlink(missing_ok=True)

    source = demo.DEMO_ROOT / "reports" / slug
    installed = destination / "reports" / slug
    for path in source.rglob("*"):
        if path.is_file():
            assert (installed / path.relative_to(source)).read_bytes() == path.read_bytes()

    # A real consumer starts a fresh CLI process, without another test's
    # process-global project-root override.
    monkeypatch.delenv("FW_PROJECT_ROOT", raising=False)
    result = subprocess.run(
        [sys.executable, "-m", "trellum.run", f"reports/{slug}", "--no-serve"],
        cwd=destination, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = destination / "output" / slug
    assert (output / "index.html").is_file()
    validation = json.loads((output / "_validation.json").read_text())
    assert validation["summary"]["fail"] == 0
    assert validation["summary"]["warn"] == 0
    if slug == "checkout-presentation":
        for name in ("stage-comparison.png", "recovery-scenario.png"):
            assert (output / "evidence" / name).read_bytes() == (source / "evidence" / name).read_bytes()
