"""The shared auth lockup fits the existing login and setup cards."""
from pathlib import Path
import mimetypes

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from playwright.sync_api import sync_playwright

from apps.accounts.forms import LoginForm
from apps.accounts.views import _step_context


@pytest.mark.parametrize('width', [1440, pytest.param(390, marks=pytest.mark.heavy)])
def test_auth_brand_fits_cards_and_keeps_forms_usable(width):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for mode in ('light', 'dark'):
            context = browser.new_context(viewport={'width': width, 'height': 900})
            context.add_init_script(f"localStorage.setItem('mgmt-theme', '{mode}')")
            page = context.new_page()
            for template, label in [('accounts/login.html', 'login'), ('accounts/setup/welcome.html', 'setup')]:
                html = render_to_string(template, {'user': AnonymousUser(), 'form': LoginForm(), **_step_context('welcome')})
                def serve(route):
                    from urllib.parse import urlparse
                    url = urlparse(route.request.url).path
                    if url == '/':
                        route.fulfill(body=html, content_type='text/html')
                    elif url.startswith('/static/'):
                        path = settings.BASE_DIR / 'static' / url.removeprefix('/static/')
                        route.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
                    elif url.startswith('/_vendor/'):
                        path = settings.BASE_DIR / 'trellum/static/vendor' / url.removeprefix('/_vendor/')
                        route.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or 'application/octet-stream')
                    else:
                        route.fulfill(status=204)
                page.route('http://brand.test/**', serve)
                page.goto('http://brand.test/')
                page.evaluate('document.fonts.ready')
                assert page.locator('.auth-brand').inner_text() == 'trellum'
                assert page.locator('.auth-brand-mark').get_attribute('aria-hidden') == 'true'
                assert page.locator('h1').is_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                box = page.locator('.auth-brand').bounding_box()
                card = page.locator('.auth-card').bounding_box()
                assert box['x'] >= card['x'] and box['x'] + box['width'] <= card['x'] + card['width']
                if label == 'login':
                    page.get_by_label('Email', exact=True).fill('reader@example.test')
                    assert page.get_by_role('button', name='Continue', exact=True).is_enabled()
                else:
                    assert page.get_by_role('link', name='Get started').is_visible()
                evidence = Path(settings.BASE_DIR) / '.data-test/brand020'
                evidence.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(evidence / f'{label}-{mode}-{width}.png'), full_page=True)
                page.unroute('http://brand.test/**', serve)
            context.close()
        browser.close()
