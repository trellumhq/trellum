"""The agent-facing layer: what the framework tells an agent about itself.

Two halves that only make sense together -- :mod:`~trellum.agent.agentdoc`
derives what to say (topics, component signatures, validator check ids) from
the code rather than from a hand-kept list, and
:mod:`~trellum.agent.agentsetup` makes sure something in the project actually
points an agent at it.
"""

from trellum.agent import (
    agentdoc,  # noqa: F401
    agentsetup,  # noqa: F401
)

# The modules kept their names, so `from trellum.agent.agentdoc import ...`
# and the shims below both work; see the shim modules at the repo root for
# why the old paths still resolve.
__all__ = ['agentdoc', 'agentsetup']
