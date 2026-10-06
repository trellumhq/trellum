"""What the framework knows about itself, assembled for an agent.

Everything here is **derived**, never restated. A hand-maintained description of
a codebase drifts, and a drifted description is worse than none — an agent that
trusts it produces confidently wrong code. So:

* component names come from :data:`trellum.components.__all__`
* theme names come from :data:`trellum.themes.THEME_REGISTRY`
* validator check ids are read out of ``validation.py`` itself
* prose comes from ``AGENTS.md`` and ``README.md``, addressed by anchor

Anchors, not headings. A section is addressed by an HTML comment carrying a
stable key::

    <!-- topic: queries -->
    ## How to write queries (MANDATORY — ...)

Heading text is prose and people rewrite prose. The anchor is a contract, and
``tests/test_agentdoc.py`` fails if an advertised topic stops resolving — so the
failure is loud in CI rather than silent in the CLI.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# The repo root, which IS the package: AGENTS.md, README.md and the
# validation package all sit there, and this module now lives one level
# down in agent/.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent

#: Topics `trellum guide <topic>` advertises, in the order a report is built.
#: Each must resolve to an anchored section in one of the documents below.
TOPICS: dict[str, str] = {
    "answer": "A number, not a report: metric first, query the source by name, what to reply",
    "queries": "Writing queries: what belongs in SQL and what belongs in pandas",
    "format": "Long vs wide format, and why charts need long",
    "generator": "The step-by-step process for writing generator.py",
    "components": "The component library and the reuse policy",
    "metrics": "metrics.yaml business metrics, and claiming them by id in KPIs",
    "filters": "DataSource, FilterBar, ScopedDataSource and propagation",
    "live-queries": "Declared live queries: build-time snapshot, host-served per-entity lookups",
    "rawhtml": "The RawHTML lifecycle inside a Visible",
    "validation": "The validator protocol, check ids and suppression",
    "report-yaml": "report.yaml fields, and writing a description",
    "themes": "Themes and the theme contract",
    "review": "The live review loop: serve, collect element feedback, poll, rebuild",
    "portal": "The portal loop over MCP: check what changed, publish, run, read the build, fix a source, add an alert",
}

_ANCHOR = re.compile(r"^<!--\s*topic:\s*([a-z0-9-]+)\s*-->\s*$", re.MULTILINE)

#: Documents searched for anchors, nearest-first. AGENTS.md is authored for
#: agents and wins; README.md is authored for people and supplies depth.
_DOCS = ("AGENTS.md", "README.md")


@dataclass(frozen=True)
class Section:
    topic: str
    source: str
    heading: str
    body: str

    def render(self) -> str:
        return f"{self.heading}\n\n{self.body}".strip()


def _read(name: str) -> str | None:
    path = PACKAGE_ROOT / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        # Shipped as package data; a partial install should degrade to "this
        # topic is unavailable" rather than raising out of a help command.
        return None


def _sections_in(name: str) -> dict[str, Section]:
    text = _read(name)
    if not text:
        return {}
    found: dict[str, Section] = {}
    marks = list(_ANCHOR.finditer(text))
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        chunk = text[start:end].strip()
        heading, _, body = chunk.partition("\n")
        found[m.group(1)] = Section(m.group(1), name, heading.strip(), body.strip())
    return found


@lru_cache(maxsize=1)
def sections() -> dict[str, Section]:
    """Every anchored section, with AGENTS.md taking precedence."""
    merged: dict[str, Section] = {}
    for name in reversed(_DOCS):          # later docs are overwritten by earlier
        merged.update(_sections_in(name))
    return merged


def guide(topic: str) -> Section | None:
    return sections().get(topic)


def missing_topics() -> list[str]:
    """Advertised topics with no anchored section. Empty is the only good value."""
    have = sections()
    return [t for t in TOPICS if t not in have]


# ── derived inventories ──────────────────────────────────────────────────

def components() -> list[str]:
    try:
        from trellum import components as c
        return list(getattr(c, "__all__", []))
    except Exception:
        return []


#: Beyond this many optional keywords, listing them all buys nothing -- the
#: agent can construct the component and read the class for the long tail.
_MAX_OPTIONAL = 8


def signatures() -> list[tuple[str, list[str], list[str]]]:
    """``(name, required args, optional keywords)`` for every exported component.

    Derived with ``inspect``, because a hand-kept list of constructor arguments
    is a hand-kept list that will be wrong.

    Naming a component is not enough to use one. The reuse table says a
    share-of-total wants ``DoughnutChart``, and an agent that reads it still
    cannot write the call: ``BarChart`` takes ``x``/``y`` where
    ``DoughnutChart`` takes ``label``/``value``, and nothing about the names
    says so.

    Measured on the identical task: one agent ran ``inspect.signature`` over
    these classes itself and then used the right component; another skipped
    that, copied the nearest existing report, and inherited its ``BarChart``
    for a question a doughnut answers better. The framework was picking which
    of those happened, and charging a tool call for the better one.
    """
    import inspect

    try:
        from trellum import components as c
    except Exception:
        return []

    out: list[tuple[str, list[str], list[str]]] = []
    for name in components():
        obj = getattr(c, name, None)
        if obj is None:
            continue
        try:
            params = list(inspect.signature(obj).parameters.values())
        except (TypeError, ValueError):
            continue  # Not introspectable; skip rather than guess.
        required, optional = [], []
        for p in params:
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            (required if p.default is p.empty else optional).append(p.name)
        out.append((name, required, optional))
    return out


def signature_lines() -> list[str]:
    """One ``Name(required)  [optional]`` line per component.

    Split out so the always-on description and the on-demand guide render from
    one function. Two copies of this formatting would be two things to keep in
    step, and the whole point of deriving it was to have nothing to keep in
    step.
    """
    out = []
    for name, required, optional in signatures():
        call = f"{name}({', '.join(required)})"
        if optional:
            shown = optional[:_MAX_OPTIONAL]
            tail = ", ..." if len(optional) > _MAX_OPTIONAL else ""
            call += f"  [{', '.join(shown)}{tail}]"
        out.append(call)
    return out


def signatures_text() -> str:
    """The signature inventory, formatted as the guide prints it."""
    lines = signature_lines()
    if not lines:
        return ""
    header = [
        "### Constructor signatures (generated from the classes)",
        "",
        "Required arguments first, optional keywords in brackets. Components "
        "that look",
        "interchangeable do not take the same argument names -- check here "
        "before writing",
        "the call rather than copying one from another report.",
        "",
    ]
    return "\n".join(header + [f"  {ln}" for ln in lines])


def themes() -> list[str]:
    try:
        from trellum.themes import THEME_REGISTRY
        return sorted(THEME_REGISTRY)
    except Exception:
        return []


@lru_cache(maxsize=1)
def check_ids() -> list[tuple[str, str]]:
    """``(check_id, level)`` pairs read out of the validation package.

    Read from the call sites rather than a hand-kept list, because a hand-kept
    list is the thing that goes stale. The first argument to ``result.warn``,
    ``result.fail`` and friends is always a string literal, so the AST gives
    this exactly — no regex guessing, and it cannot disagree with the code.

    An agent otherwise recovers these by grepping validation.py, which is
    measurably what happens: 7 of 40 tool calls in the benchmark run.
    """
    # Every module under validation/, not one file: the validator was split
    # into a package (one module per check family) and a hardcoded
    # validation.py path would simply have returned nothing -- leaving
    # `trellum checks` silent and `doctor` reporting "none found", with no
    # error anywhere to say why.
    trees = []
    for path in sorted((PACKAGE_ROOT / "validation").rglob("*.py")):
        try:
            trees.append(ast.parse(path.read_text(encoding="utf-8")))
        except (OSError, SyntaxError):
            continue
    if not trees:
        return []

    levels = {"passed": "PASS", "info": "INFO", "warn": "WARN", "fail": "FAIL"}
    out: dict[str, str] = {}
    msgs: dict[str, str] = {}

    def _text(node: ast.AST) -> str:
        """Best-effort message text. f-strings become `{...}` placeholders."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                else:
                    parts.append("{...}")
            return "".join(parts)
        return ""

    for node in (n for tree in trees for n in ast.walk(tree)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        ident = level = None
        message = ""
        if attr == "add":
            # result.add(id, level, message, ...)
            if len(node.args) >= 2 and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[1], ast.Constant):
                ident = str(node.args[0].value)
                level = levels.get(str(node.args[1].value), "?")
                if len(node.args) >= 3:
                    message = _text(node.args[2])
        elif attr in levels and node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                ident, level = value, levels[attr]
                if len(node.args) >= 2:
                    message = _text(node.args[1])
        if ident and level:
            out.setdefault(ident, level)
            # Keep the longest message seen: a check raised from several places
            # usually has one call site that explains it and others that are terse.
            if message and len(message) > len(msgs.get(ident, "")):
                msgs[ident] = " ".join(message.split())
    _MESSAGES.clear()
    _MESSAGES.update(msgs)
    return sorted(out.items())


#: Populated as a side effect of :func:`check_ids`; see :func:`check_message`.
_MESSAGES: dict[str, str] = {}


def check_message(check_id: str) -> str:
    """What the validator actually says when *check_id* fires.

    Extracted from the call site, so it cannot drift from the code. An agent
    otherwise recovers this by grepping ``validation.py`` -- or, as one measured
    run did, by copying a report, breaking it deliberately, and reading the
    output.
    """
    check_ids()          # ensure populated (cached)
    return _MESSAGES.get(check_id, "")


def version() -> str:
    try:
        from trellum import __version__
        return __version__
    except Exception:
        return "unknown"
