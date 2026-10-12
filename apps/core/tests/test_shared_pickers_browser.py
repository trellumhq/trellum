"""Shared pickers retain native form values, validation and usable viewport geometry."""
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", params=("chromium", "webkit"))
def browser(request):
    with sync_playwright() as playwright:
        instance = getattr(playwright, request.param).launch(headless=True)
        yield instance
        instance.close()


def html():
    people = "".join(
        f'<label><input type="checkbox" name="people" value="{i}" '
        f'data-recipient-name="Person {i}" data-recipient-identity="person{i}@example.test">Person {i}</label>'
        for i in range(120)
    )
    return f"""<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body class="mgmt"><main><div class="ui-form-rail"><header><h1>Schedule</h1></header>
    <form id="schedule"><section class="ui-form-section"><div class="ui-form-row">
    <div class="ui-field"><label for="zone">Timezone</label><select id="zone" name="timezone" required data-choice-picker>
    <option value="">Choose timezone</option><optgroup label="Europe"><option value="Europe/Amsterdam">Europe / Amsterdam</option>
    <option value="Europe/London">Europe / London</option><option value="Europe/Paris" disabled>Europe / Paris</option></optgroup>
    <optgroup label="Unavailable" disabled><option value="Asia/Tokyo">Asia / Tokyo</option></optgroup>
    <optgroup label="Hidden" hidden><option value="Hidden/Zone">Hidden zone</option></optgroup></select></div>
    <div class="ui-field"><label for="freq">Frequency</label><select id="freq" name="freq"><option>Daily</option></select></div>
    <div class="ui-field ui-field-wide" id="recipients" data-recipient-picker data-picker-label="Recipients">
    <label>Recipients</label><div data-recipient-type="people">{people}</div>
    <div data-recipient-type="roles"><label><input type="checkbox" name="roles" value="admins">All admins</label>
    <label><input type="checkbox" name="roles" value="developers">All developers</label></div>
    <div data-recipient-type="groups"><label><input type="checkbox" name="groups" value="finance">Finance group</label>
    <label><input type="checkbox" name="groups" value="disabled" disabled>Unavailable group</label></div></div>
    </div><button id="after" type="button">After pickers</button><button type="submit">Save</button></section></form>
    </div></main><script>window.changes=[]; document.querySelector('form').addEventListener('change',e=>changes.push(e.target.name));</script>
    </body></html>"""


def page_for(browser, width=1440, theme="light", *, enhance=True, portal_css=True):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    page.set_content(html())
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    if portal_css:
        page.add_style_tag(content=(ROOT / "static/ui.css").read_text(encoding="utf-8"))
    for name in ("choice-picker.css", "recipient-picker.css"):
        page.add_style_tag(content=(ROOT / "static" / name).read_text(encoding="utf-8"))
    if enhance:
        for name in ("choice-picker.js", "recipient-picker.js"):
            page.add_script_tag(content=(ROOT / "static" / name).read_text(encoding="utf-8"))
    return context, page


def test_wrapping_label_allows_clicking_and_typing_in_search(browser):
    context, page = page_for(browser, enhance=False)
    page.evaluate("""() => {
        const select = document.querySelector('#zone');
        document.querySelector('label[for=zone]').append(select);
    }""")
    page.add_script_tag(content=(ROOT / "static/choice-picker.js").read_text(encoding="utf-8"))
    page.locator('.ui-picker-trigger').click()
    search = page.locator('.ui-picker-search')
    search.click()
    expect(search).to_be_focused()
    page.keyboard.type('London')
    expect(page.locator('.ui-picker-option')).to_have_count(1)
    page.get_by_role('button', name='Europe / London', exact=True).click()
    expect(page.locator('#zone')).to_have_value('Europe/London')
    context.close()


def test_single_choice_browse_search_keyboard_and_native_payload(browser):
    context, page = page_for(browser)
    root = page.locator(".ui-choice-picker")
    trigger = root.locator(".ui-picker-trigger")
    trigger.click()
    expect(root.locator(".ui-picker-option")).to_have_count(5)
    expect(root.get_by_role("button", name="Europe / Paris")).to_be_disabled()
    expect(root.get_by_role("button", name="Asia / Tokyo")).to_be_disabled()
    search = root.locator(".ui-picker-search")
    search.fill("London")
    search.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(page.locator("#zone")).to_have_value("Europe/London")
    expect(trigger).to_be_focused()
    expect(trigger).to_have_attribute("aria-label", "Timezone: Europe / London")
    assert page.evaluate("new FormData(document.querySelector('form')).get('timezone')") == "Europe/London"
    assert page.evaluate("changes") == ["timezone"]
    trigger.click()
    expect(search).to_have_value("")
    expect(root.locator(".ui-picker-option")).to_have_count(5)
    search.fill("no such timezone")
    expect(root.locator(".ui-picker-message")).to_have_text("No matches.")
    search.press("Escape")
    expect(trigger).to_be_focused()
    expect(root.locator(".ui-picker-popup")).to_be_hidden()
    context.close()


def test_server_validation_is_visible_below_closed_choice_and_keeps_selection(browser):
    context, page = page_for(browser)
    page.evaluate("""() => {
        const form = document.querySelector('form');
        form.setAttribute('data-settings-form', '');
        form.setAttribute('data-settings-save', '');
        const zone = document.querySelector('#zone');
        zone.value = 'Europe/Amsterdam';
        zone.dispatchEvent(new Event('change', {bubbles:true}));
        window.fetch = () => Promise.resolve(new Response(JSON.stringify({
            ok:false, errors:{timezone:['This timezone is unavailable for this schedule.']}
        }), {status:400, headers:{'Content-Type':'application/json'}}));
    }""")
    page.add_script_tag(content=(ROOT / 'static/settings-forms.js').read_text(encoding='utf-8'))
    page.get_by_role('button', name='Save', exact=True).click()
    error = page.locator('[data-settings-error]')
    expect(error).to_be_visible()
    trigger = page.locator('.ui-choice-picker .ui-picker-trigger')
    expect(trigger).to_have_attribute('aria-invalid', 'true')
    expect(page.locator('#zone')).to_have_value('Europe/Amsterdam')
    assert error.bounding_box()['y'] >= trigger.bounding_box()['y'] + trigger.bounding_box()['height']
    context.close()


def test_required_single_choice_focuses_visible_trigger_and_mirrors_errors(browser):
    context, page = page_for(browser)
    trigger = page.locator(".ui-choice-picker .ui-picker-trigger")
    assert not page.evaluate("document.querySelector('form').reportValidity()")
    expect(trigger).to_be_focused()
    expect(trigger).to_have_attribute("aria-invalid", "true")
    expect(page.locator(".ui-picker-error")).to_be_visible()
    page.evaluate("""const field=document.querySelector('#zone'); field.value='Europe/Amsterdam';
        field.dispatchEvent(new Event('change',{bubbles:true}));""")
    expect(trigger).not_to_have_attribute("aria-invalid", "true")
    page.evaluate("""const field=document.querySelector('#zone'); field.setAttribute('aria-invalid','true');
        field.setAttribute('aria-describedby','server-error'); field.insertAdjacentHTML('afterend','<p id="server-error">Rejected zone</p>');""")
    expect(trigger).to_have_attribute("aria-invalid", "true")
    expect(trigger).to_have_attribute("aria-describedby", "server-error")
    expect(page.locator("#server-error")).to_be_visible()
    page.evaluate("document.querySelector('#zone').disabled=true")
    expect(trigger).to_be_disabled()
    context.close()


def test_combined_recipients_filters_chips_keyboard_and_formdata(browser):
    context, page = page_for(browser, 390)
    root = page.locator("#recipients")
    trigger = root.locator(".ui-picker-trigger")
    trigger.click()
    expect(root.locator(".recipient-picker-option")).to_have_count(124)
    root.get_by_role("button", name="People", exact=True).click()
    search = root.locator(".ui-picker-search")
    search.fill("person99@")
    search.press("ArrowDown")
    page.keyboard.press("Space")
    expect(root.locator(".ui-picker-popup")).to_be_visible()
    expect(root.locator(".recipient-picker-selected")).to_contain_text("Person 99")
    search.fill("")
    root.get_by_role("button", name="Roles", exact=True).click()
    root.locator('input[value="admins"]').check()
    root.get_by_role("button", name="Groups", exact=True).click()
    root.locator('input[value="finance"]').check()
    expect(trigger).to_have_attribute("aria-label", "Recipients: 3 selected")
    expect(root.locator(".recipient-picker-selected")).to_contain_text("Role: All admins")
    expect(root.locator(".recipient-picker-selected")).to_contain_text("Group: Finance group")
    root.get_by_role("button", name="Done", exact=True).click()
    expect(trigger).to_be_focused()
    root.get_by_role("button", name="Remove Person 99", exact=True).click()
    expect(root.get_by_role("button", name="Remove Role: All admins", exact=True)).to_be_focused()
    assert page.evaluate("Object.fromEntries(new FormData(document.querySelector('form')))") == {
        "timezone": "", "freq": "Daily", "roles": "admins", "groups": "finance"
    }
    root.get_by_role("button", name="Remove Role: All admins", exact=True).click()
    root.get_by_role("button", name="Remove Group: Finance group", exact=True).click()
    expect(trigger).to_be_focused()
    expect(root.locator(".recipient-picker-status")).to_contain_text("0 selected")
    context.close()


def test_popup_tab_escape_and_outside_do_not_trap_or_close_outer_dialog(browser):
    context, page = page_for(browser)
    page.evaluate("document.addEventListener('keydown',e=>{if(e.key==='Escape')window.outerClosed=true;})")
    root = page.locator("#recipients")
    trigger = root.locator(".ui-picker-trigger")
    trigger.press("Enter")
    root.locator(".ui-picker-search").press("Escape")
    assert not page.evaluate("Boolean(window.outerClosed)")
    expect(trigger).to_be_focused()
    trigger.click()
    root.get_by_role("button", name="Done").focus()
    page.keyboard.press("Tab")
    expect(page.locator("#after")).to_be_focused()
    expect(root.locator(".ui-picker-popup")).to_be_hidden()
    trigger.click()
    page.locator("h1").click()
    expect(root.locator(".ui-picker-popup")).to_be_hidden()
    context.close()


def test_programmatic_values_reinitialization_new_regions_reset_and_disabled(browser):
    context, page = page_for(browser)
    page.evaluate("""document.querySelector('#zone').value='Europe/London';
        document.querySelector('input[value="99"]').checked=true;
        TrellumChoices.init(document); TrellumRecipients.init(document);
        document.body.insertAdjacentHTML('beforeend','<div data-choice-picker></div><label for="new">New</label><select id="new" name="new" data-choice-picker><option>A</option></select>');
        document.dispatchEvent(new Event('settings:updated'));""")
    expect(page.locator(".ui-choice-picker")).to_have_count(2)
    expect(page.locator("#recipients .ui-picker-trigger")).to_contain_text("1 selected")
    expect(page.locator("#zone").locator("..").locator(".ui-picker-trigger")).to_contain_text("London")
    for name in ("choice-picker.js", "recipient-picker.js"):
        page.add_script_tag(content=(ROOT / "static" / name).read_text(encoding="utf-8"))
    expect(page.locator(".ui-choice-picker")).to_have_count(2)
    expect(page.locator("#recipients .recipient-picker")).to_have_count(1)
    page.evaluate("document.querySelector('form').reset()")
    expect(page.locator("#recipients .ui-picker-trigger")).to_contain_text("Choose recipients")
    expect(page.locator("#zone").locator("..").locator(".ui-picker-trigger")).to_contain_text("Choose timezone")
    page.evaluate("document.querySelectorAll('#recipients input').forEach(node=>node.disabled=true)")
    expect(page.locator("#recipients .ui-picker-trigger")).to_be_disabled()
    context.close()


def test_empty_and_legacy_checkbox_groups_keep_fallback_errors(browser):
    context, page = page_for(browser, enhance=False)
    page.evaluate("""document.body.insertAdjacentHTML('beforeend',
        '<div id="legacy" data-recipient-picker><label>Roles</label><div><label><input name="legacy" value="admins" type="checkbox" checked>Admins</label><ul class="errorlist"><li>Choose an eligible role</li></ul></div></div>'+
        '<div id="empty" data-recipient-picker data-picker-empty-state="No permission groups are available."><label>Groups</label></div>');""")
    for name in ("choice-picker.js", "recipient-picker.js"):
        page.add_script_tag(content=(ROOT / "static" / name).read_text(encoding="utf-8"))
    expect(page.locator("#legacy .errorlist")).to_be_visible()
    expect(page.locator("#legacy .ui-picker-trigger")).to_contain_text("1 selected")
    page.locator("#empty .ui-picker-trigger").click()
    expect(page.locator("#empty .ui-picker-message")).to_have_text("No permission groups are available.")
    context.close()


@pytest.mark.parametrize("width,theme", [(1440, "light"), (1440, "dark"), (390, "light"), (390, "dark")])
def test_geometry_near_bottom_long_labels_and_shared_rail(browser, width, theme):
    context, page = page_for(browser, width, theme)
    page.evaluate("""document.querySelector('main').style.padding='24px';
        document.querySelector('#zone').options[1].textContent='An exceptionally long timezone label '.repeat(8);
        document.querySelector('#zone').value='Europe/Amsterdam'; TrellumChoices.init(document);
        document.querySelector('main').style.paddingTop='520px';""")
    trigger = page.locator(".ui-choice-picker .ui-picker-trigger")
    trigger.scroll_into_view_if_needed()
    trigger.click()
    popup = page.locator(".ui-choice-picker .ui-picker-popup")
    rect = popup.bounding_box()
    assert rect["x"] >= 0 and rect["x"] + rect["width"] <= width + 1
    assert rect["y"] >= 0 and rect["y"] + rect["height"] <= 901
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    rail = page.locator(".ui-form-rail").bounding_box()
    content = page.locator("main").evaluate("""el => {
        const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return {left: r.left + el.clientLeft + parseFloat(s.paddingLeft),
                width: el.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight)};
    }""")
    assert abs(rail["x"] - content["left"]) < 1
    assert abs(rail["width"] - content["width"]) < 1
    columns = page.locator(".ui-form-row").evaluate("node=>getComputedStyle(node).gridTemplateColumns.split(' ').length")
    assert columns == (1 if width < 768 else 2)
    if width < 768:
        assert trigger.bounding_box()["height"] >= 44
        assert trigger.evaluate("node=>getComputedStyle(node).fontSize") == "16px"
        assert popup.locator(".ui-picker-search").evaluate("node=>getComputedStyle(node).fontSize") == "16px"
    else:
        assert rect["y"] + rect["height"] <= trigger.bounding_box()["y"] + 1
    assert page.locator(".ui-form-row > .ui-field").first.evaluate("node=>getComputedStyle(node).marginBottom") == "0px"
    assert page.locator(".ui-form-row > .ui-field > label").first.evaluate("node=>getComputedStyle(node).marginTop") == "0px"
    popup.screenshot(path=str(ROOT / ".artifacts" / f"shared-picker-{browser.browser_type.name}-{width}-{theme}.png"))
    context.close()


def test_open_picker_stays_onscreen_when_responsive_layout_moves_its_anchor(browser):
    context, page = page_for(browser)
    root = page.locator('#recipients')
    root.locator('.ui-picker-trigger').click()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.evaluate("""() => {
        document.querySelector('main').style.paddingTop = '2400px';
        scrollTo(0, 0);
        dispatchEvent(new Event('resize'));
    }""")
    popup = root.locator('.ui-picker-popup')
    box = popup.bounding_box()
    assert box['y'] >= 0 and box['y'] + box['height'] <= 844
    assert box['x'] >= 0 and box['x'] + box['width'] <= 390
    root.locator('.ui-picker-search').fill('person99@')
    root.locator('input[value="99"]').check()
    assert page.evaluate("new FormData(document.querySelector('form')).get('people')") == '99'
    context.close()


def test_widget_scoped_styles_escape_transformed_drawer_and_native_nojs(browser):
    context, page = page_for(browser, 390, enhance=False, portal_css=False)
    expect(page.locator("#zone")).to_be_visible()
    expect(page.locator('input[value="99"]')).to_be_visible()
    page.locator("#zone").select_option("Europe/London")
    page.locator('input[value="99"]').check()
    assert page.evaluate("new FormData(document.querySelector('form')).get('people')") == "99"
    page.evaluate("document.querySelector('main').style.cssText='transform:translateX(0);width:350px;overflow:hidden'")
    for name in ("choice-picker.js", "recipient-picker.js"):
        page.add_script_tag(content=(ROOT / "static" / name).read_text(encoding="utf-8"))
    root = page.locator("#recipients")
    root.locator(".ui-picker-trigger").click()
    popup = root.locator(".ui-picker-popup")
    assert popup.evaluate("node=>node.matches(':popover-open')")
    rect = popup.bounding_box()
    assert rect["x"] >= 0 and rect["x"] + rect["width"] <= 391
    assert rect["y"] >= 0 and rect["y"] + rect["height"] <= 901
    context.close()


def test_small_keyboard_viewport_and_required_checkbox_stay_usable(browser):
    context, page = page_for(browser, 390)
    page.set_viewport_size({"width": 390, "height": 320})
    page.emulate_media(reduced_motion="reduce")
    root = page.locator("#recipients")
    root.locator(".ui-picker-trigger").click()
    root.get_by_role("button", name="Groups", exact=True).click()
    root.locator(".ui-picker-search").fill("missing")
    page.evaluate("document.querySelector('input[value=\"99\"]').required=true;document.querySelector('input[value=\"99\"]').reportValidity()")
    expect(root.locator('input[value="99"]')).to_be_focused()
    expect(root.get_by_role("button", name="All", exact=True)).to_have_attribute("aria-pressed", "true")
    expect(root.locator(".ui-picker-search")).to_have_value("")
    rect = root.locator(".ui-picker-popup").bounding_box()
    assert rect["y"] >= 0 and rect["y"] + rect["height"] <= 321
    page.keyboard.press("Space")
    assert page.evaluate("document.querySelector('input[value=\"99\"]').checkValidity()")
    context.close()
