"""Browser regressions for full-width documents and workspace overlays."""

import time

import pytest

from trellum.runner.execute import run_report
from trellum.testing.test_workspace_journey import _pane, _queue_in_widget
from trellum.testing.test_workspace_journey import (
    workspace_browser_project as workspace_browser_project,
)


def _source(page, name="Source One"):
    frame = page.frame_locator("#fw-workspace-report")
    frame.get_by_role("heading", name=name, exact=True).wait_for()
    frame.locator("#fwLoading").wait_for(state="hidden")
    page.wait_for_function("document.getElementById('fw-workspace-report').dataset.ready === 'true'")
    page.locator("#fw-workspace-report").evaluate("""async el => {
        const win = el.contentWindow;
        await win.document.fonts.ready;
        for (let i = 0; i < 4; i++) await new Promise(resolve => win.requestAnimationFrame(resolve));
    }""")
    return frame


def _make_long_reports(destination):
    for slug in ("source-one", "source-two"):
        source_dir = destination.parent / slug
        generator = (source_dir / "generator.py").read_text()
        generator += '        ctx.add_section("More conversions", [BarChart(df, x="channel", y="conversion", title="More conversion by channel", dataset_id="conversions")])\n' * 4
        (source_dir / "generator.py").write_text(generator)
        run_report(str(source_dir))


def _dimensions(page):
    return page.locator("#fw-workspace-report").evaluate("""el => {
        const frame = el.getBoundingClientRect();
        const chart = el.contentDocument.querySelector('canvas').getBoundingClientRect();
        return [frame.width, frame.height, chart.width, chart.height];
    }""")


@pytest.mark.parametrize("viewport", [{"width": 1366, "height": 768}, {"width": 390, "height": 844}])
def test_one_document_and_queue_overlay_preserve_geometry(workspace_browser_project, viewport):
    from playwright.sync_api import sync_playwright

    origin, _, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport=viewport)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            _pane(page, "Reports")
            source = _source(page)
            assert page.locator(".fw-tabs button[data-tab]").count() == 2
            assert page.locator(".fw-pane:visible").count() == 1
            assert not page.locator("#fw-workspace-analysis").is_visible()
            assert page.locator("#fw-workspace-report").bounding_box()["width"] >= viewport["width"] - 50
            assert not source.locator("#fwrvBar").is_visible()
            assert not source.locator("#fwrvPanel").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            source.locator("canvas").evaluate("() => window.layoutDocumentMarker = 'same-document'")
            # A settled chart keeps its dimensions while the overlay opens and closes.
            page.wait_for_function("document.getElementById('fw-workspace-report').contentWindow._chartInstances && Object.keys(document.getElementById('fw-workspace-report').contentWindow._chartInstances).length > 0")
            before = _dimensions(page)
            _pane(page, "Queue")
            dialog = page.locator("#queue-dialog")
            assert dialog.evaluate("el => el.matches(':modal')")
            assert _dimensions(page) == before
            assert page.locator("#chat-note").is_visible()
            assert dialog.evaluate("el => el.contains(document.activeElement)")
            focusable = dialog.locator("button:enabled:visible, textarea:enabled:visible, select:enabled:visible, input:enabled:visible")
            focusable.last.focus()
            page.keyboard.press("Tab")
            assert dialog.evaluate("el => el.contains(document.activeElement)")
            focusable.first.focus()
            page.keyboard.press("Shift+Tab")
            assert dialog.evaluate("el => el.contains(document.activeElement)")
            page.keyboard.press("Escape")
            dialog.wait_for(state="hidden")
            assert page.locator("#queue-toggle").evaluate("el => el === document.activeElement")
            assert _dimensions(page) == before
            _pane(page, "Analysis")
            assert page.locator(".fw-pane:visible").count() == 1
            _pane(page, "Reports")
            assert source.locator("canvas").evaluate("() => window.layoutDocumentMarker") == "same-document"
            assert _dimensions(page) == before
            page.screenshot(path=f"/tmp/trellum-full-width-{viewport['width']}.png", full_page=True)
            assert not errors
        finally:
            browser.close()


@pytest.mark.parametrize("width", [1366, 390])
def test_clean_view_restores_document_and_supports_iframe_escape_and_touch(workspace_browser_project, width):
    from playwright.sync_api import sync_playwright

    origin, _, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": 844}, has_touch=width == 390)
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            _pane(page, "Reports")
            source = _source(page)
            source.locator("canvas").evaluate("() => window.cleanDocumentMarker = 'same-document'")
            page.locator("#content-only").click()
            assert not page.locator(".fw-header").is_visible()
            assert not page.locator(".fw-tabs").is_visible()
            assert not page.locator(".fw-report-head").is_visible()
            assert not source.locator("#fwrvBar").is_visible()
            assert not source.locator("#fwrvPanel").is_visible()
            box = page.locator("#fw-workspace-report").bounding_box()
            assert box["width"] >= width - 2 and box["height"] >= 842
            source.locator("canvas").click()
            page.keyboard.press("Escape")
            page.locator("#content-only").wait_for(state="visible")
            assert page.locator('[data-tab="reports"]').get_attribute("aria-current") == "page"
            assert source.locator("canvas").evaluate("() => window.cleanDocumentMarker") == "same-document"
            page.locator("#content-only").click()
            exit_button = page.locator("#exit-content-only")
            if width == 390:
                exit_button.tap()
            else:
                exit_button.click()
            page.locator("#content-only").wait_for(state="visible")
            assert source.locator("canvas").evaluate("() => window.cleanDocumentMarker") == "same-document"
        finally:
            browser.close()


def test_source_filters_and_scroll_survive_page_and_source_navigation(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    origin, _, destination = workspace_browser_project
    _make_long_reports(destination)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            source_requests = []

            def delayed_source_data(route):
                source_requests.append(route.request.url)
                if len(source_requests) > 1:
                    time.sleep(0.4)
                route.continue_()

            page.route("**/source-one/data.json*", delayed_source_data)
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            _pane(page, "Reports")
            source = _source(page)
            source.locator("select.fw-dropdown").evaluate("el => el._fwSlimSelect.setSelected(['NL'])")
            page.wait_for_function("new URL(document.getElementById('fw-workspace-report').contentWindow.location.href).searchParams.get('region') === 'NL'")
            source.locator("canvas").first.evaluate("() => window.scrollTo(0, 400)")
            page.wait_for_function("Math.abs(document.getElementById('fw-workspace-report').contentWindow.scrollY - 400) <= 2")
            _pane(page, "Analysis")
            _pane(page, "Reports")
            page.wait_for_function("Math.abs(document.getElementById('fw-workspace-report').contentWindow.scrollY - 400) <= 2")
            page.locator("#content-only").click()
            page.wait_for_function("document.getElementById('fw-workspace-report').contentDocument.querySelector('.fwrv-root').inert")
            source.locator("canvas").first.evaluate("() => { window.scrollTo(0, 900); document.body.tabIndex=-1; document.body.focus({preventScroll:true}); }")
            page.keyboard.press("Escape")
            page.locator("#content-only").wait_for(state="visible")
            page.wait_for_function("Math.abs(document.getElementById('fw-workspace-report').contentWindow.scrollY - 400) <= 2")
            page.locator("#source-select").select_option("source-two")
            source = _source(page, "Source Two")
            source.locator("select.fw-dropdown").evaluate("el => el._fwSlimSelect.setSelected(['DE'])")
            page.wait_for_function("new URL(document.getElementById('fw-workspace-report').contentWindow.location.href).searchParams.get('region') === 'DE'")
            source.locator("canvas").first.evaluate("() => window.scrollTo(0, 700)")
            page.locator("#source-select").select_option("source-one")
            source = _source(page)
            page.wait_for_function("Math.abs(document.getElementById('fw-workspace-report').contentWindow.scrollY - 400) <= 2")
            assert source.locator("select.fw-dropdown").evaluate("el => el._fwSlimSelect.getSelected()") == ["NL"]
            page.locator("#source-select").select_option("source-two")
            source = _source(page, "Source Two")
            page.wait_for_function("Math.abs(document.getElementById('fw-workspace-report').contentWindow.scrollY - 700) <= 2")
            assert source.locator("select.fw-dropdown").evaluate("el => el._fwSlimSelect.getSelected()") == ["DE"]
            assert len(source_requests) >= 2
        finally:
            browser.close()


def test_queue_edits_and_removal_update_both_loaded_widgets(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    origin, _, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            page.locator("#global-select").wait_for(state="visible")
            assert page.locator("#global-select").evaluate("el => getComputedStyle(el).fontSize") == "12px"
            page.get_by_role("button", name="Chat & queue").click()
            assert page.locator("#queue-summary").inner_text() == "Unsent annotations"
            assert "No unsent annotations" in page.locator("#queue-list").inner_text()
            assert "Nothing queued yet" not in page.locator("#queue-list").inner_text()
            assert page.get_by_role("heading", name="Conversation").is_visible()
            assert page.locator("#send").evaluate("el => el.classList.contains('fwrv-pri')")
            page.locator("#queue-close").click()
            _pane(page, "Reports")
            source = _source(page)
            _queue_in_widget(page, source, "Original source request")
            page.wait_for_function("document.getElementById('queue-list').textContent.includes('Snapshot ready')")
            _pane(page, "Queue")
            page.locator("#queue-list textarea[data-fw-request-id]").fill("Edited source request")
            page.wait_for_function("['fw-workspace-analysis', 'fw-workspace-report'].every(id => document.getElementById(id).contentDocument.querySelector('#fwrvList textarea').value === 'Edited source request')")
            page.get_by_role("button", name="Remove queued request 1", exact=True).click()
            page.wait_for_function("document.getElementById('queue-count').textContent === '0' && ['fw-workspace-analysis', 'fw-workspace-report'].every(id => document.getElementById(id).contentDocument.getElementById('fwrvCount').textContent === '0')")
            assert not page.locator("#queue-list textarea").count()
        finally:
            browser.close()


def test_pending_snapshot_keeps_source_visible_and_blocks_clean_resize(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    origin, _, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            _pane(page, "Reports")
            source = _source(page)
            source.locator("canvas").evaluate("""() => {
                const capture = fw.captureElementSnapshotForAnalysis;
                fw.captureElementSnapshotForAnalysis = async (...args) => {
                    await new Promise(resolve => { window.releaseLayoutSnapshot = resolve; });
                    return capture(...args);
                };
            }""")
            _queue_in_widget(page, source, "Keep this report rendered during capture")
            page.wait_for_function("typeof document.getElementById('fw-workspace-report').contentWindow.releaseLayoutSnapshot === 'function'")
            before = _dimensions(page)
            page.locator('[data-tab="analysis"]').evaluate("el => el.click()")
            page.locator("#content-only").evaluate("el => el.click()")
            page.locator("#source-select").evaluate("el => { el.value='source-two'; el.dispatchEvent(new Event('change', {bubbles:true})); }")
            assert page.locator('[data-tab="reports"]').get_attribute("aria-current") == "page"
            assert page.locator(".fw-header").is_visible()
            assert page.locator("#fw-workspace-report").get_attribute("data-slug") == "source-one"
            assert _dimensions(page) == before
            source.locator("canvas").evaluate("() => window.releaseLayoutSnapshot()")
            page.wait_for_function("document.getElementById('queue-list').textContent.includes('Snapshot ready')")
            assert "Snapshot ready" in page.locator("#queue-list").inner_text()
        finally:
            browser.close()


def test_failed_or_obsolete_source_build_keeps_the_last_report(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    origin, _, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            mode = {"source_two": "ready"}

            def source_state(route):
                response = route.fetch()
                state = response.json()
                for report in state["reports"]:
                    if report["slug"] == "source-two":
                        report["build_state"] = mode["source_two"]
                        if mode["source_two"] == "failed":
                            report["error"] = "Synthetic build failure"
                route.fulfill(response=response, json=state)

            page.route("**/_fw/review/workspace/state?*", source_state)
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            _pane(page, "Reports")
            _source(page)
            mode["source_two"] = "failed"
            with page.expect_response("**/_fw/review/workspace/state?*"):
                pass
            page.locator("#source-select").select_option("source-two")
            page.locator("#source-status").get_by_text("Synthetic build failure", exact=False).wait_for()
            assert page.locator("#fw-workspace-report").get_attribute("data-slug") == "source-one"
            assert page.locator("#retry-build").is_visible()
            assert page.locator("#global-select").is_disabled()
            mode["source_two"] = "building"
            with page.expect_response("**/_fw/review/workspace/state?*"):
                pass
            page.locator("#source-status").get_by_text("Building report", exact=False).wait_for()
            assert page.locator("#fw-workspace-report").get_attribute("data-slug") == "source-one"
            assert page.locator("#global-select").is_disabled()
            page.locator("#source-select").select_option("source-one")
            mode["source_two"] = "ready"
            with page.expect_response("**/_fw/review/workspace/state?*"):
                pass
            assert page.locator("#source-select").input_value() == "source-one"
            assert page.locator("#fw-workspace-report").get_attribute("data-slug") == "source-one"
        finally:
            browser.close()


@pytest.mark.parametrize("build_state", ["missing", "stale"])
def test_missing_source_build_requires_one_explicit_click(workspace_browser_project, build_state):
    from playwright.sync_api import sync_playwright

    origin, _, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            admissions = []
            state_reads = []
            built = {"source-two": False}

            def source_state(route):
                response = route.fetch()
                state = response.json()
                state_reads.append(1)
                for report in state["reports"]:
                    if report["slug"] == "source-two" and not built["source-two"]:
                        report["build_state"] = build_state
                route.fulfill(response=response, json=state)

            def admit(route):
                admissions.append(route.request.post_data_json)
                built["source-two"] = True
                route.fulfill(json={"build_state": "building"})

            page.clock.install()
            page.add_init_script("""(() => {
                window.reviewStateReads = 0;
                const fetch = window.fetch;
                window.fetch = function(url, options) {
                    return fetch.call(this, url, options).then(response => {
                        if (String(url).includes('/workspace/state')) window.reviewStateReads++;
                        return response;
                    });
                };
            })();""")
            page.route("**/_fw/review/workspace/state?*", source_state)
            page.route("**/_fw/review/workspace/open", admit)
            page.goto(origin + "/_fw/review/workspace?source=source-two")
            _pane(page, "Reports")
            expected_status = "not been built locally" if build_state == "missing" else "out of date"
            page.locator("#source-status").get_by_text(expected_status, exact=False).wait_for()
            page.wait_for_function("document.getElementById('fw-workspace-report').dataset.slug !== 'source-two'")
            page.clock.fast_forward(3000)
            page.wait_for_function("window.reviewStateReads >= 2")
            assert len(state_reads) >= 2
            assert admissions == []
            page.locator("#source-select").select_option("")
            page.go_back()
            assert page.locator("#source-select").input_value() == "source-two"
            assert admissions == []
            page.reload()
            page.locator("#source-status").get_by_text(expected_status, exact=False).wait_for()
            assert admissions == []

            # Repeated synchronous activations share the same pending admission.
            page.locator("#open-report").evaluate("el => { el.click(); el.click(); }")
            page.wait_for_function("document.getElementById('fw-workspace-report').dataset.slug === 'source-two' && document.getElementById('fw-workspace-report').dataset.ready === 'true'")
            assert len(admissions) == 1
        finally:
            browser.close()


def test_history_restores_page_and_selected_source(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    origin, _, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            _pane(page, "Reports")
            _source(page)
            page.locator("#source-select").select_option("source-two")
            _source(page, "Source Two")
            _pane(page, "Analysis")
            assert page.locator("#analysis-tab").get_attribute("aria-current") == "page"
            page.go_back()
            _source(page, "Source Two")
            assert page.locator("#reports-tab").get_attribute("aria-current") == "page"
            assert page.locator("#source-select").input_value() == "source-two"
            page.go_back()
            _source(page)
            assert page.locator("#reports-tab").get_attribute("aria-current") == "page"
            assert page.locator("#source-select").input_value() == "source-one"
            page.go_forward()
            _source(page, "Source Two")
            assert page.locator("#reports-tab").get_attribute("aria-current") == "page"
            assert page.locator("#source-select").input_value() == "source-two"
            page.go_forward()
            assert page.locator("#analysis-tab").get_attribute("aria-current") == "page"
            assert page.locator(".fw-pane:visible").count() == 1
        finally:
            browser.close()


def test_inactive_analysis_rebuild_preserves_scrolled_source(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    origin, _, destination = workspace_browser_project
    _make_long_reports(destination)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            mode = {"rebuilt": False}

            def rebuilt_analysis(route):
                response = route.fetch()
                state = response.json()
                if mode["rebuilt"]:
                    state["analysis"]["version"] = str(state["analysis"]["version"]) + "-rebuilt"
                route.fulfill(response=response, json=state)

            page.route("**/_fw/review/workspace/state?*", rebuilt_analysis)
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            _pane(page, "Reports")
            source = _source(page)
            old_frame = page.locator("#fw-workspace-analysis").get_attribute("data-frame-id")
            source.locator("canvas").first.evaluate("() => window.scrollTo(0, 900)")
            page.wait_for_function("Math.abs(document.getElementById('fw-workspace-report').contentWindow.scrollY - 900) <= 2")
            mode["rebuilt"] = True
            with page.expect_response("**/_fw/review/workspace/state?*"):
                pass
            page.wait_for_function("old => { const frame=document.getElementById('fw-workspace-analysis'); return frame.dataset.frameId !== old && frame.dataset.ready === 'true'; }", arg=old_frame)
            assert abs(source.locator("canvas").first.evaluate("() => window.scrollY") - 900) <= 2
            assert page.locator("#reports-tab").get_attribute("aria-current") == "page"
        finally:
            browser.close()


def test_failed_source_data_disables_selection_and_recovers(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    origin, _, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            page.goto(origin + "/_fw/review/workspace?source=source-one")
            _pane(page, "Reports")
            _source(page)
            page.route("**/source-two/data.json*", lambda route: route.abort("failed"))
            page.locator("#source-select").select_option("source-two")
            source = page.frame_locator("#fw-workspace-report")
            source.get_by_text("Failed to load data", exact=True).wait_for()
            assert page.locator("#global-select").is_disabled()
            assert page.locator("#content-only").is_disabled()
            page.get_by_role("button", name="Reload report", exact=True).wait_for()
            page.unroute("**/source-two/data.json*")
            page.get_by_role("button", name="Reload report", exact=True).click()
            _source(page, "Source Two")
            page.wait_for_function("!document.getElementById('global-select').disabled")
            page.locator("#source-select").select_option("source-one")
            _source(page)
            page.wait_for_function("!document.getElementById('global-select').disabled")
            assert page.locator("#fw-workspace-report").get_attribute("data-slug") == "source-one"
        finally:
            browser.close()


def test_back_restores_reports_without_a_source_selection(workspace_browser_project):
    from playwright.sync_api import sync_playwright

    origin, _, _ = workspace_browser_project
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            page.goto(origin + "/_fw/review/workspace")
            _pane(page, "Reports")
            page.wait_for_function("Array.from(document.getElementById('source-select').options).some(option => option.value === 'source-one')")
            page.locator("#source-select").select_option("source-one")
            _source(page)
            page.go_back()
            assert page.locator("#reports-tab").get_attribute("aria-current") == "page"
            assert page.locator("#source-select").input_value() == ""
            assert not page.locator("#fw-workspace-report").is_visible()
            assert page.locator("#global-select").is_disabled()
            page.go_forward()
            _source(page)
            assert page.locator("#source-select").input_value() == "source-one"
        finally:
            browser.close()
