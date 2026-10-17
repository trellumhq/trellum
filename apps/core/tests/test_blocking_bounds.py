"""C19: no request waits unboundedly on SMTP, LDAP or the assistant."""
import socket
import threading
import time
from types import SimpleNamespace

import pytest
from django.core import mail as dj_mail
from django.test import override_settings

pytestmark = pytest.mark.django_db


@pytest.fixture
def hanging_server():
    """Accepts connections and never says a word."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    held = []
    stop = threading.Event()

    def run():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(srv.accept()[0])
            except OSError:
                pass

    threading.Thread(target=run, daemon=True).start()
    yield srv.getsockname()[1]
    stop.set()
    for c in held:
        c.close()
    srv.close()


def test_smtp_connection_times_out(hanging_server):
    """The route a real send uses: InstanceEmailBackend building its SMTP
    delegate from the instance settings, not Django's stock backend."""
    from apps.core.mail import InstanceEmailBackend
    from apps.core.models import InstanceConfig

    cfg = InstanceConfig.load()
    cfg.email_host, cfg.email_port, cfg.email_use_tls = "127.0.0.1", hanging_server, False
    cfg.save()
    with override_settings(EMAIL_TIMEOUT=1):
        backend = InstanceEmailBackend(fail_silently=False)
        assert backend.snapshot.provider == "smtp"
        assert backend._delegate.timeout == 1
        start = time.monotonic()
        with pytest.raises(OSError):
            backend.open()
    assert time.monotonic() - start < 5


def test_email_timeout_default_is_bounded():
    from django.conf import settings

    assert settings.EMAIL_TIMEOUT and settings.EMAIL_TIMEOUT <= 30


def _load_base_settings(monkeypatch, timeout):
    """Execute a private copy of the base settings module with EMAIL_TIMEOUT
    in the environment; the live django.conf.settings is untouched."""
    import importlib.util
    from pathlib import Path

    import trellum_portal.settings.base as live

    monkeypatch.setenv("EMAIL_TIMEOUT", timeout)
    spec = importlib.util.spec_from_file_location("_base_settings_probe", Path(live.__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value", ["0", "-1", "-30"])
def test_non_positive_email_timeout_is_refused_at_startup(monkeypatch, value):
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured, match="EMAIL_TIMEOUT"):
        _load_base_settings(monkeypatch, value)


def test_positive_email_timeout_is_accepted(monkeypatch):
    assert _load_base_settings(monkeypatch, "7").EMAIL_TIMEOUT == 7


def test_ldap_test_connection_times_out(hanging_server, monkeypatch):
    from apps.accounts import ldap

    monkeypatch.setattr(ldap, "CONNECT_TIMEOUT", 1)
    monkeypatch.setattr(ldap, "RECEIVE_TIMEOUT", 1)
    cfg = SimpleNamespace(
        ldap_server_uri=f"ldap://127.0.0.1:{hanging_server}", ldap_ca_cert="",
        ldap_bind_dn="cn=x", ldap_bind_password="pw", ldap_user_search_base="dc=x",
    )
    start = time.monotonic()
    with pytest.raises(ldap.LDAPError):
        ldap.test_connection(cfg)
    assert time.monotonic() - start < 8


def test_password_reset_survives_mail_failure(client, django_user_model, monkeypatch):
    django_user_model.objects.create_user(email="a@example.com", password="x-Long-pass-123")

    def boom(*a, **k):
        raise OSError("smtp down secret-token")

    monkeypatch.setattr("django.core.mail.message.EmailMessage.send", boom)
    resp = client.post("/password-reset/", {"email": "a@example.com"})
    assert resp.status_code == 302 and resp["Location"] == "/password-reset/sent/"


def test_assistant_availability_cached_and_invalidated(monkeypatch):
    from apps.assistant import llm

    calls = []
    monkeypatch.setattr(llm, "is_available", lambda org, purpose="chat": calls.append(1) or (True, ""))
    llm._available_cache.clear()
    org = SimpleNamespace(pk=7)
    for _ in range(3):
        assert llm.is_available_cached(org) == (True, "")
    assert len(calls) == 1
    llm._drop_available(None, SimpleNamespace(org_id=7))
    llm.is_available_cached(org)
    assert len(calls) == 2
