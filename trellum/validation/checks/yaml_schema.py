"""report.yaml fields and their shapes."""

from __future__ import annotations

from typing import Any

from trellum.validation.result import ValidationResult


def _check_yaml_schema(ctx: Any, result: ValidationResult) -> None:
    """Category 9: report.yaml schema."""
    config = ctx.config
    from trellum.report_config import validate_content_config
    try:
        validate_content_config(config)
    except ValueError as exc:
        result.fail("yaml-content-kind", str(exc))
    recommended = ["name", "description", "studio", "category"]
    missing = [k for k in recommended if not config.get(k)]
    if missing:
        result.warn(
            "yaml-missing-required",
            f"report.yaml is missing recommended fields: {missing}. "
            f"Report metadata will be incomplete.",
        )

    # Description quality: a host's AI assistant routes user questions
    # to reports using the description (plus tags). A one-liner like
    # "Revenue report" is not enough signal — it should state the business
    # questions the report answers, the key metrics, and the grain.
    desc = (config.get("description") or "").strip()
    if desc and len(desc) < 60:
        result.warn(
            "report-description-weak",
            f"report.yaml description is only {len(desc)} chars. Write it for "
            "discoverability: which business questions does this report "
            "answer, which key metrics/dimensions does it carry, and at what "
            "grain/freshness? A host's AI assistant uses this text to "
            "route user questions to the right report.",
        )

    if ctx._connections and not config.get("data_sources"):
        result.warn(
            "yaml-missing-data-sources",
            "Generator uses database connections but report.yaml has no "
            "'data_sources' section. Framework falls back to legacy default.",
        )

    schedule = config.get("schedule", {})
    cron = schedule.get("cron", "") if isinstance(schedule, dict) else ""
    if not cron and config.get("kind", "report") != "analysis":
        result.info(
            "yaml-cron-invalid",
            "No schedule.cron defined. Auto-refresh will not work.",
        )
