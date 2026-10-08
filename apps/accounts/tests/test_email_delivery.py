"""Invitation and password reset use the selected instance API route."""
from unittest.mock import patch

import pytest

from apps.core.models import EmailApiConnection, InstanceConfig

pytestmark = pytest.mark.django_db


class RecordingBackend:
    def __init__(self):
        self.messages = []

    def open(self):
        return True

    def close(self):
        pass

    def send_messages(self, messages):
        self.messages.extend(messages)
        return len(messages)


def test_invitation_and_password_reset_use_selected_api_sender(login, org_admin, org, member, settings):
    settings.EMAIL_BACKEND = "apps.core.mail.InstanceEmailBackend"
    profile = EmailApiConnection.objects.create(
        name="Primary API", provider="sendgrid", from_email="verified@example.com",
        provider_config={"region": "global"}, credentials={"api_key": "test-only-secret"},
    )
    config = InstanceConfig.load()
    config.active_email_api_connection = profile
    config.save(update_fields=["active_email_api_connection"])
    backend = RecordingBackend()
    with patch("apps.core.mail.build_api_backend", return_value=backend) as builder:
        client = login(org_admin)
        response = client.post(f"/orgs/{org.slug}/settings/invites",
                               {"email": "new@example.com", "org_role": "member"})
        assert response.status_code == 200
        client.logout()
        reset = client.post("/password-reset/", {"email": member.email})
        assert reset.status_code == 302
    assert builder.call_count >= 2
    assert len(backend.messages) == 2
    assert [message.to for message in backend.messages] == [["new@example.com"], [member.email]]
    assert all(message.from_email == "verified@example.com" for message in backend.messages)
    assert all("test-only-secret" not in str(message) for message in backend.messages)
