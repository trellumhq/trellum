"""Demo installs keep offline analyses and their evidence together."""

import json
import sys

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


def test_packaged_analysis_installs_and_builds_offline(tmp_path, monkeypatch):
    destination = tmp_path / "consumer"
    assert demo.main(["--dest", str(destination), "--no-data"]) == 0
    # A source checkout can already contain generated fixtures; a wheel never
    # carries them. Remove that optional copy before proving the offline build.
    (destination / "data-sources" / "demo.sqlite").unlink(missing_ok=True)

    source = demo.DEMO_ROOT / "reports" / "checkout-findings"
    installed = destination / "reports" / "checkout-findings"
    for path in source.rglob("*"):
        if path.is_file():
            assert (installed / path.relative_to(source)).read_bytes() == path.read_bytes()

    monkeypatch.chdir(destination)
    monkeypatch.setattr(
        sys, "argv", ["trellum.run", "reports/checkout-findings", "--no-serve"]
    )
    from trellum.runner import main

    main()
    assert (destination / "output" / "checkout-findings" / "index.html").is_file()
