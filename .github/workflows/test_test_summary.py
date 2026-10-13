import json
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

from test_summary import summarize


class SummaryTests(unittest.TestCase):
    def test_outcomes_timings_and_private_output_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "input.xml")
            source.write_text('''<testsuites><testsuite tests="3" time="12.5" hostname="private-host">
              <properties><property name="token" value="private-value"/></properties>
              <testcase classname="tests" name="slow" time="4"><failure message="private-value">private-value</failure>
                <system-out>private-value</system-out><system-err>private-value</system-err></testcase>
              <testcase classname="tests" name="skip" time="1"><skipped message="private-value"/></testcase>
              <testcase classname="tests" name="pass" time="2"/>
              <system-out>private-value</system-out>
            </testsuite></testsuites>''')
            destination = Path(directory, "sanitized")
            summary = summarize(source, destination, "Portal")
            report = json.loads((destination / "timing.json").read_text())
            self.assertEqual((report["tests"], report["failures"], report["skipped"]), (3, 1, 1))
            self.assertEqual(report["elapsed_seconds"], 12.5)
            self.assertEqual(report["durations"][0], {"test": "tests.slow", "seconds": 4.0})
            self.assertIn("tests.slow", summary)
            xml = (destination / "junit.xml").read_text()
            self.assertNotIn("private-value", xml)
            self.assertNotIn("private-host", xml)
            self.assertEqual(len(list(ET.fromstring(xml).iter("failure"))), 1)

    def test_single_suite_and_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "input.xml")
            source.write_text('<testsuite time="1"><testcase name="error"><error>private</error></testcase></testsuite>')
            summarize(source, Path(directory, "out"), "Tests")
            report = json.loads(Path(directory, "out/timing.json").read_text())
            self.assertEqual(report["errors"], 1)
            self.assertEqual(report["elapsed_seconds"], 1)


if __name__ == "__main__":
    unittest.main()
