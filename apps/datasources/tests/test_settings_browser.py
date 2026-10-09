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
        assert page.locator('strong[data-ds-name]').text_content() == "wh"
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
