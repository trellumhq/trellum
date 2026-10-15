"""The operator console is one visible entry in the shell sidebar, for operators only."""
import pytest

pytestmark = pytest.mark.django_db

SIDEBAR_ENTRY = '<span class="tl-console-label">Operator console</span>'


def _healthy(monkeypatch):
    monkeypatch.setattr("apps.core.health.quick_health_summary", lambda: {
        "status": "ok", "message": "Healthy", "detail": "",
    })


def test_operator_sees_the_console_in_the_sidebar_and_the_account_menu(login, member, monkeypatch):
    member.is_operator_flag = True
    member.save(update_fields=["is_operator_flag"])
    _healthy(monkeypatch)
    html = login(member).get("/").content.decode()
    group = html[html.index('data-console-nav-group="operator"'):]
    assert 'href="/operator/"' in group.split("</div>", 1)[0] and SIDEBAR_ENTRY in group.split("</div>", 1)[0]
    assert '<a href="/operator/">Operator console</a>' in html


def test_the_console_entry_is_active_on_operator_pages(login, member, monkeypatch):
    member.is_operator_flag = True
    member.save(update_fields=["is_operator_flag"])
    _healthy(monkeypatch)
    html = login(member).get("/operator/orgs/").content.decode()
    assert 'class="tl-console-nav-link active" href="/operator/" title="Operator console" aria-current="page"' in html


def test_non_operator_has_no_console_entry(login, member, monkeypatch):
    _healthy(monkeypatch)
    html = login(member).get("/").content.decode()
    assert SIDEBAR_ENTRY not in html and 'href="/operator/"' not in html
