"""Readable synthetic outputs and adversarial structured answer submissions."""
import copy
import json

import pytest

from apps.assistant import answer_review as review
from apps.assistant.tests.test_answer_review import (
    Box, evidence, rendered, state_for, submission,
)


def accepted(args, entry):
    text, feedback = review.submit(args, state_for(entry), Box(entry))
    assert text and feedback is None, feedback
    return text


def test_readable_named_total_and_actual_business_limitations():
    entry = evidence("metric_query")
    args = submission(entry)
    args["claims"][0]["scope"] = "complete_total"
    args["text"] = ("Revenue is {{result}} {{context:result:currency}} for {{context:result:period}}. "
                    "The calculation uses the recorded country filter. Refund treatment still needs confirmation.")
    args["interpretation"]["question"] = "Interpret revenue as the recorded sales metric for the requested country and period."
    args["checks"]["question_interpretation"]["note"] = args["interpretation"]["question"]
    text = accepted(args, entry)
    assert "Revenue is 42 EUR" in text
    assert "2026\\-10\\-01 to 2026\\-11\\-01" in text
    assert "country: NL" in text
    assert "Cancelled orders and refunds may need different treatment" in text
    assert "ingestion is complete or duplicate records exist" in text
    assert "SQL intent equivalence remains unknown" in text
    assert "model assessment" in text
    for private in ("evidence-call", "binding", "definition_hash", "Execution context:", review.reference(entry)["evidence_hash"]):
        assert private not in text


def grouped_submission(count):
    entry = evidence("report_query", columns=["country", "revenue"],
                     rows=[[f"Market {i}", i * 10] for i in range(count)],
                     group_by=["country"], aggregate=[{"column": "revenue", "agg": "sum"}])
    args = submission(entry)
    args["interpretation"]["question"] = "Compare published revenue across markets using the returned grouped sums, with business coverage still exploratory."
    args["checks"]["question_interpretation"]["note"] = args["interpretation"]["question"]
    args["checks"]["business_meaning"]["note"] = "Revenue may mean booked sales or collected payments; the intended recognition rule remains unresolved."
    args["claims"] = []
    ref = {key: review.reference(entry)[key] for key in ("tool_result_id", "evidence_hash")}
    lines = ["Revenue by market:", "", "| Market | Revenue |", "| --- | --- |"]
    for row in range(count):
        for column in ("country", "revenue"):
            args["claims"].append({**ref, "id": f"{column}_{row}", "row": row, "column": column, "scope": "cell"})
        lines.append("| {{country_" + str(row) + "}} | {{revenue_" + str(row) + "}} |")
    lines.extend(["", "These groups describe the published result. Their business comparability still needs confirmation."])
    args["text"] = "\n".join(lines)
    return entry, args


@pytest.mark.parametrize("count", [16, 100])
def test_grouped_comparison_exceeds_old_cell_cap_without_echoing_context(count):
    entry, args = grouped_submission(count)
    assert len(args["claims"]) == count * 2
    assert len(review.encoded(args).encode()) < review.MAX_BYTES
    text = accepted(args, entry)
    assert "| Market 15 | 150 |" in text
    assert "business comparability still needs confirmation" in text
    assert len(text.encode()) < review.MAX_BYTES


@pytest.mark.parametrize("field,value", [
    ("metric_id", "profit"), ("report", "unrelated"), ("period", {"start": "2026-01-01", "end": "2026-02-01"}),
    ("filters", {}), ("unit", "people"), ("currency", "USD"), ("timezone", "UTC"), ("breakdown", ["country"]),
])
def test_intended_context_must_match_independently_of_exact_actual_echo(field, value):
    entry = evidence("metric_query")
    args = submission(entry)
    # The claim still echoes the genuine actual context; intention is separate.
    args["intended"][0]["context"][field] = value
    text, feedback = review.submit(args, state_for(entry), Box(entry))
    assert text is None and "Intended metric" in feedback


@pytest.mark.parametrize("field,value", [("date_range", {"column": "day", "from": "2026-10-01", "to": "2026-11-01"}),
                                        ("filters", {"country": ["DE"]}), ("group_by", ["country"]), ("dataset", "other")])
def test_report_intended_context_mismatch(field, value):
    entry = evidence("report_query", dataset="daily_sales")
    args = submission(entry)
    args["intended"][0]["context"][field] = value
    assert review.submit(args, state_for(entry), Box(entry))[0] is None


def test_source_intent_mismatch_and_exploration_without_canonical_binding():
    entry = evidence()
    args = submission(entry)
    assert "Exploratory source query" in accepted(args, entry)
    args["intended"][0]["context"]["source_id"] = 2
    assert review.submit(args, state_for(entry), Box(entry))[0] is None


@pytest.mark.parametrize("mutation", ["all_na", "all_unknown", "blank", "generic", "no_interpretation", "short_interpretation", "missing_intent", "duplicate_intent", "missing_metric", "unrelated_metric"])
def test_checklist_and_canonical_meaning_loopholes(mutation):
    entry = evidence("metric_query")
    args = submission(entry)
    if mutation in {"all_na", "all_unknown"}:
        for check in args["checks"].values():
            check["state"] = "not_applicable" if mutation == "all_na" else "unknown"
    elif mutation == "blank":
        args["checks"]["assumptions"]["note"] = ""
    elif mutation == "generic":
        for check in args["checks"].values():
            check["note"] = "Everything looks acceptable."
    elif mutation == "no_interpretation":
        del args["interpretation"]
    elif mutation == "short_interpretation":
        args["interpretation"]["question"] = "sales"
    elif mutation == "missing_intent":
        args["intended"] = []
    elif mutation == "duplicate_intent":
        args["intended"] *= 2
    else:
        args["interpretation"]["approach"] = "defined"
        args["checks"]["business_meaning"].update(state="assessed", evidence=args["checks"]["reconciliation"]["evidence"])
        if mutation == "missing_metric":
            del args["intended"][0]["context"]["metric_id"]
        else:
            args["intended"][0]["context"]["metric_id"] = "profit"
    assert review.submit(args, state_for(entry), Box(entry))[0] is None


def test_canonical_definition_match_is_only_model_assessed_business_meaning():
    entry = evidence("metric_query")
    args = submission(entry)
    args["interpretation"]["approach"] = "defined"
    args["checks"]["business_meaning"].update(state="assessed", evidence=args["checks"]["reconciliation"]["evidence"],
        note="The intended metric identifier matches the executed definition; its suitability remains a business judgment.")
    text = accepted(args, entry)
    assert "business meaning (assessed)" in text
    assert "does not prove the user's meaning" in text


def test_interpretation_and_intended_context_are_persisted_privately():
    entry = evidence("metric_query")
    args = submission(entry)
    state = state_for(entry)
    assert review.submit(args, state, Box(entry))[0]
    assert state["_answer_review"]["interpretation"] == args["interpretation"]
    assert state["_answer_review"]["intended"] == args["intended"]
    assert "intended" not in json.dumps(review.visible_transcript(state["transcript"]))


@pytest.mark.parametrize("check", ["period_bounds_timezone", "filters", "grain_units"])
def test_inapplicable_and_assessed_require_supporting_context(check):
    entry = evidence("metric_query")
    args = submission(entry)
    args["checks"][check]["state"] = "not_applicable"
    assert review.submit(args, state_for(entry), Box(entry))[0] is None
    args["checks"][check].update(state="assessed", evidence=args["checks"]["reconciliation"]["evidence"])
    assert accepted(args, entry)
    field = {"period_bounds_timezone": "period", "filters": "filters", "grain_units": "unit"}[check]
    del args["intended"][0]["context"][field]
    assert review.submit(args, state_for(entry), Box(entry))[0] is None


def test_not_applicable_is_distinct_from_unknown_and_requires_rationale():
    entry = evidence("report_query")
    args = submission(entry)
    args["checks"]["filters"].update(state="not_applicable", note="This is an unfiltered exploratory observation, with no requested business population restriction.")
    assert "filters (not_applicable): This is an unfiltered" in accepted(args, entry)
    args["checks"]["filters"]["note"] = ""
    assert review.submit(args, state_for(entry), Box(entry))[0] is None


@pytest.mark.parametrize("check", ["business_meaning", "period_bounds_timezone", "filters", "grain_units"])
def test_missing_source_semantics_cannot_be_dismissed_as_inapplicable(check):
    entry = evidence()
    args = submission(entry)
    args["checks"][check]["state"] = "not_applicable"
    assert review.submit(args, state_for(entry), Box(entry))[0] is None


def test_no_report_date_scope_can_be_explicitly_inapplicable():
    entry = evidence("report_query")
    args = submission(entry)
    args["checks"]["period_bounds_timezone"].update(state="not_applicable",
        note="The intended scope is the entire published result, without a date selection.")
    assert "period bounds timezone (not_applicable)" in accepted(args, entry)
    del args["intended"][0]["context"]["date_range"]
    assert review.submit(args, state_for(entry), Box(entry))[0] is None


@pytest.mark.parametrize("text", ["Revenue is 999.", "Revenue is ninety-nine.", "Growth was 5%.", "Revenue doubled.",
                                  "Revenue is &#57;&#57;.", "Revenue is ９９.", "Revenue is Ⅸ.",
                                  "Revenue is {{invented}}.", "Revenue is {{context:result:query}}.",
                                  "Revenue is {{result}", "This is verified: {{result}}.",
                                  "The unit is m2: {{result}}.", "On 2026-11-02: {{result}}."])
def test_analytical_text_cannot_introduce_unmapped_numeric_literals_or_refs(text):
    entry = evidence()
    args = submission(entry)
    args["text"] = text
    assert review.submit(args, state_for(entry), Box(entry))[0] is None


@pytest.mark.parametrize("mode", ["help", "greeting", "clarification", "limitation"])
@pytest.mark.parametrize("text", ["Revenue is 999.", "Revenue is forty euros.", "Sales doubled.", "Revenue is {{result}}.", "The result is verified."])
def test_non_data_modes_are_not_an_easy_numeric_or_assurance_bypass(mode, text):
    args = {"mode": mode, "text": text, "clarify": ["business_meaning"]}
    assert review.submit(args, state_for(), Box())[0] is None


@pytest.mark.parametrize("mode,text", [
    ("clarification", "Should revenue mean booked sales, invoiced sales or collected payments? Should refunds and tax be excluded?"),
    ("help", "Published reports contain built output. Source grants separately authorize read-only database exploration. A successful SQL query does not establish its business meaning."),
    ("help", 'The “Approved calculation” label describes the executor contract. It does not certify source quality or the assistant\'s explanation.'),
    ("greeting", "Hello! Tell me which report or business question you would like to explore."),
    ("limitation", "The available report omits refund status, so I cannot determine net revenue. Can you identify a source that records refunds?"),
])
def test_useful_specific_non_data_text_without_evidence_or_assurance(mode, text):
    args = {"mode": mode, "text": text, "clarify": ["business_meaning"]}
    result, feedback = review.submit(args, state_for(), Box())
    assert text in result and feedback is None
    assert result.startswith("Clarification (assistant-authored)" if mode == "clarification" else "Assistant explanation (not independently verified)")


@pytest.mark.parametrize("location", ["text", "interpretation", "note"])
def test_all_visible_model_text_uses_the_numerical_guard(location):
    entry = evidence()
    args = submission(entry)
    if location == "text":
        args["text"] = "Revenue is 123."
    elif location == "interpretation":
        args["interpretation"]["question"] = "The question asks about revenue of 123."
    else:
        args["checks"]["data_quality"]["note"] = "There are 123 missing rows."
    assert review.submit(args, state_for(entry), Box(entry))[0] is None


def test_data_values_labels_and_metadata_are_markdown_escaped():
    attack = '<img src=x onerror=alert(1)> | [open](https://bad.example) *bold* `code`\n# heading'
    entry = evidence("report_query", columns=[attack], rows=[[attack]], report_name=attack, filters={attack: [attack]})
    args = submission(entry)
    args["claims"][0].update(column=attack, expected=attack)
    text = accepted(args, entry)
    assert '[open](' not in text
    assert '*bold*' not in text
    assert '\\| \\[open\\]' in text
    assert '\n# heading' not in text
    assert r'\<img' in text


@pytest.mark.parametrize("bound", ["text", "interpretation", "claims", "intentions", "rendered_bytes"])
def test_new_fields_and_placeholder_expansion_are_bounded(bound):
    entry = evidence()
    args = submission(entry)
    if bound == "text":
        args["text"] = "x" * 12001
    elif bound == "interpretation":
        args["interpretation"]["question"] = "x" * 1001
    elif bound == "claims":
        args["claims"] *= review.MAX_CLAIMS + 1
    elif bound == "intentions":
        args["intended"] *= 33
    else:
        entry = evidence(rows=[["x" * 20000]])
        args = submission(entry)
        args["claims"][0]["expected"] = entry["provenance"]["rows"][0][0]
        args["text"] = "{{result}} " * 4
    assert review.submit(args, state_for(entry), Box(entry))[0] is None


def test_signed_native_batches_submission_redaction_and_followup_roundtrip(monkeypatch):
    from dataclasses import replace
    from apps.assistant import llm
    from apps.assistant.tests.test_message_sse import FakeClient, FakeResponse, TextBlock, ToolUseBlock
    from apps.assistant.tests.test_provider_runtime import CONFIG

    class Thinking:
        def model_dump(self):
            return {"type": "thinking", "thinking": "__PRIVATE_SIGNED_REASONING__", "signature": "unaltered-signature"}

    entry = evidence()
    entry["tool_use_id"] = "batch-last"
    args = submission(entry)
    bad = copy.deepcopy(args)
    bad["claims"][0]["expected"] = "__PRIVATE_REJECTED_ARGUMENT__"
    native = [
        FakeResponse([Thinking(), TextBlock("__PRIVATE_DRAFT__"), ToolUseBlock("batch-first", "query_source", {}),
                      ToolUseBlock("batch-second", "query_source", {})], "tool_use"),
        FakeResponse([Thinking(), ToolUseBlock("batch-third", "query_source", {}),
                      ToolUseBlock("batch-last", "query_source", {})], "tool_use"),
        FakeResponse([Thinking(), ToolUseBlock("reject-submit", review.NAME, bad)], "tool_use"),
        FakeResponse([Thinking(), ToolUseBlock("accept-submit", review.NAME, args)], "tool_use"),
        FakeResponse([Thinking(), ToolUseBlock("followup-submit", review.NAME, args)], "tool_use"),
    ]
    calls = []
    client = FakeClient(native, calls)
    monkeypatch.setattr(llm, "_anthropic_client", lambda config: client)
    config = replace(CONFIG, provider="anthropic", model="synthetic-signed-model")
    state = {}
    first = list(llm.stream_turn(config, state, "Count sales", toolbox=Box(), system_blocks=[], max_turns=6))
    assert "value: 42" in rendered(first)
    assert state["_answer_review"]["attempts"] == 1
    assert not state.get("_continuation_error")
    # Reconnect reloads only JSON state, including original native signed blocks.
    state = json.loads(json.dumps(state))
    second = list(llm.stream_turn(config, state, "Explain that count again", toolbox=Box(), system_blocks=[]))
    assert "value: 42" in rendered(second)
    assert state["_answer_review"]["attempts"] == 0
    assert not state.get("_continuation_error")
    replay = calls[-1]["messages"]
    native_assistants = [m for m in replay if m["role"] == "assistant" and m["content"][0].get("type") == "thinking"]
    assert len(native_assistants) == 4
    assert all(m["content"][0]["signature"] == "unaltered-signature" for m in native_assistants)
    rejected_call = next(b for m in native_assistants for b in m["content"] if b.get("id") == "reject-submit")
    assert rejected_call["input"] == bad  # Original signed provider continuation remains exact.
    assert any(len(m["content"]) == 2 and all(b.get("type") == "tool_result" for b in m["content"])
               for m in replay if isinstance(m["content"], list))
    outward = json.dumps(first + second + review.visible_transcript(state["transcript"]))
    stored_transcript = json.dumps(state["transcript"])
    for secret in ("__PRIVATE_DRAFT__", "__PRIVATE_SIGNED_REASONING__", "__PRIVATE_REJECTED_ARGUMENT__"):
        assert secret not in outward + stored_transcript
    assert review.NAME not in outward
    assert "__PRIVATE_REJECTED_ARGUMENT__" in json.dumps(state["_provider"])
    assert all(b["input"] == {} for e in state["transcript"] if e["role"] == "assistant"
               for b in e["content"] if b.get("name") == review.NAME)
