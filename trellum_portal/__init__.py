"""trellum portal.

``__version__`` is the release identity, and the one place it is written down.
The git tag is checked against it at release time, so a published image can
always be traced back to a commit — before this, the tag reached the running
instance only as a field named "branch", and nothing tied it to the code.

Semver, with one project-specific rule: **a change to report-build behaviour
in ``trellum/`` is at least a MINOR release**, even when no control-plane code
changed. Documentation and packaging-metadata-only fixes may use a PATCH
release. Report builds are what customers see, so a change in build behaviour
is a change in the product.
The framework ships from this repository and carries this same version — see
``trellum/__init__.py``, which must be kept in step.

MAJOR is reserved for a breaking config change, an upgrade needing a manual
step, or a contract-phase migration (one that is not rollback-safe — see
docs/MIGRATIONS.md).
"""

__version__ = "0.3.0"
