"""Publish test timing and outcomes, omitting captured output and failure bodies."""

import argparse
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET


def summarize(source, destination, title):
    root = ET.parse(source).getroot()
    cases = list(root.iter("testcase"))
    totals = {"tests": len(cases), "failures": 0, "errors": 0, "skipped": 0}
    sanitized = ET.Element("testsuites")
    suite = ET.SubElement(sanitized, "testsuite", name=title)
    durations = []
    for case in cases:
        attributes = {key: case.get(key, "") for key in ("classname", "name", "time")}
        clean = ET.SubElement(suite, "testcase", attributes)
        for tag in ("failure", "error", "skipped"):
            if case.find(tag) is not None:
                totals[{"failure": "failures", "error": "errors", "skipped": "skipped"}[tag]] += 1
                ET.SubElement(clean, tag, message="See the job log for details")
        durations.append({"test": f"{case.get('classname', '')}.{case.get('name', '')}",
                          "seconds": float(case.get("time", "0"))})
    durations.sort(key=lambda item: item["seconds"], reverse=True)
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    elapsed = sum(float(item.get("time", "0")) for item in suites)
    suite.attrib.update({key: str(value) for key, value in totals.items()})
    suite.set("time", str(elapsed))
    destination.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(sanitized).write(destination / "junit.xml", encoding="utf-8", xml_declaration=True)
    report = {"title": title, **totals, "elapsed_seconds": elapsed, "durations": durations}
    (destination / "timing.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = [f"## {title}", "", f"{totals['tests']} tests; {totals['failures']} failures; "
             f"{totals['errors']} errors; {totals['skipped']} skipped. Suite elapsed: {elapsed:.1f}s.",
             "", "| Slowest tests | Seconds |", "|---|---:|"]
    for item in durations[:20]:
        name = item["test"].replace("|", "&#124;").replace("\n", " ").replace("`", "'")
        lines.append(f"| `{name}` | {item['seconds']:.2f} |")
    summary = "\n".join(lines) + "\n"
    (destination / "summary.md").write_text(summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()
    if args.junit.is_file():
        summary = summarize(args.junit, args.out_dir, args.title)
    else:
        summary = f"## {args.title}\n\nNo JUnit results: the test step did not complete. See the job log.\n"
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as output:
            output.write(summary)


if __name__ == "__main__":
    main()
