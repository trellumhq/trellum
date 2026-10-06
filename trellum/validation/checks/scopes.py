"""Report scopes."""

from __future__ import annotations

from typing import Any

from trellum.validation.result import ValidationResult


def _check_scopes(ctx: Any, result: ValidationResult) -> None:
    """Category 8: Scope system."""
    if not ctx.has_scopes:
        return

    for scope_name, sections in ctx.scopes.items():
        if not sections:
            result.warn(
                "scope-empty",
                f"Scope '{scope_name}' was declared but has no sections.",
            )

    if len(ctx.scopes) > 1:
        default = ctx.default_scope
        result.info(
            "scope-no-default",
            f"Multiple scopes exist. Default is '{default}' (first registered). "
            f"All scopes: {list(ctx.scopes.keys())}",
        )
