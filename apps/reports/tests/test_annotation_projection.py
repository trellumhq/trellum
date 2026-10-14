"""Declaration admission semantics; database/runtime gates are separate."""
from django.test import SimpleTestCase
from apps.reports.annotation_projection import AdmissionError, declaration, normalize


class DeclarationAdmissionTests(SimpleTestCase):
    def test_duplicate_history_and_defaults(self):
        result = declaration(b'type_defaults: {release: false}\nevents:\n- {date: 1999-01-01, label: same}\n- {date: 1999-01-01, label: same}\n')
        self.assertEqual([e["ordinal"] for e in result.events], [0, 1])
        self.assertFalse(result.events[0]["visible_default"])

    def test_local_scope_and_type(self):
        rows, _ = normalize({"studio": "default", "annotations": {"events": [{"date": "2020-01-01", "studio": ""}]}}, local=True)
        self.assertEqual(rows[0]["scope"], "")
        self.assertEqual(rows[0]["event_type"], "event")

    def test_cycle_rejected(self):
        with self.assertRaises(AdmissionError):
            declaration(b'events: &loop [*loop]')

    def test_invalid_dates_and_reversed_end(self):
        result = declaration(b'events: [{date: invalid}, {date: 2020-01-02, end_date: 2020-01-01}]')
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0]["ordinal"], 1)
        self.assertLess(result.events[0]["end_date"], result.events[0]["start_date"])

    def test_100000_ordinary_events(self):
        raw = b'events:\n' + b'- {date: 1999-01-01, label: event}\n' * 100000
        self.assertEqual(len(declaration(raw).events), 100000)
