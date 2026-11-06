"""The shipped chat renderer displays reconciled evidence as literal text."""
import copy
import json
from pathlib import Path

import pytest
from django.conf import settings
from playwright.sync_api import expect

from apps.assistant import answer_review as review
from apps.assistant.tests.test_answer_review import Box, evidence, state_for, submission
from apps.assistant.tests.test_widget_browser import browser  # noqa: F401
from apps.assistant.views import _widget_bundle


def mount_answer(browser, text, *, width=1440, bundle=None):
    page = browser.new_page(viewport={"width": width, "height": 900 if width > 640 else 844})
    def respond(route):
        path = route.request.url.removeprefix("http://reports.test")
        if path == "/api/assistant/widget.js":
            route.fulfill(body=bundle or _widget_bundle(), content_type="application/javascript")
        elif path in {"/static/ui.css", "/static/themes.generated.css"}:
            route.fulfill(body=(settings.BASE_DIR / path.lstrip("/")).read_text(), content_type="text/css")
        elif "/available" in path:
            route.fulfill(json={"available": True, "suggestions": [], "studio_name": "Synthetic studio"})
        elif path.endswith("/sessions"):
            route.fulfill(json={"id": 7, "scope": "studio", "report": None})
        elif path.endswith("/sessions/7/message"):
            events = [{"type": "turn_start"}, {"type": "text_delta", "text": text}, {"type": "done"}]
            route.fulfill(body="".join("data: " + json.dumps(event) + "\n\n" for event in events), content_type="text/event-stream")
        elif route.request.resource_type == "document":
            route.fulfill(body='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Synthetic review</title><link rel="stylesheet" href="/static/themes.generated.css"><link rel="stylesheet" href="/static/ui.css"></head><body class="mgmt"><script src="/api/assistant/widget.js"></script></body></html>', content_type="text/html")
        else:
            route.fulfill(status=204)
    page.route("http://reports.test/**", respond)
    page.goto("http://reports.test/s/demo/casino/reports")
    page.locator("#assistantPill" if width <= 640 else "#assistantAsk").click()
    page.locator("#assistantInput").fill("Compare revenue by market")
    page.locator("#assistantSend").click()
    expect(page.locator(".assistant-msg.assistant")).to_be_visible()
    return page


def synthetic_answer():
    label = 'NL | [team](/s/demo/casino/reports) *all* `x` <img src=x onerror=window.pwned=1> & Co \\ path'
    entry = evidence("report_query", columns=["country", "revenue"], rows=[[label, 1.25]],
                     group_by=["country"], report_name="Synthetic sales")
    args = submission(entry)
    args["interpretation"]["question"] = "Compare published revenue by market, preserving the returned country label and recorded value."
    args["checks"]["question_interpretation"]["note"] = args["interpretation"]["question"]
    args["checks"]["business_meaning"]["note"] = "Revenue may mean booked sales or collected payments; the intended recognition rule remains unresolved."
    args["claims"][0].update(column="country", expected=label)
    amount = copy.deepcopy(args["claims"][0])
    amount.update(id="amount", column="revenue", expected=1.25)
    args["claims"].append(amount)
    args["text"] = ("Revenue by market:\n\n| Market | Revenue |\n| --- | --- |\n| {{result}} | {{amount}} |\n\n"
                    "The groups describe the published result. Refund treatment still needs confirmation.")
    text, feedback = review.submit(args, state_for(entry), Box(entry))
    assert text and feedback is None, feedback
    return text, label


@pytest.mark.parametrize("width", [1440, pytest.param(390, marks=pytest.mark.heavy)])
def test_answer_values_labels_and_table_pipes_stay_literal(browser, width):  # noqa: F811
    text, label = synthetic_answer()
    page = mount_answer(browser, text, width=width)
    try:
        answer = page.locator(".assistant-msg.assistant")
        expect(answer.locator("tbody tr")).to_have_count(1)
        expect(answer.locator("td")).to_have_count(2)
        expect(answer.locator("td").first).to_have_text(label)
        expect(answer.locator("td.num")).to_have_text("1.25")
        expect(answer.locator("td a, td em, td code, td img")).to_have_count(0)
        expect(answer).to_contain_text("Cancelled orders and refunds may need different treatment")
        assert page.evaluate("window.pwned || 0") == 0
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        artifacts = Path("/tmp/trellum285-review")
        artifacts.mkdir(exist_ok=True)
        page.locator("#assistantMessages").evaluate("el => el.scrollTop = 0")
        page.screenshot(path=str(artifacts / f"answer-{width}.png"))
    finally:
        page.close()
