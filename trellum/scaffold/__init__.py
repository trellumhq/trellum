"""Writing new things into a project: a project, and a report inside it.

``python -m trellum.init`` scaffolds the project-level files;
``python -m trellum.new`` scaffolds one report. Both are one-shot generators
that never touch a file they did not create.
"""

from trellum.scaffold import (
    init_project,  # noqa: F401
    new_report,  # noqa: F401
)

# The modules kept their names, so `from trellum.agent.agentdoc import ...`
# and the shims below both work; see the shim modules at the repo root for
# why the old paths still resolve.
__all__ = ['init_project', 'new_report']
