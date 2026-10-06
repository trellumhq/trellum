"""What the framework says about a finished build.

:mod:`~trellum.reporting.diagnostics` measures a report -- dataset sizes,
which filters actually reach which charts -- and :mod:`~trellum.reporting.gallery`
renders the index page over a directory of built reports. Both read output
rather than producing it, which is why they sit together and apart from the
runner.
"""

from trellum.reporting import (
    diagnostics,  # noqa: F401
    gallery,  # noqa: F401
)

# The modules kept their names, so `from trellum.agent.agentdoc import ...`
# and the shims below both work; see the shim modules at the repo root for
# why the old paths still resolve.
__all__ = ['diagnostics', 'gallery']
