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

    def test_constructed_config_cycle_is_rejected(self):
        events = []
        events.append(events)
        with self.assertRaises(AdmissionError):
            normalize({"events": events})

    def test_shared_constructed_graph_counts_repeated_visits(self):
        from unittest.mock import patch

        shared_event = {"date": "2020-01-01", "label": "shared"}
        with patch("apps.reports.annotation_projection.MAX_VISITS", 6), self.assertRaises(AdmissionError):
            normalize({"events": [shared_event, shared_event]})

    def test_invalid_dates_and_reversed_end(self):
        result = declaration(b'events: [{date: invalid}, {date: 2020-01-02, end_date: 2020-01-01}]')
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0]["ordinal"], 1)
        self.assertLess(result.events[0]["end_date"], result.events[0]["start_date"])

    def test_100000_ordinary_events(self):
        raw = b'events:\n' + b'- {date: 1999-01-01, label: event}\n' * 100000
        self.assertEqual(len(declaration(raw).events), 100000)

    def test_malformed_shapes_are_not_empty_sources(self):
        for raw in (b'events: {}', b'type_defaults: []', b'events: invalid', b'[one, two]'):
            with self.subTest(raw=raw), self.assertRaises(AdmissionError) as error:
                declaration(raw)
            self.assertEqual(error.exception.code, "parse-error")

    def test_limits_reject_whole_source(self):
        from unittest.mock import patch
        for patch_name, raw in (
            ("MAX_EVENTS", b'events: [{date: 2020-01-01}, {date: 2020-01-02}]'),
            ("NORMALIZED_BYTES", b'events: [{date: 2020-01-01, label: long}]'),
            ("RAW_BYTES", b'events: []'),
            ("EXPANDED_BYTES", b'events: [{date: 2020-01-01}]'),
            ("MAX_VISITS", b'events: [{date: 2020-01-01}]'),
            ("MAX_NODES", b'events: [{date: 2020-01-01}]'),
        ):
            with self.subTest(limit=patch_name), patch("apps.reports.annotation_projection." + patch_name, 1), self.assertRaises(AdmissionError):
                declaration(raw)

    def test_alias_amplification_and_depth_before_construction(self):
        from unittest.mock import patch
        raw = b'a: &a [one, two, three]\nb: &b [*a, *a, *a]\nevents: [*b, *b, *b]\n'
        with patch("apps.reports.annotation_projection.MAX_VISITS", 20), self.assertRaises(AdmissionError):
            declaration(raw)
        with patch("apps.reports.annotation_projection.MAX_DEPTH", 2), self.assertRaises(AdmissionError):
            declaration(b'events: [[[{date: 2020-01-01}]]]')

    def test_local_none_and_absent_scope_differ(self):
        rows, _ = normalize({"studio": None, "annotations": {"events": [{"date": "2020-01-01"}, {"date": "2020-01-01", "studio": ""}]}}, local=True)
        self.assertEqual([row["scope"] for row in rows], ["None", ""])

    def test_local_wrong_falsey_shapes_are_errors(self):
        for config in ({"annotations": []}, {"annotations": {"events": {}}}, {"annotations": {"events": ""}}):
            with self.subTest(config=config), self.assertRaises(AdmissionError) as error:
                normalize(config, local=True)
            self.assertEqual(error.exception.code, "parse-error")

    def test_unrelated_config_is_outside_declaration_budget(self):
        from unittest.mock import patch
        config = {"queries": "x" * 100000, "annotations": {"unrelated": "y" * 100000, "events": [{"date": "2020-01-01"}]}}
        with patch("apps.reports.annotation_projection.EXPANDED_BYTES", 1000):
            rows, _ = normalize(config, local=True)
        self.assertEqual(len(rows), 1)
