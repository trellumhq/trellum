"""Reject framework test skips except the known Windows-only NTFS cases."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ALLOWED = (
    "../testing/test_private_artifacts.py",
    "NTFS stream alias requires Windows",
)
MAX_ALLOWED = 2
SUMMARY = re.compile(
    r"^\d+ (?:passed|failed|error|errors|skipped|deselected|xfailed|xpassed|warnings?)"
    r"(?:, \d+ (?:passed|failed|error|errors|skipped|deselected|xfailed|xpassed|warnings?))*"
    r"(?: in .+)?$",
    re.MULTILINE,
)
SKIPPED_COUNT = re.compile(r"\b(\d+) skipped\b")
DETAIL = re.compile(r"^SKIPPED \[(\d+)\] (.*?):(\d+): (.*)$", re.MULTILINE)


def check(output: str) -> list[str]:
    summaries = SUMMARY.findall(output)
    summary = summaries[-1] if summaries else None
    if summary is None:
        return ["pytest output has no recognizable final summary line"]
    skipped_count = SKIPPED_COUNT.search(summary)
    reported = int(skipped_count.group(1)) if skipped_count else 0
    details = [(int(count), path, int(line), reason) for count, path, line, reason in DETAIL.findall(output)]
    detail_count = sum(count for count, *_ in details)
    errors = []

    if detail_count != reported:
        errors.append(f"pytest reports {reported} skipped test(s), but details account for {detail_count}")

    allowed_count = 0
    for count, path, line, reason in details:
        if (path, reason) == ALLOWED:
            allowed_count += count
        else:
            errors.append(f"unexpected skip [{count}]: {path}:{line}: {reason}")

    if allowed_count > MAX_ALLOWED:
        errors.append(f"allowed NTFS skip count is {allowed_count}; maximum is {MAX_ALLOWED}")
    return errors


def self_test() -> None:
    allowed = """SKIPPED [2] ../testing/test_private_artifacts.py:73: NTFS stream alias requires Windows
1527 passed, 2 skipped, 12 deselected in 13.09s
"""
    clean = "1527 passed, 12 deselected in 13.09s"
    summary_missing_details = "1527 passed, 2 skipped, 12 deselected in 13.09s"
    unexpected = """SKIPPED [1] ../testing/test_other.py:20: optional dependency missing
1527 passed, 1 skipped, 12 deselected in 13.09s
"""
    additional = """SKIPPED [2] ../testing/test_private_artifacts.py:73: NTFS stream alias requires Windows
SKIPPED [1] ../testing/test_other.py:20: optional dependency missing
1527 passed, 3 skipped, 12 deselected in 13.09s
"""
    over_limit = """SKIPPED [3] ../testing/test_private_artifacts.py:73: NTFS stream alias requires Windows
1527 passed, 3 skipped, 12 deselected in 13.09s
"""
    assert check(clean) == []
    assert check(allowed) == []
    assert check(summary_missing_details)
    assert check(unexpected)
    assert check(additional)
    assert check(over_limit)


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        self_test()
        print("skip gate self-check passed")
        return 0
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} PYTEST_OUTPUT", file=sys.stderr)
        return 2
    errors = check(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if errors:
        print("ERROR: unexpected framework test skips:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Framework skip gate passed (only known NTFS platform skips, if any).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
