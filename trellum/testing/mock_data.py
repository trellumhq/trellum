"""Auto-generate mock DataFrames from SQL query constants.

Parses SELECT clauses to infer column names, then generates type-appropriate
random data based on column name heuristics.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

_DATE_PATTERNS = re.compile(
    r"(event_date|install_date|cohort_date|start_date|end_date|ref_date|"
    r"last_updated|last_etl_run|last_event_ts|month|date)",
    re.IGNORECASE,
)
_REVENUE_PATTERNS = re.compile(
    r"(revenue|iap|spend|investment|budget|ad_rev|adrev|gross|net_rev|"
    r"cpm|price|avg_transaction|iap_usd|dtc_revenue|purchase_revenue)",
    re.IGNORECASE,
)
_COUNT_PATTERNS = re.compile(
    r"(dau|mau|wau|users|payers|installs|impressions|clicks|transactions|"
    r"txn_count|ftd|hands|sessions|events|cohort_size|participants|"
    r"unique_|total_|count|whales|ftp|first_time|invites|invitee)",
    re.IGNORECASE,
)
_RATE_PATTERNS = re.compile(
    r"(ret_d\d+|eligible_pct|pct|ratio|rate|conversion|share|percent|"
    r"retained_|arpdau|arppu|step_conversion)",
    re.IGNORECASE,
)
_ID_PATTERNS = re.compile(
    r"(user_id|game_event_id|quest_id)",
    re.IGNORECASE,
)


_CATEGORICAL_VALUES = {
    "platform": ["iOS", "Android", "Web", "pras"],
    "placement": ["rewarded_video", "interstitial", "banner", "offerwall", "native"],
    "dsi_bucket": ["0-1d", "2-7d", "8-14d", "15-30d", "31-60d", "61-90d", "90d+"],
    "spender_tier": ["non_spender", "minnow", "dolphin", "whale", "super_whale"],
    "audience_segment": ["new", "current", "reactivated", "dormant"],
    "test_variant": ["control", "variant_a", "variant_b"],
    "currency_name": ["chips", "gold", "diamonds"],
    "template_type": ["cash_game", "tournament", "sit_n_go"],
    "is_bot": [0, 1],
    "top1perc_flag": [0, 1],
    "network_type": ["organic", "paid"],
    "campaign_type": ["ua", "retargeting", "branding"],
    "attribution_mode": ["deterministic", "probabilistic"],
    "user_type": ["new", "returning"],
    "cz_bucket": ["CZ1", "CZ2", "CZ3", "CZ4", "CZ5"],
    "elite_flag": ["Elite Member", "Non-Elite"],
    "build_platform": ["iOS", "Android"],
    "account_manager": ["Direct", "Managed"],
    # "country" reads as a count to _COUNT_PATTERNS -- `count` is a substring
    # of it -- so without an entry here it generates integers, and a report
    # that groups by country then builds a dict keyed by numpy scalars.
    # orjson refuses those keys even under OPT_NON_STR_KEYS, so the render
    # dies with a TypeError naming neither the column nor the report. Names,
    # not ISO codes: the demo warehouse stores names and the shipped atlas
    # keys on them.
    "country": ["United States", "Canada", "United Kingdom", "Germany",
                "Japan", "Australia"],
    # Its partner in every country filter pair, and equally unrecognisable to
    # the patterns above -- it would otherwise come out as random floats.
    "region": ["NA", "EMEA", "APAC"],
}

# Columns that look categorical by name but must be numeric for generator math
_FORCE_NUMERIC = {
    "source_direct", "source_from_purchased", "source_organic", "sink_amount",
    "non_dtc_revenue", "dtc_revenue",
}


def _extract_columns_from_sql(sql: str) -> list[str]:
    """Parse column aliases from a SQL SELECT clause.

    Handles CTEs (WITH ... AS), subqueries, CASE expressions, f-string
    interpolated fragments, and DISTINCT.
    """
    sql_clean = re.sub(r"--[^\n]*", "", sql)
    sql_clean = re.sub(r"/\*.*?\*/", "", sql_clean, flags=re.DOTALL)

    # For CTEs, find the final (outermost) SELECT
    cte_pattern = re.compile(
        r"\bWITH\b.*?\)\s*\bSELECT\b",
        re.IGNORECASE | re.DOTALL,
    )
    cte_match = cte_pattern.search(sql_clean)
    if cte_match:
        final_select_pos = cte_match.end() - len("SELECT")
        sql_clean = sql_clean[final_select_pos:]

    select_match = re.search(
        r"\bSELECT\s+(?:DISTINCT\s+)?(.*?)\bFROM\b",
        sql_clean,
        re.IGNORECASE | re.DOTALL,
    )
    if not select_match:
        return []

    select_body = select_match.group(1)

    # Split on commas, respecting parentheses depth
    depth = 0
    parts = []
    current: list[str] = []
    for char in select_body:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current).strip())

    columns = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Skip pure whitespace / empty from f-string interpolation
        if re.match(r"^\s*$", part):
            continue
        # Match explicit AS alias (most reliable)
        alias_match = re.search(r"\bAS\s+[`\"']?(\w+)[`\"']?\s*$", part, re.IGNORECASE)
        if alias_match:
            columns.append(alias_match.group(1))
            continue
        # Match table.column or plain column at the end
        dot_match = re.search(r"(?:\w+\.)?(\w+)\s*$", part)
        if dot_match:
            col = dot_match.group(1)
            # Skip SQL keywords that ended up as "column names"
            if col.upper() not in ("END", "THEN", "ELSE", "WHEN", "CASE", "NULL",
                                     "AND", "OR", "NOT", "IN", "IS", "AS"):
                columns.append(col)

    return columns


def _infer_type(col: str) -> str:
    """Infer column data type from its name."""
    if col in _FORCE_NUMERIC:
        return "revenue"

    # Exact categorical match takes highest priority (avoids false positives
    # from substring matching in count/revenue patterns)
    for cat_key in _CATEGORICAL_VALUES:
        if col.lower() == cat_key.lower():
            return f"categorical:{cat_key}"

    if _ID_PATTERNS.search(col):
        return "id"
    if _DATE_PATTERNS.search(col):
        if col == "month":
            return "month"
        return "date"
    if _RATE_PATTERNS.search(col):
        return "rate"
    if _REVENUE_PATTERNS.search(col):
        return "revenue"
    if _COUNT_PATTERNS.search(col):
        return "count"

    name_lower = col.lower()
    for cat_key in _CATEGORICAL_VALUES:
        if cat_key.lower() in name_lower:
            return f"categorical:{cat_key}"

    if any(kw in name_lower for kw in ("name", "label", "type", "group", "bucket", "tier", "segment", "flag", "mode")):
        return "categorical_generic"

    return "numeric"


def _generate_column(col: str, col_type: str, n_rows: int, date_range: tuple[str, str]) -> list:
    """Generate n_rows of mock data for a column based on its inferred type."""
    start_dt = datetime.strptime(date_range[0], "%Y-%m-%d")
    end_dt = datetime.strptime(date_range[1], "%Y-%m-%d")
    n_dates = max((end_dt - start_dt).days + 1, 1)

    if col_type == "date":
        dates = [(start_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n_dates)]
        return [dates[i % len(dates)] for i in range(n_rows)]

    if col_type == "month":
        n_months = min(max(n_dates // 28, 6), 60)
        months = sorted(set(
            (start_dt + timedelta(days=i * 30)).strftime("%Y-%m")
            for i in range(n_months)
        ))
        return [months[i % len(months)] for i in range(n_rows)]

    if col_type == "id":
        return [random.randint(100000, 999999) for _ in range(n_rows)]

    if col_type == "revenue":
        return [round(random.uniform(100, 50000), 2) for _ in range(n_rows)]

    if col_type == "count":
        return [random.randint(10, 10000) for _ in range(n_rows)]

    if col_type == "rate":
        return [round(random.uniform(0.01, 0.95), 4) for _ in range(n_rows)]

    if col_type.startswith("categorical:"):
        key = col_type.split(":", 1)[1]
        values = _CATEGORICAL_VALUES.get(key, ["A", "B", "C"])
        # Ensure every value appears at least once for diversity, then fill randomly
        result = list(values[:n_rows])
        while len(result) < n_rows:
            result.append(random.choice(values))
        random.shuffle(result)
        return result[:n_rows]

    if col_type == "categorical_generic":
        labels = [f"{col}_val_{i}" for i in range(min(5, n_rows))]
        return [random.choice(labels) for _ in range(n_rows)]

    return [round(random.uniform(0, 1000), 2) for _ in range(n_rows)]


def generate_mock_df(
    sql: str,
    n_rows: int = 30,
    date_range: Optional[tuple[str, str]] = None,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate a mock DataFrame matching a SQL query's output schema.

    Args:
        sql: SQL query string (column names are parsed from SELECT clause).
        n_rows: Number of rows to generate.
        date_range: Tuple of (start_date, end_date) as YYYY-MM-DD strings.
        seed: Random seed for reproducibility.

    Returns:
        DataFrame with auto-generated columns matching the query schema.
    """
    random.seed(seed)
    if date_range is None:
        # Track the framework clock (FW_NOW when set) so mock output is
        # reproducible for visual-regression baselines.
        from trellum.project import get_now_utc

        end = get_now_utc()
        start = end - timedelta(days=60)
        date_range = (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))

    columns = _extract_columns_from_sql(sql)
    if not columns:
        return pd.DataFrame({"value": [random.randint(1, 100) for _ in range(n_rows)]})

    data = {}
    for col in columns:
        col_type = _infer_type(col)
        data[col] = _generate_column(col, col_type, n_rows, date_range)

    return pd.DataFrame(data)


def generate_mock_dfs_from_module(queries_module, n_rows: int = 30, seed: int = 42) -> dict[str, pd.DataFrame]:
    """Generate mock DataFrames for all SQL constants in a queries module.

    Returns a dict mapping query constant name to its mock DataFrame.
    """
    result = {}
    for attr_name in dir(queries_module):
        if attr_name.startswith("_"):
            continue
        val = getattr(queries_module, attr_name)
        if not isinstance(val, str):
            continue
        if "SELECT" not in val.upper():
            continue
        result[attr_name] = generate_mock_df(val, n_rows=n_rows, seed=seed)
    return result
