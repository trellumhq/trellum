import pytest
from playwright.sync_api import expect, sync_playwright
from django.conf import settings
from django.contrib.auth import get_user_model


@pytest.mark.django_db
@pytest.mark.parametrize(
    "width,theme", [(1440, "light"), (390, "light"), (1440, "dark"), (390, "dark")]
)
def test_quota_form_saves_and_retains_failed_draft(login, org, width, theme):
    operator = get_user_model().objects.create_user(
        email="quota-ops@portal.local", password="pw-Str0ng-pw", is_operator_flag=True
    )
    response = login(operator).get(f"/operator/orgs/{org.slug}/")
    html = response.content.decode()
    url = f"http://portal.test/operator/orgs/{org.slug}/"
    ui_css = (settings.BASE_DIR / "static" / "ui.css").read_text(encoding="utf-8")
    shell_css = (settings.BASE_DIR / "static" / "console-shell.css").read_text(encoding="utf-8")
    shell_js = (settings.BASE_DIR / "static" / "console-shell.js").read_text(encoding="utf-8")
    helper = (settings.BASE_DIR / "static" / "settings-forms.js").read_text(encoding="utf-8")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": width, "height": 850}, device_scale_factor=1)
        saves = 0
        page.route(url, lambda route: route.fulfill(content_type="text/html", body=html))
        def save(route):
            nonlocal saves
            saves += 1
            if saves == 1:
                route.fulfill(status=400, json={
                    "ok": False,
                    "message": "Please correct the highlighted fields.",
                    "errors": {"max_concurrent_runs": ["Enter a whole number."]},
                })
            else:
                route.fulfill(json={
                    "ok": True,
                    "message": "Quota saved. Zero means no limit.",
                    "values": {"max_concurrent_runs": 4},
                    "refresh": ["quota-usage"],
                })
        page.route(f"{url}quota", save)
        page.goto(url)
        page.evaluate(f"localStorage.setItem('mgmt-theme', '{theme}')")
        page.add_style_tag(content=(ui_css + "\n" + shell_css +
                                   "\n* { animation:none !important; transition:none !important }"))
        page.add_script_tag(content=shell_js)
        page.add_script_tag(content=helper)

        quota = page.locator("form[data-settings-save]")
        field = quota.locator('[name="max_concurrent_runs"]')
        field.fill("3")
        field.focus()
        field.evaluate("element => element.scrollIntoView({block: 'center'})")
        field_top = field.evaluate("element => element.getBoundingClientRect().top")
        quota.evaluate("form => form.requestSubmit()")
        expect(quota).to_have_attribute("data-settings-state", "error")
        expect(field).to_have_attribute("aria-invalid", "true")
        expect(page.locator(".settings-form-error")).to_contain_text("Enter a whole number.")
        error_id = field.get_attribute("aria-describedby").split()[-1]
        assert page.evaluate("id => document.getElementById(id).textContent", error_id).strip() == "Enter a whole number."
        assert field.input_value() == "3"
        assert page.evaluate("document.activeElement.name") == "max_concurrent_runs"
        assert field.evaluate("element => element.getBoundingClientRect().top") == field_top

        field.fill("5")
        field.focus()
        field.evaluate("element => element.scrollIntoView({block: 'center'})")
        field_top = field.evaluate("element => element.getBoundingClientRect().top")
        quota.evaluate("form => form.requestSubmit()")
        expect(quota).to_have_attribute("data-settings-state", "saved")
        expect(field).to_have_value("4")
        assert page.evaluate("document.activeElement.name") == "max_concurrent_runs"
        assert field.evaluate("element => element.getBoundingClientRect().top") == field_top
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(settings.BASE_DIR / f"quota-{width}-{theme}.png"), full_page=True)
        browser.close()
