"""Admission for authored, bounded complete calendar snapshots."""

from __future__ import annotations

from trellum.components.calendar_coverage import CalendarCoverage, bucket_context_config, daily_date


def _check_calendar_coverage(comps, result):
    from trellum.components.filterable import DataSource, ScopedDataSource

    sources = {
        getattr(c, "id", None): c for c, _ in comps if isinstance(c, (DataSource, ScopedDataSource))
    }
    seen = set()
    for comp, section in comps:
        declaration = (
            comp if isinstance(comp, CalendarCoverage) else getattr(comp, "bucket_context", None)
        )
        if declaration is None:
            continue
        try:
            if not isinstance(declaration, CalendarCoverage):
                raise ValueError("bucket_context must reference CalendarCoverage")
            config = declaration.config()
            if comp is not declaration:
                bucket_context_config(comp)
            if id(declaration) in seen:
                continue
            seen.add(id(declaration))
            source = sources.get(declaration.dataset_id)
            if isinstance(source, ScopedDataSource):
                source = sources.get(source.parent)
            if (
                not isinstance(source, DataSource)
                or getattr(source, "chunk_by", None)
                or getattr(source, "projection", None)
            ):
                raise ValueError(
                    "calendar coverage requires one complete unchunked snapshot, optionally one native scope; live/chained/derived sources are unsupported"
                )
            frame = source.df
            columns = list(
                dict.fromkeys(
                    [declaration.date_column, declaration.bucket_column, *declaration.input_columns]
                )
            )
            if len(frame) > 10000 or any(c not in frame.columns for c in columns):
                raise ValueError(
                    "calendar coverage needs declared columns and at most 10000 source facts"
                )
            if len(frame[columns].to_json(orient="values").encode()) > 4 * 1024 * 1024:
                raise ValueError("calendar coverage relevant columns exceed 4 MiB")
            for row in frame[columns].to_dict("records"):
                day = daily_date(row[declaration.date_column])
                key = row[declaration.bucket_column]
                bounds = config["buckets"].get(key) if isinstance(key, str) else None
                if (
                    not declaration.start <= day.isoformat() <= declaration.end
                    or bounds is None
                    or not bounds[0] <= day.isoformat() <= bounds[1]
                ):
                    raise ValueError(
                        "calendar coverage raw dates/keys disagree with the authored mapping or reporting interval"
                    )
            result.passed(
                "calendar-coverage",
                "Bounded UTC calendar declaration and original snapshot keys agree.",
                component=type(comp).__name__,
                section=section,
            )
        except (ValueError, TypeError, OverflowError) as exc:
            result.fail(
                "calendar-coverage-invalid",
                str(exc),
                component=type(comp).__name__,
                section=section,
            )
    result.ran("calendar_coverage")
