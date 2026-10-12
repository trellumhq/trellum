"""Datasource saves, delayed tests and uploads keep the working page intact."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]


def page_html(*, test_timeout=30000):
    source = (ROOT / "templates/datasources/settings.html").read_text(encoding="utf-8")
    script = source.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    script = script.replace("{% if org_page %}true{% else %}false{% endif %}", "false")
    script = script.replace("{{ max_upload_bytes }}", "0").replace("{{ test_url_base }}", "/api/datasources/")
    script = script.replace("}, 30000);", "}, " + str(test_timeout) + ");")
    driver = (ROOT / "static/settings-forms.js").read_text(encoding="utf-8")
    return """
      <meta charset="utf-8">
      <div style="height:600px"></div>
      <table data-mobile-cards><tbody><tr id="ds-row-wh" data-source-id="1" data-source-name="wh">
        <td data-label="Source"><strong data-ds-name>wh</strong><span class="ui-chip">from repository</span>
          <br data-ds-description-break><span class="muted" data-ds-description>Old description</span></td>
        <td data-label="Scope">studio</td><td data-label="Type">Postgres</td>
        <td data-label="Status"><span data-ds-badge>Connected</span><span data-ds-detail></span>
          <div data-ds-result id="test-result-1"></div>
          <div class="ds-progress" id="ds-progress-1" hidden><div class="ds-progress-bar"><span></span></div><div class="ds-progress-label"></div></div>
        </td><td data-label="Used by">0 reports</td><td data-label="Actions">
          <label><input class="ds-file-input" type="file" data-pk="1" data-name="wh" data-url="/upload"><span data-ds-upload-label>Upload</span></label>
          <button data-ds-test type="button">Test</button>
        </td></tr></tbody></table>
      <div style="height:600px"></div>
      <h2 data-ds-form-title>Add a portal-only source (advanced)</h2>
      <form id="ds-form" method="post" action="http://datasources.test/settings" data-settings-form data-settings-save>
        <input type="hidden" name="csrfmiddlewaretoken" value="csrf-form-token">
        <input type="hidden" name="id" value="1"><input name="name" value="wh">
        <input name="host" value="old"><select name="type"><option value="postgres">Postgres</option></select>
        <label><input type="checkbox" name="new_connection_per_query">Use a new connection for every query</label>
        <div data-f="path"><label>Path</label><input name="path"><span class="helptext">Help</span></div>
        <input type="checkbox" name="upload"><span id="ds-git-warning"></span>
        <button type="submit">Save</button>
      </form><form data-settings-form><input name="other" value="Untouched"><button>Save other</button></form>
      <div style="height:1200px"></div>
      <script id="type-fields" type="application/json">{"postgres":[]}</script>
      <script>
        window.requests = []; window.saveCount = 0;
        window.fetch = function(url, options) {
          requests.push({url:String(url), revision:options.body.get('revision'), newConnection:options.body.get('new_connection_per_query')});
          if (String(url).endsWith('/test')) {
            return new Promise(function(resolve) {
              window.completeTest = function(data) { resolve({json:function(){return Promise.resolve(data)}}); };
            });
          }
          saveCount += 1;
          return Promise.resolve({ok:true, json:function(){return Promise.resolve(Object.assign({
            ok:true, message:'Saved. Testing connection…', source_id:1, source_name:'wh',
            source_type:'postgres', type_label:'Postgres', source_scope:'studio', description:'',
            test_url:'/api/datasources/wh/test', revision:'revision-' + saveCount, values:{id:1},
            can_edit:true, edit_url:'/settings?edit=1', delete_url:'/settings'
          }, window.saveResponse || {}))}});
        };
        window.XMLHttpRequest = function() {
          var self = this; self.events = {}; self.upload = {addEventListener:function(){}};
          self.open = function(){}; self.setRequestHeader=function(){};
          self.addEventListener = function(name, fn){self.events[name]=fn};
          self.send = function() {
            window.completeUpload = function() {
              self.status=200; self.responseText=JSON.stringify({ok:true,source_id:1,revision:'uploaded',size_label:'8 B',download_url:'/download'});
              self.events.load();
            };
          };
        };
      </script>
    """ + f"<script>{script}</script><script>{driver}</script>"


def load_page(page, **kwargs):
    page.route("http://datasources.test/settings", lambda route: route.fulfill(content_type="text/html", body=page_html(**kwargs)))
    page.goto("http://datasources.test/settings")


def load_editor(page):
    html = page_html()
    start, end = html.index('<table data-mobile-cards>'), html.index('</table>') + len('</table>')
    html = html[:start] + """
      <h1 data-ds-editor-name>wh</h1><p data-ds-saved-evidence>Last confirmed yesterday</p><p data-ds-saved-scope>Credentials for this studio.</p>
      <div id="ds-editor-status" data-ds-status><span data-ds-badge>Last confirmed yesterday</span>
        <span data-ds-detail></span><div data-ds-result></div><button type="button" data-ds-test>Test saved</button>
      </div>
    """ + html[end:]
    html = html.replace('id="ds-form"', 'id="ds-editor" data-revision="revision-0" data-org-revision="org-0"')
    html = html.replace('<button type="submit">Save</button>', '<input name="password" type="password"><input name="org_level" type="checkbox"><button type="button" data-ds-draft-test>Test draft</button><button type="submit">Save</button>', 1)
    html = html.replace("requests.push({url:String(url),", "requests.push({action:options.body.get('action'),password:options.body.get('password'),url:String(url),")
    html = html.replace("if (String(url).endsWith('/test')) {", """if (options.body.get('action') === 'test_draft') {
            return new Promise(function(resolve) { window.completeDraft = function(data) { resolve({json:function(){return Promise.resolve(data)}}); }; });
          }
          if (window.delaySave && !String(url).endsWith('/test')) {
            return new Promise(function(resolve) { window.completeSave = function() { resolve({ok:true,json:function(){return Promise.resolve({ok:true,message:'Saved. Testing connection…',source_id:1,source_name:'wh',label:'Configured / not tested',test_url:'/api/datasources/wh/test',revision:'revision-new',editor_revision:'revision-new',edit_url:'/settings/wh/edit'})}}); }; });
          }
          if (String(url).endsWith('/test')) {""")
    page.route("http://datasources.test/settings", lambda route: route.fulfill(content_type="text/html", body=html))
    page.goto("http://datasources.test/settings")


def test_editor_first_edit_invalidates_evidence_and_failed_draft_retains_secret():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_editor(page)
        page.locator('[name="password"]').fill("replacement")
        assert page.locator('[data-ds-badge]').text_content() == "Draft changed"
        assert page.locator('[data-ds-saved-evidence]').text_content() == "Last confirmed yesterday"
        page.locator('[data-ds-draft-test]').click()
        page.wait_for_function("window.requests.length === 1")
        assert page.evaluate("requests[0].action") == "test_draft"
        assert page.evaluate("requests[0].password") == "replacement"
        assert page.evaluate("requests[0].revision") == "revision-0"
        page.evaluate("completeDraft({ok:false,detail:'Refused.',revision:'revision-0'})")
        page.wait_for_function("document.querySelector('[data-ds-result]').textContent.includes('Refused.')")
        assert page.locator('[name="password"]').input_value() == "replacement"
        assert page.locator('[data-ds-badge]').text_content() == "Draft test failed"
        assert page.locator('[data-ds-saved-evidence]').text_content() == "Last confirmed yesterday"
        assert page.evaluate("window.saveCount") == 0
        browser.close()


def test_editor_changed_draft_discards_old_probe_and_uses_org_revision():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_editor(page)
        page.locator('[data-ds-draft-test]').click()
        page.evaluate("window.oldDraft = window.completeDraft")
        page.locator('[name="password"]').fill("newer")
        page.evaluate("oldDraft({ok:true,detail:'Old success.',revision:'revision-0'})")
        page.wait_for_function("document.querySelector('[data-ds-draft-test]').disabled === false")
        assert page.locator('[data-ds-badge]').text_content() == "Draft changed"
        assert "Old success" not in page.locator('[data-ds-result]').text_content()
        page.locator('[name="org_level"]').check()
        page.locator('[data-ds-draft-test]').click()
        assert page.evaluate("requests[1].revision") == "org-0"
        page.evaluate("completeDraft({ok:true,detail:'New draft.',revision:'org-0'})")
        page.wait_for_function("document.querySelector('[data-ds-result]').textContent.includes('New draft.')")
        assert page.locator('[data-ds-badge]').text_content() == "Draft confirmed (unsaved)"
        browser.close()


def test_editor_edits_during_delayed_save_stay_dirty_and_no_probe_starts():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_editor(page)
        page.evaluate("window.delaySave = true")
        page.locator('[name="host"]').fill("submitted")
        page.locator('#ds-editor button[type="submit"]').click()
        page.locator('[name="host"]').fill("newer draft")
        scroll = page.evaluate("window.scrollY")
        page.evaluate("window.focused = document.activeElement; completeSave()")
        page.wait_for_function("document.querySelector('#ds-editor').getAttribute('aria-busy') === null")
        assert page.locator('[name="host"]').input_value() == "newer draft"
        assert page.locator('#ds-editor').evaluate("form => form.classList.contains('is-dirty')")
        assert page.locator('[data-ds-badge]').text_content() == "Draft changed"
        assert page.locator('[data-ds-test]').is_disabled()
        assert page.evaluate("window.requests.length") == 1
        assert page.evaluate("window.scrollY") == scroll
        assert page.evaluate("document.activeElement === window.focused")
        assert page.locator('#ds-editor').get_attribute('data-revision') == "revision-new"
        browser.close()


def test_editor_shared_save_identifies_target_and_keeps_distinct_draft_revisions():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_editor(page)
        page.evaluate("""() => {
          document.querySelector('#ds-editor [name=name]').remove();
          window.saveResponse = {source_scope:'org',label:'Configured / not tested',editor_revision:'studio-revision',org_revision:'org-revision',revision:'org-revision'};
        }""")
        page.locator('[name="org_level"]').check()
        page.locator('#ds-editor button[type="submit"]').click()
        page.wait_for_function("window.requests.length === 2")
        assert page.locator('[data-ds-saved-scope]').text_content() == "Organization credentials shared with every studio."
        assert page.locator('#ds-editor').get_attribute('data-revision') == "studio-revision"
        assert page.locator('#ds-editor').get_attribute('data-org-revision') == "org-revision"
        assert page.locator('#ds-editor').get_attribute('action') == "http://datasources.test/settings"
        page.evaluate("completeTest({ok:true,label:'Last confirmed at org time',detail:'Org confirmed.',revision:'org-revision'})")
        page.wait_for_function("document.querySelector('[data-ds-result]').textContent.includes('Org confirmed.')")
        page.locator('[name="org_level"]').uncheck()
        page.locator('[data-ds-draft-test]').click()
        assert page.evaluate("requests[2].revision") == "studio-revision"
        assert page.locator('[data-ds-saved-scope]').text_content() == "Organization credentials shared with every studio."
        browser.close()


def test_returned_list_refresh_discards_older_read_and_preserves_neighbor_draft():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_page(page)
        page.locator('[name="other"]').fill("Neighbor draft")
        page.evaluate("""() => {
          document.querySelector('table').dataset.settingsRefresh = 'datasources';
          window.reads = [];
          window.fetch = function() { return new Promise(function(resolve) { reads.push(resolve); }); };
          window.finishRead = function(index, label) {
            var table = document.querySelector('table').cloneNode(true);
            table.querySelector('[data-ds-badge]').textContent = label;
            reads[index]({ok:true,headers:{get:function(){return 'text/html'}},text:function(){return Promise.resolve(table.outerHTML)}});
          };
          window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}));
          window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}));
        }""")
        scroll = page.evaluate("window.scrollY")
        page.evaluate("window.focused = document.activeElement; finishRead(1, 'Last confirmed at new time')")
        page.wait_for_function("document.querySelector('[data-ds-badge]').textContent === 'Last confirmed at new time'")
        page.evaluate("finishRead(0, 'Old saved evidence')")
        assert page.locator('[data-ds-badge]').text_content() == "Last confirmed at new time"
        assert page.locator('[name="other"]').input_value() == "Neighbor draft"
        assert page.evaluate("window.scrollY") == scroll
        assert page.evaluate("document.activeElement === window.focused")
        browser.close()


def test_editor_renamed_source_uses_new_endpoint_and_old_saved_probe_cannot_confirm_draft():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_editor(page)
        page.evaluate("window.saveResponse = {source_name:'renamed',edit_url:'/settings/renamed/edit',label:'Configured / not tested',editor_revision:'revision-1'}")
        page.locator('[name="name"]').fill("renamed")
        page.locator('#ds-editor button[type="submit"]').click()
        page.wait_for_function("window.requests.length === 2")
        assert page.url == "http://datasources.test/settings/renamed/edit"
        assert page.locator('#ds-editor').get_attribute('action') == "/settings/renamed/edit"
        page.locator('[name="password"]').fill("newer secret")
        page.evaluate("completeTest({ok:true,label:'Last confirmed at old time',detail:'Old saved success.',revision:'revision-1'})")
        page.wait_for_function("document.querySelector('#ds-editor').getAttribute('aria-busy') === null")
        assert page.locator('[data-ds-badge]').text_content() == "Draft changed"
        assert "Old saved success" not in page.locator('[data-ds-result]').text_content()
        assert page.locator('[data-ds-saved-evidence]').text_content() == "Configured / not tested"
        page.locator('[data-ds-draft-test]').click()
        assert page.evaluate("requests[2].url") == "/settings/renamed/edit"
        assert page.evaluate("requests[2].revision") == "revision-1"
        browser.close()


def test_save_confirms_before_test_and_retains_page_focus_scroll_and_other_draft():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_page(page)
        page.locator('[name="other"]').fill("Another draft")
        page.locator('[name="host"]').fill("new")
        page.locator('[name="host"]').press("Enter")
        page.wait_for_function("window.requests.length === 2")
        status = page.locator('#ds-form [data-settings-dirty-status]')
        assert status.text_content() == "Saved. Testing connection…"
        assert page.evaluate("window.requests[1].revision") == "revision-1"
        scroll = page.evaluate("window.scrollY")
        page.evaluate("window.focusedElement = document.activeElement")
        page.evaluate("completeTest({ok:true,detail:'Connected.',revision:'revision-1'})")
        page.wait_for_function("document.querySelector('#ds-form [data-settings-dirty-status]').textContent === 'Saved. Connection confirmed.'")
        assert page.evaluate("window.scrollY") == scroll
        assert page.evaluate("document.activeElement === window.focusedElement")
        assert page.locator('[name="other"]').input_value() == "Another draft"
        browser.close()


def test_save_submits_checked_and_unchecked_connection_option_without_navigation():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_page(page)
        checkbox = page.locator('[name="new_connection_per_query"]')
        checkbox.check()
        page.locator('#ds-form button[type="submit"]').click()
        page.wait_for_function("window.requests.length === 2")
        assert page.evaluate("window.requests[0].newConnection") == "on"
        assert page.url == "http://datasources.test/settings"

        page.evaluate("window.completeTest({ok:true,detail:'Connected.',revision:'revision-1'})")
        page.wait_for_function("window.requests.length === 2")
        checkbox.uncheck()
        page.locator('#ds-form button[type="submit"]').click()
        page.wait_for_function("window.requests.length === 4")
        assert page.evaluate("window.requests[2].newConnection") is None
        assert page.url == "http://datasources.test/settings"
        browser.close()


def test_successful_crud_save_follows_same_origin_list_redirect():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_page(page)
        page.route("http://datasources.test/settings/list", lambda route: route.fulfill(
            content_type="text/html", body="<main>Data source list</main>"
        ))
        page.evaluate("window.saveResponse = {redirect:'/settings/list'}")
        page.locator('[name="host"]').fill("saved")
        page.locator('#ds-form button[type="submit"]').click()
        page.wait_for_url("http://datasources.test/settings/list")
        assert page.locator("main").text_content() == "Data source list"
        browser.close()


def test_old_test_does_not_overwrite_new_draft_or_new_saved_revision():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_page(page)
        page.locator('[name="host"]').fill("first")
        page.locator('#ds-form button[type="submit"]').click()
        page.wait_for_function("window.requests.length === 2")
        page.evaluate("window.completeOldTest = window.completeTest")
        page.locator('[name="host"]').fill("second")
        page.locator('#ds-form button[type="submit"]').click()
        page.wait_for_function("window.requests.length === 4")
        page.evaluate("completeOldTest({ok:true,detail:'Old connection.',revision:'revision-1'})")
        page.wait_for_timeout(30)
        assert page.locator('#ds-form [data-settings-dirty-status]').text_content() == "Saved. Testing connection…"
        assert "Old connection" not in page.locator('[data-ds-result]').text_content()
        page.evaluate("completeTest({ok:false,detail:'Refused.',revision:'revision-2'})")
        page.wait_for_function("document.querySelector('[data-ds-result]').textContent.includes('Refused.')")
        assert page.locator('[name="host"]').input_value() == "second"
        assert "Connection test failed: Refused." in page.locator('#ds-form [data-settings-dirty-status]').text_content()
        browser.close()


@pytest.mark.parametrize("width", [390, 1440])
def test_upload_label_keeps_native_chooser_without_page_overflow(width):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.set_content('<body class="mgmt"><main><label class="ui-btn ghost">'
                         '<input type="file" class="ds-file-input">Replace file</label></main></body>')
        page.add_style_tag(content=(ROOT / "static/ui.css").read_text(encoding="utf-8"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator('.ds-file-input').bounding_box()["width"] <= 1
        with page.expect_file_chooser() as chooser:
            page.get_by_text("Replace file", exact=True).click()
        chooser.value.set_files([])
        browser.close()


def test_upload_updates_file_controls_without_navigation_and_preserves_draft():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_page(page)
        page.locator('[name="other"]').fill("Another draft")
        page.locator('.ds-file-input').set_input_files({"name": "data.csv", "mimeType": "text/csv", "buffer": b"a,b\n1,2\n"})
        scroll = page.evaluate("window.scrollY")
        page.evaluate("completeUpload()")
        assert page.locator('[data-ds-upload-label]').text_content() == "Replace file"
        assert page.locator('[data-ds-download]').get_attribute("href") == "/download"
        assert page.locator('.ds-progress-label').text_content() == "Uploaded 8 B."
        assert page.evaluate("window.scrollY") == scroll
        assert page.locator('[name="other"]').input_value() == "Another draft"
        browser.close()


@pytest.mark.parametrize("phase", ["request", "json"])
def test_connection_test_deadline_covers_request_and_json_and_allows_retry(phase):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_page(page, test_timeout=120)
        page.evaluate("""phase => {
          window.fetch = function() {
            return phase === 'request' ? new Promise(function(){})
              : Promise.resolve({json:function(){return new Promise(function(){})}});
          };
          var row = document.querySelector('tr[data-source-name]');
          window.testPromise = dsTest(row, '/test', 'revision-1', null, row.querySelector('[data-ds-test]'));
        }""", phase)
        page.wait_for_function("document.querySelector('[data-ds-result]').textContent.includes('timed out')", timeout=2000)
        assert page.locator('[data-ds-test]').is_enabled()
        assert page.locator('[data-ds-badge]').text_content() == "Not confirmed"
        page.evaluate("""() => {
          window.fetch = function() {return Promise.resolve({json:function(){return Promise.resolve({ok:true,detail:'Retried.',revision:'revision-1'})}})};
          var row = document.querySelector('tr[data-source-name]');
          return dsTest(row, '/test', 'revision-1', null, row.querySelector('[data-ds-test]'));
        }""")
        assert "Retried." in page.locator('[data-ds-result]').text_content()
        assert page.locator('[data-ds-test]').is_enabled()
        browser.close()


def test_old_test_finally_does_not_enable_button_during_newer_test():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_page(page)
        page.evaluate("""() => {
          var row = document.querySelector('tr[data-source-name]'), button = row.querySelector('[data-ds-test]');
          dsTest(row, '/first/test', 'first', null, button);
          window.firstResult = window.completeTest;
          dsTest(row, '/second/test', 'second', null, button);
          window.firstResult({ok:true,detail:'Old.',revision:'first'});
        }""")
        page.wait_for_timeout(20)
        assert page.locator('[data-ds-test]').is_disabled()
        assert "Old." not in page.locator('[data-ds-result]').text_content()
        page.evaluate("completeTest({ok:true,detail:'New.',revision:'second'})")
        page.wait_for_function("document.querySelector('[data-ds-test]').disabled === false")
        assert "New." in page.locator('[data-ds-result]').text_content()
        browser.close()


def test_saved_row_preserves_repository_chip_and_formatted_description():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_page(page)
        page.evaluate("""() => {
          window.saveResponse = {description:'Updated description', download_url:'/download/wh'};
          document.querySelector('[data-ds-detail]').textContent = 'Add credentials first';
        }""")
        page.locator('[name="host"]').fill("new")
        page.locator('[name="host"]').press("Enter")
        page.wait_for_function("window.requests.length === 2")
        assert page.locator('[data-label="Source"] .ui-chip').text_content() == "from repository"
        assert page.locator('[data-label="Source"] [data-ds-record]').text_content() == "wh"
        assert page.locator('[data-ds-description]').text_content() == "Updated description"
        assert page.locator('[data-ds-description]').get_attribute("class") == "muted"
        assert page.locator('[data-ds-description-break]').evaluate("element => !element.hidden")
        assert page.locator('[data-label="Scope"] .scope-chip').text_content() == "studio"
        assert page.locator('[data-ds-download]').get_attribute("href") == "/download/wh"
        assert page.locator('[data-ds-detail]').text_content() == ""
        browser.close()


def test_new_source_has_native_edit_delete_and_next_save_edits_same_source():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_page(page)
        page.evaluate("""() => {
          document.querySelector('tr[data-source-name]').remove();
          document.querySelector('#ds-form [name=id]').value = '';
          window.saveResponse = {source_id:9,source_name:'created',edit_url:'/settings?edit=9',values:{id:9}};
        }""")
        page.locator('[name="name"]').fill("created")
        page.locator('#ds-form button[type="submit"]').click()
        page.wait_for_function("window.requests.length === 2")
        row = page.locator('tr[data-source-id="9"]')
        assert row.locator('[data-ds-record]').get_attribute("href") == "/settings?edit=9"
        assert row.locator('[data-ds-edit]').get_attribute("href") == "/settings?edit=9"
        delete = row.locator('form')
        assert delete.get_attribute("method") == "post" and delete.get_attribute("action") == "/settings"
        assert delete.locator('[name="csrfmiddlewaretoken"]').input_value() == "csrf-form-token"
        assert delete.locator('[name="action"]').input_value() == "delete"
        assert delete.locator('[name="id"]').input_value() == "9"
        assert page.locator('#ds-form [name="id"]').input_value() == "9"
        assert page.locator('#ds-form button[type="submit"]').text_content() == "Save changes"
        assert page.locator('[data-ds-form-title]').text_content() == "Edit “created”"
        confirmations = []
        page.on("dialog", lambda dialog: (confirmations.append(dialog.message), dialog.dismiss()))
        delete.locator('button').click()
        assert "created" in confirmations[-1]
        page.evaluate("window.saveResponse.source_name = 'renamed'")
        page.locator('[name="name"]').fill("renamed")
        page.locator('#ds-form button[type="submit"]').click()
        page.wait_for_function("window.requests.length === 4")
        delete.locator('button').click()
        assert "renamed" in confirmations[-1] and "created" not in confirmations[-1]
        assert page.locator('tr[data-source-id="9"]').count() == 1
        assert row.locator('[data-ds-edit]').count() == 1
        assert page.locator('[data-ds-form-title]').text_content() == "Edit “renamed”"
        browser.close()


def test_first_credential_save_updates_configure_and_removal_controls():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_page(page)
        page.evaluate("""() => {
          var form = document.querySelector('#ds-form');
          form.setAttribute('id','ds-configure');
          var panel = document.createElement('div');
          form.before(panel); panel.appendChild(form);
          var link = document.createElement('a');
          link.dataset.dsConfigure = '';
          link.setAttribute('href','?configure=wh#configure'); link.textContent='Configure';
          document.querySelector('[data-label=Actions]').appendChild(link);
          window.saveResponse = {configured:true,can_edit:false,configure_url:'?configure=wh#configure',remove_credentials:true};
        }""")
        page.locator('[name="host"]').fill("new")
        page.locator('#ds-configure button[type="submit"]').click()
        page.wait_for_function("window.requests.length === 2")
        assert page.locator('a[href="?configure=wh#configure"]').text_content() == "Edit credentials"
        remove = page.locator('[data-ds-remove-credentials]')
        assert remove.locator('[name="action"]').input_value() == "remove_credentials"
        assert remove.locator('[name="name"]').input_value() == "wh"
        assert remove.locator('[name="csrfmiddlewaretoken"]').input_value() == "csrf-form-token"
        confirmations = []
        page.on("dialog", lambda dialog: (confirmations.append(dialog.message), dialog.dismiss()))
        remove.locator('button').click()
        assert "Remove the stored credentials" in confirmations[-1] and "wh" in confirmations[-1]
        browser.close()
