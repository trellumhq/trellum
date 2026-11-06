"""``trellum setup project --remove``: take back exactly what setup added.

The lifecycle test is the one that matters. It starts from a project that
already has its own agent instructions, hooks, MCP servers and credentials,
runs setup (twice), the portal connection, the discovery commands and the
doctor, then removes -- and requires every preexisting file to come back
byte-for-byte. Credential locking also leaves its private coordination file.
The rest pin the refusals: what removal must leave alone rather than guess about.
"""
from __future__ import annotations

import json
import os

import pytest

from trellum.agent import agentremoval, agentsetup
from trellum.cli import main

PORTAL_URL = "https://bi.example.com/s/acme/games"
PORTAL_KEY = "trellum_pk_ab12cd34_" + "x" * 32

THEIR_AGENTS = "# House rules\n\n- Run `make test`.\n- Café ☕ stays café.\n\n\n"
THEIR_SETTINGS = {
    "permissions": {"allow": ["Bash(make test)"]},
    "note": "café",
    "hooks": {
        "SessionStart": [{"matcher": "", "hooks": [
            {"type": "command", "command": "their-tool start", "timeout": 5}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "their-guard"}]}],
    },
}
THEIR_MCP = {"mcpServers": {"docs": {"command": "npx", "args": ["docs-mcp"]}}}
THEIR_ENV = "# secrets\nBI_WH_USER=ana\nBI_WH_PASS=pw\n"


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(agentsetup.dump_json(data), encoding="utf-8")


def _tree(root):
    """Every file under *root* as ``{relative path: bytes}``."""
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_setup_then_remove_preserves_the_project_files_byte_for_byte(tmp_path, capsys):
    """Preexisting instructions, hooks, servers and secrets survive the whole
    lifecycle; only what setup added goes."""
    (tmp_path / "AGENTS.md").write_bytes(THEIR_AGENTS.encode("utf-8"))
    _write_json(tmp_path / ".claude" / "settings.json", THEIR_SETTINGS)
    _write_json(tmp_path / ".mcp.json", THEIR_MCP)
    (tmp_path / ".env").write_text(THEIR_ENV, encoding="utf-8")
    before = _tree(tmp_path)
    dest = ["--dest", str(tmp_path)]

    assert main(["setup", "project", *dest]) == 0
    first = _tree(tmp_path)
    assert main(["setup", "project", *dest]) == 0
    assert _tree(tmp_path) == first, "repeating setup changed something"
    assert main(["setup", "portal", "--url", PORTAL_URL, "--key", PORTAL_KEY, *dest]) == 0
    with_portal = _tree(tmp_path)

    # What the agent runs once it is set up: discovery, then the doctor.
    assert main([]) == 0 and main(["guide", "portal"]) == 0
    assert main(["doctor", *dest]) == 0
    assert _tree(tmp_path) == with_portal, "a read-only command wrote to the project"

    now = _tree(tmp_path)
    assert now["AGENTS.md"].startswith(before["AGENTS.md"]) and now["AGENTS.md"] != before["AGENTS.md"]
    assert "CLAUDE.md" in now and ".cursor/mcp.json" in now
    assert any(b"-m trellum" in v for k, v in now.items() if k == ".claude/settings.json")

    capsys.readouterr()
    assert main(["setup", "project", "--remove", *dest]) == 0
    out = capsys.readouterr().out
    assert "deleted    CLAUDE.md" in out and "removed    AGENTS.md" in out
    assert "TRELLUM_API_KEY" in out, "the key left in .env was not pointed out"

    after = _tree(tmp_path)
    assert after.pop(".env") == with_portal[".env"], ".env must not be touched"
    before.pop(".env")
    assert after.pop(".env.lock") == b"", "credential lock must contain no secret data"
    assert after == before

    # Removing again finds nothing, and setup can be run again afterwards.
    assert main(["setup", "project", "--remove", *dest]) == 0
    assert "Nothing from Trellum to remove" in capsys.readouterr().out
    assert main(["setup", "project", *dest]) == 0
    assert main(["doctor", *dest]) == 0


@pytest.mark.parametrize("original, restored", [
    (b"", b""),
    (b"x\n", b"x\n"),
    (b"x\n\n", b"x\n\n"),
    (b"x\n\n\n\n", b"x\n\n\n\n"),
    (b"x\r\n", b"x\r\n"),
    (b"x\r\ny\r\n\r\n", b"x\r\ny\r\n\r\n"),
    (b"x", b"x"),
])
def test_trailing_newlines_and_line_endings_come_back_as_they_were(tmp_path, original, restored):
    (tmp_path / "AGENTS.md").write_bytes(original)
    agentsetup.write_project_files(tmp_path)
    assert (tmp_path / "AGENTS.md").read_bytes().startswith(original)
    agentremoval.remove(tmp_path)
    assert (tmp_path / "AGENTS.md").read_bytes() == restored


def test_the_summary_names_the_files_the_doctor_and_the_way_out(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["setup", "project"]) == 0
    out = capsys.readouterr().out
    assert "guidance  CLAUDE.md, AGENTS.md" in out
    assert "hooks     .claude/settings.json" in out
    assert " -m trellum doctor\n" in out
    assert " -m trellum setup project --remove" in out

    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(tmp_path.parent)
    assert main(["setup", "project", "--dest", str(other)]) == 0
    assert f"doctor --dest {other}" in capsys.readouterr().out


def test_init_ends_with_the_same_summary(tmp_path, capsys):
    from trellum.scaffold.init_project import init_project

    init_project(str(tmp_path), non_interactive=True)
    out = capsys.readouterr().out
    assert "Agent setup:" in out and "doctor" in out and "--remove" in out


@pytest.mark.parametrize("what", ["hooks", "portal", "reporting"])
def test_remove_is_refused_for_other_setup_modes_before_anything_is_written(
        what, tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    project = tmp_path / "project"
    project.mkdir()

    argv = ["setup", what, "--remove", "--dest", str(project), "--url", PORTAL_URL,
            "--key", PORTAL_KEY, "--non-interactive", "--timezone", "UTC"]
    assert main(argv) == 2
    assert f"`setup {what}`" in capsys.readouterr().err
    assert _tree(tmp_path) == {}, "refusing must happen before any file is written"


def test_an_edited_pointer_is_kept_and_reported(tmp_path, capsys):
    agentsetup.write_project_files(tmp_path)
    path = tmp_path / "CLAUDE.md"
    edited = path.read_text(encoding="utf-8") + "\n## Our additions\n\nBe kind.\n"
    path.write_text(edited, encoding="utf-8")

    assert main(["setup", "project", "--remove", "--dest", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "kept       CLAUDE.md" in captured.out
    assert "edited since" in captured.err
    assert path.read_text(encoding="utf-8") == edited
    assert not (tmp_path / "AGENTS.md").exists(), "the untouched pointer is still ours to delete"


def test_a_pointer_from_another_interpreter_is_still_recognised(tmp_path):
    """The only thing that differs per machine is the interpreter."""
    pointer = agentsetup.POINTER.format(py="/opt/venv/bin/python", cwd_note="", portal="")
    (tmp_path / "CLAUDE.md").write_text(pointer, encoding="utf-8")
    # One interpreter edited to something else is a hand edit, not a machine.
    (tmp_path / "AGENTS.md").write_text(
        pointer.replace("/opt/venv/bin/python", "python", 1), encoding="utf-8")

    rows = {r.name: r.status for r in agentremoval.remove(tmp_path)}
    assert rows["CLAUDE.md"] == "deleted" and rows["AGENTS.md"] == "kept"
    assert not (tmp_path / "CLAUDE.md").exists() and (tmp_path / "AGENTS.md").exists()


def test_unbalanced_markers_are_not_guessed_at(tmp_path, capsys):
    original = f"# mine\n{agentsetup.BLOCK_START}\nhalf a block\n"
    (tmp_path / "AGENTS.md").write_text(original, encoding="utf-8")
    assert main(["setup", "project", "--remove", "--dest", str(tmp_path)]) == 1
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == original
    assert "matched pair" in capsys.readouterr().err


def test_text_that_is_not_utf8_is_left_alone(tmp_path):
    (tmp_path / "AGENTS.md").write_bytes(b"\xff\xfe not utf-8 " + agentsetup.BLOCK_START.encode())
    before = _tree(tmp_path)
    rows = {r.name: r.status for r in agentremoval.remove(tmp_path)}
    assert rows["AGENTS.md"] == "unreadable" and _tree(tmp_path) == before


def test_invalid_json_is_left_alone_and_the_rest_is_still_removed(tmp_path, capsys):
    (tmp_path / "AGENTS.md").write_text("# house\n", encoding="utf-8")
    agentsetup.write_project_files(tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.write_text('{"hooks": ', encoding="utf-8")                # truncated
    (tmp_path / ".mcp.json").write_text('["not", "an", "object"]', encoding="utf-8")
    before = _tree(tmp_path)

    assert main(["setup", "project", "--remove", "--dest", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "unreadable .claude/settings.json" in out and "unreadable .mcp.json" in out
    after = _tree(tmp_path)
    assert after[".claude/settings.json"] == before[".claude/settings.json"]
    assert after[".mcp.json"] == before[".mcp.json"]
    assert after["AGENTS.md"] == b"# house\n" and "CLAUDE.md" not in after


def test_only_hooks_written_by_setup_are_removed(tmp_path):
    """No substring matching: lookalikes, edited entries and neighbours stay."""
    py = "/opt/venv/bin/python"
    ours_start = {"type": "command", "command": f"{py} -m trellum", "timeout": 10}
    theirs = [
        {"type": "command", "command": f"{py} -m trellum-tools lint", "timeout": 10},
        {"type": "command", "command": f"cd lib && {py} -m trellum", "timeout": 10},
        {"type": "command", "command": f"{py} -m trellum", "timeout": 99},         # edited
        {"type": "command", "command": f"{py} -m trellum", "timeout": 10, "statusMessage": "hi"},
        {"type": "command", "command": "their-tool"},
    ]
    settings = {"hooks": {
        "SessionStart": [
            {"matcher": "", "hooks": [ours_start, *theirs]},     # theirs share our group
            {"matcher": "startup", "hooks": [ours_start]},       # our command, their matcher
        ],
        "PostToolUse": [
            {"matcher": "Edit|Write", "hooks": [
                {"type": "command", "command": f"{py} -m trellum hook post-edit", "timeout": 15}]},
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "their-lint"}]},
        ],
        "Stop": [{"matcher": "", "hooks": [ours_start]}],       # not an event we write
    }}
    _write_json(tmp_path / ".claude" / "settings.json", settings)

    rows = {r.name: r for r in agentremoval.remove(tmp_path)}
    row = rows[".claude/settings.json"]
    assert row.status == "removed" and "-m trellum" in row.note

    expected = json.loads(json.dumps(settings))
    expected["hooks"]["SessionStart"][0]["hooks"] = theirs
    expected["hooks"]["PostToolUse"] = [expected["hooks"]["PostToolUse"][1]]
    assert json.loads((tmp_path / ".claude" / "settings.json").read_text("utf-8")) == expected


def test_a_settings_file_that_only_held_our_hooks_is_deleted_with_its_folder(tmp_path):
    agentsetup.write_project_files(tmp_path)
    (tmp_path / "CLAUDE.md").unlink()
    (tmp_path / "AGENTS.md").unlink()
    rows = {r.name: r.status for r in agentremoval.remove(tmp_path)}
    assert rows[".claude/settings.json"] == "deleted"
    assert _tree(tmp_path) == {} and not (tmp_path / ".claude").exists()


def test_an_unrelated_file_in_the_settings_folder_keeps_the_folder(tmp_path):
    agentsetup.write_project_files(tmp_path)
    (tmp_path / ".claude" / "settings.local.json").write_text("{}", encoding="utf-8")
    agentremoval.remove(tmp_path)
    assert (tmp_path / ".claude" / "settings.local.json").read_text("utf-8") == "{}"
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_a_server_named_trellum_that_setup_did_not_write_is_kept(tmp_path, capsys):
    mine = {"mcpServers": {"trellum": {"command": "my-own-server"}, "docs": {"command": "x"}}}
    _write_json(tmp_path / ".mcp.json", mine)
    assert main(["setup", "project", "--remove", "--dest", str(tmp_path)]) == 1
    assert json.loads((tmp_path / ".mcp.json").read_text("utf-8")) == mine
    assert "did not write" in capsys.readouterr().err


def test_our_server_goes_and_the_others_stay(tmp_path):
    assert main(["setup", "portal", "--url", PORTAL_URL, "--key", PORTAL_KEY,
                 "--dest", str(tmp_path)]) == 0
    conf = json.loads((tmp_path / ".mcp.json").read_text("utf-8"))
    conf["mcpServers"]["docs"] = {"command": "x"}
    _write_json(tmp_path / ".mcp.json", conf)

    agentremoval.remove(tmp_path)
    assert json.loads((tmp_path / ".mcp.json").read_text("utf-8")) == {
        "mcpServers": {"docs": {"command": "x"}}}
    assert not (tmp_path / ".cursor").exists()
    assert PORTAL_KEY in (tmp_path / ".env").read_text("utf-8")


def test_a_symlinked_instructions_file_stays_a_symlink(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# house\n", encoding="utf-8")
    try:
        os.symlink("AGENTS.md", tmp_path / "CLAUDE.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    agentsetup.write_project_files(tmp_path)
    assert agentsetup.BLOCK_START in (tmp_path / "AGENTS.md").read_text("utf-8")

    agentremoval.remove(tmp_path)
    assert (tmp_path / "AGENTS.md").read_text("utf-8") == "# house\n"
    assert (tmp_path / "CLAUDE.md").is_symlink()


def test_removing_from_a_project_that_never_had_setup_changes_nothing(tmp_path, capsys):
    (tmp_path / "AGENTS.md").write_text("# house\n", encoding="utf-8")
    _write_json(tmp_path / ".claude" / "settings.json", THEIR_SETTINGS)
    before = _tree(tmp_path)
    assert main(["setup", "project", "--remove", "--dest", str(tmp_path)]) == 0
    assert _tree(tmp_path) == before
    assert "Nothing from Trellum to remove" in capsys.readouterr().out


def test_setup_preserves_custom_hooks_and_mixed_groups():
    custom = {"type": "command", "command": "python -m trellum-tools", "timeout": 10}
    settings = agentsetup.hook_config()
    settings["SessionStart"][0]["hooks"].append(custom)
    merged, _ = agentsetup.merge_hooks({"hooks": settings})
    assert any(custom in g["hooks"] for g in merged["hooks"]["SessionStart"])
    assert agentsetup.merge_hooks(merged) == (merged, False)


def test_removal_keeps_project_directory(tmp_path):
    _write_json(tmp_path / ".mcp.json", {"mcpServers": {"trellum": agentsetup.mcp_servers(PORTAL_URL)[".mcp.json"]}})
    agentremoval.remove(tmp_path)
    assert tmp_path.is_dir()


def test_removal_does_not_write_through_symlink(tmp_path):
    outside = tmp_path / "outside.json"
    _write_json(outside, {"hooks": agentsetup.hook_config()})
    before = outside.read_bytes()
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").symlink_to(outside)
    assert agentremoval.incomplete(agentremoval.remove(tmp_path))
    assert outside.read_bytes() == before
