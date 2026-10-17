"""Password-reset requests: throttled, answered the same for every address,
and never waiting on the mail route."""
import logging
import threading
import time
from types import SimpleNamespace

import pytest
from django.core import mail

from apps.accounts import throttle
from apps.core import mail as core_mail
from apps.core.mail import wait_for_background_sends

pytestmark = pytest.mark.django_db

URL = "/password-reset/"
SENT = "/password-reset/sent/"


def _request(client, email):
    resp = client.post(URL, {"email": email})
    assert resp.status_code == 302 and resp["Location"] == SENT
    return resp


_FakeRequest = SimpleNamespace(META={"REMOTE_ADDR": "127.0.0.1"})


class TestThrottle:
    def test_one_mail_per_address_per_cooldown(self, client, member):
        _request(client, member.email)
        _request(client, member.email)
        _request(client, member.email.upper())  # same inbox, spelled differently
        wait_for_background_sends()
        assert len(mail.outbox) == 1

    def test_unknown_addresses_are_counted_like_known_ones(self, client, member):
        # The claim is made before any account lookup, so a cooldown never
        # tells an address with an account from one without.
        _request(client, "ghost@nowhere.io")
        assert throttle.password_reset_allowed("ghost@nowhere.io", _FakeRequest) is False
        assert throttle.password_reset_allowed(member.email, _FakeRequest) is True

    def test_other_addresses_are_not_held_up(self, client, member, make_user):
        other = make_user("other@demo.example")
        _request(client, member.email)
        _request(client, other.email)
        wait_for_background_sends()
        assert sorted(m.to[0] for m in mail.outbox) == sorted([member.email, other.email])

    def test_zero_disables_the_address_cooldown(self, client, member, settings):
        settings.PASSWORD_RESET_EMAIL_COOLDOWN_SECONDS = 0
        _request(client, member.email)
        _request(client, member.email)
        wait_for_background_sends()
        assert len(mail.outbox) == 2

    def test_client_address_is_capped_per_window(self, client, make_user, settings):
        settings.PASSWORD_RESET_IP_MAX_REQUESTS = 3
        for i in range(5):
            make_user(f"user{i}@demo.example")
        for i in range(5):
            _request(client, f"user{i}@demo.example")  # every answer is the same neutral redirect
        wait_for_background_sends()
        assert sorted(m.to[0] for m in mail.outbox) == [f"user{i}@demo.example" for i in range(3)]

    def test_a_request_over_the_ip_cap_does_not_claim_the_address(self, client, member, settings):
        settings.PASSWORD_RESET_IP_MAX_REQUESTS = 1
        _request(client, "first@nowhere.io")
        assert throttle.password_reset_allowed(member.email, _FakeRequest) is False
        settings.PASSWORD_RESET_IP_MAX_REQUESTS = 0
        assert throttle.password_reset_allowed(member.email, _FakeRequest) is True


class TestOffTheRequest:
    def test_response_time_does_not_include_the_mail_round_trip(self, client, member, monkeypatch):
        started = threading.Event()

        def slow_send(self, fail_silently=False):
            started.set()
            time.sleep(0.8)  # an SMTP server that takes its time
            return 1

        monkeypatch.setattr("django.core.mail.message.EmailMessage.send", slow_send)
        t0 = time.monotonic()
        _request(client, member.email)
        existing = time.monotonic() - t0
        t0 = time.monotonic()
        _request(client, "ghost@nowhere.io")
        unknown = time.monotonic() - t0
        assert started.wait(5), "the existing account's mail was never handed to the sender"
        assert existing < 0.5 and unknown < 0.5
        wait_for_background_sends()

    def test_failures_are_logged_by_class_only_and_stay_neutral(self, client, member, monkeypatch, caplog):
        def boom(self, fail_silently=False):
            raise OSError("smtp down secret-token")

        monkeypatch.setattr("django.core.mail.message.EmailMessage.send", boom)
        with caplog.at_level(logging.WARNING, logger="apps.core.mail"):
            _request(client, member.email)
            wait_for_background_sends()
        assert "password reset mail was not accepted: OSError" in caplog.text
        assert "secret-token" not in caplog.text
        assert all(r.exc_info is None for r in caplog.records)

    def test_a_full_set_of_slots_drops_the_mail_and_says_so(self, client, member, monkeypatch, caplog):
        slots = threading.BoundedSemaphore(1)
        assert slots.acquire(blocking=False)  # every slot busy
        monkeypatch.setattr(core_mail, "_background_slots", slots)
        with caplog.at_level(logging.WARNING, logger="apps.core.mail"):
            _request(client, member.email)
        wait_for_background_sends()
        assert mail.outbox == []
        assert "password reset mail was dropped" in caplog.text

    def test_an_invalid_mail_route_does_not_break_the_page(self, client, member, monkeypatch, caplog):
        def broken(**kwargs):
            raise RuntimeError("route secret")

        monkeypatch.setattr(core_mail, "resolve_delivery_connection", broken)
        with caplog.at_level(logging.WARNING, logger="apps.core.mail"):
            _request(client, member.email)
        assert "password reset mail was not sent: RuntimeError" in caplog.text
        assert "route secret" not in caplog.text

    def test_slots_are_released_after_each_send(self, client, member, settings):
        settings.PASSWORD_RESET_EMAIL_COOLDOWN_SECONDS = 0
        settings.PASSWORD_RESET_IP_MAX_REQUESTS = 0
        rounds = core_mail._BACKGROUND_SLOTS + 3
        for _ in range(rounds):
            _request(client, member.email)
            wait_for_background_sends()
        assert len(mail.outbox) == rounds
