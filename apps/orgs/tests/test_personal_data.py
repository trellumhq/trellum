"""Exporting and erasing a person (internal planning ticket #077).

Two things are load-bearing here and the rest is detail. **Erasure must not
destroy what the installation is separately obliged to keep** -- the LLM
billing ledger is the one the schema got wrong, and it is tested from both
ends: through the feature, and through a bare ``user.delete()``, which is
what the Django admin does. **Export must not hand one person another
person's data** -- a third party's address in audit metadata, or the mere
existence of another tenant.
"""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.utils import timezone

from apps.assistant.models import AssistantSession, LlmUsage
from apps.core import roles
from apps.core.models import AuditLog
from apps.orgs import personal_data
from apps.orgs.models import OrgMembership
from apps.reports.models import EmailSchedule, ReportFavorite
from apps.studios.models import StudioMembership, StudioPreference

pytestmark = pytest.mark.django_db


@pytest.fixture
def subject(make_user, org, studio, grant_studio):
    user = make_user("erika@demo.example", org=org)
    user.name = "Erika Example"
    user.save(update_fields=["name"])
    grant_studio(user, studio, roles.VIEWER)
    return user


@pytest.fixture
def spend(subject, org):
    return LlmUsage.objects.create(
        org=org, user=subject, month=date(2026, 8, 1), cost_usd=Decimal("12.5000")
    )


def _export_text(user, org) -> str:
    return json.dumps(personal_data.export(user, org), default=str)


# ── The trap: a billing ledger behind a CASCADE ──────────────────────────────

class TestBillingLedgerSurvives:
    def test_erasure_keeps_the_spend_row_attributed(self, rf, subject, org, org_admin, spend):
        request = rf.post("/")
        request.user = org_admin
        personal_data.erase(request, subject, org)

        spend.refresh_from_db()
        assert spend.cost_usd == Decimal("12.5000")
        # Still attributed: the tombstoned row is what keeps "what did this
        # person cost us" answerable after the person is gone.
        assert spend.user_id == subject.pk

    def test_a_bare_user_delete_no_longer_takes_it_down(self, subject, spend):
        """The Django admin registers User, so one click used to delete the
        org's spend history along with the account."""
        subject.delete()

        spend.refresh_from_db()
        assert spend.cost_usd == Decimal("12.5000")
        assert spend.user_id is None


# ── Export ───────────────────────────────────────────────────────────────────

class TestExport:
    def test_covers_the_account_and_this_organization(
        self, subject, org, studio, report_row
    ):
        ReportFavorite.objects.create(user=subject, report=report_row)
        AssistantSession.objects.create(
            user=subject, org=org, title="Why is churn up",
            state={"transcript": [{"role": "user", "content": "why is churn up"}], "usage": {}},
        )
        data = personal_data.export(subject, org)

        assert data["account"]["email"] == "erika@demo.example"
        assert data["account"]["name"] == "Erika Example"
        assert data["access"]["organization_role"] == roles.ORG_MEMBER
        assert [s["studio"] for s in data["access"]["studios"]] == [studio.slug]
        assert data["activity"]["favourite_reports"][0]["report"] == report_row.slug
        conversation = data["activity"]["assistant_conversations"][0]
        assert conversation["title"] == "Why is churn up"
        assert conversation["transcript"][0]["content"] == "why is churn up"

    def test_never_carries_credentials(self, subject, org, report_row):
        from apps.reports.models import ShareLink

        link = ShareLink.objects.create(report=report_row, created_by=subject)
        text = _export_text(subject, org)

        assert subject.password not in text
        assert link.token not in text

    def test_never_names_a_third_party(self, subject, org, org_admin, member):
        """Audit metadata routinely holds someone else's address -- the member
        an admin removed, the address an invitation went to."""
        AuditLog.objects.create(
            org=org, actor=subject, action="member.remove", category="authz",
            target_type="accounts.user", target_id=str(member.pk),
            metadata={"email": member.email},
        )
        text = _export_text(subject, org)

        assert member.email not in text
        assert str(member.pk) not in [
            row.get("target_id") for row in json.loads(text)["activity"]["audit_trail"]
        ]

    def test_never_names_another_organization(self, subject, org, other_org):
        OrgMembership.objects.create(user=subject, org=other_org, role=roles.ORG_MEMBER)
        data = personal_data.export(subject, org)
        text = json.dumps(data, default=str)

        assert data["scope"]["other_organizations"] == 1
        assert other_org.slug not in text
        assert other_org.name not in text

    def test_activity_in_another_organization_stays_there(self, subject, org, other_org):
        OrgMembership.objects.create(user=subject, org=other_org, role=roles.ORG_MEMBER)
        AssistantSession.objects.create(user=subject, org=other_org, title="rival plans")

        assert personal_data.export(subject, org)["activity"]["assistant_conversations"] == []


# ── Erasure: the org footprint ───────────────────────────────────────────────

class TestErase:
    def test_deletes_this_organizations_personal_data(
        self, rf, subject, org, org_admin, studio, report_row
    ):
        ReportFavorite.objects.create(user=subject, report=report_row)
        AssistantSession.objects.create(user=subject, org=org, title="private")
        request = rf.post("/")
        request.user = org_admin

        personal_data.erase(request, subject, org)

        assert not OrgMembership.objects.filter(user=subject, org=org).exists()
        assert not StudioMembership.objects.filter(user=subject, studio__org=org).exists()
        assert not ReportFavorite.objects.filter(user=subject).exists()
        assert not AssistantSession.objects.filter(user=subject).exists()

    def test_tombstones_the_account_but_keeps_it_countable(
        self, rf, subject, org, org_admin
    ):
        row = AuditLog.objects.create(
            org=org, actor=subject, action="report.view", category="access"
        )
        request = rf.post("/")
        request.user = org_admin

        personal_data.erase(request, subject, org)

        subject.refresh_from_db()
        assert subject.erased_at is not None
        assert subject.email == f"erased-{subject.pk}@erased.invalid"
        assert subject.name == ""
        assert subject.is_active is False
        assert not subject.has_usable_password()

        # The whole point of the tombstone: the audit row still points at a
        # row, so their past actions stay countable and stay distinguishable
        # from a system row, which carries actor=NULL.
        row.refresh_from_db()
        assert row.actor_id == subject.pk
        assert AuditLog.objects.filter(actor=subject).count() == 1

    def test_scrubs_the_address_out_of_audit_metadata(self, rf, subject, org, org_admin):
        """Otherwise the erased identifier survives in the one table erasure
        deliberately keeps."""
        row = AuditLog.objects.create(
            org=org, action="auth.login_failed", category="auth", outcome="failure",
            metadata={"attempted": "Erika@demo.example", "scope": "email"},
        )
        request = rf.post("/")
        request.user = org_admin

        personal_data.erase(request, subject, org)

        row.refresh_from_db()
        assert row.metadata["attempted"] == "[erased]"
        assert row.metadata["scope"] == "email"  # untouched

    def test_the_erasure_itself_is_audited_without_the_address(
        self, rf, subject, org, org_admin
    ):
        request = rf.post("/")
        request.user = org_admin
        email = subject.email

        personal_data.erase(request, subject, org)

        row = AuditLog.objects.get(action="person.erase")
        assert row.actor_id == org_admin.pk
        assert row.target_id == str(subject.pk)
        assert row.metadata["scope"] == "account"
        assert email not in json.dumps(row.metadata)

    def test_delivery_schedules_keep_running_without_them(
        self, rf, subject, org, org_admin, report_row, member
    ):
        schedule = EmailSchedule.objects.create(report=report_row, created_by=member)
        schedule.recipients.add(subject, member)
        request = rf.post("/")
        request.user = org_admin

        personal_data.erase(request, subject, org)

        schedule.refresh_from_db()
        assert list(schedule.recipients.all()) == [member]


class TestMultiOrgErasure:
    @pytest.mark.parametrize("retained_membership", [False, True])
    def test_preferences_follow_erasure_scope(
        self, rf, subject, org, studio, other_org, other_studio, org_admin, retained_membership
    ):
        if retained_membership:
            OrgMembership.objects.create(user=subject, org=other_org, role=roles.ORG_MEMBER)
        StudioPreference.objects.create(user=subject, studio=studio, theme="ocean")
        StudioPreference.objects.create(user=subject, studio=other_studio, theme="nord")
        exported = personal_data.export(subject, org)
        assert exported["studio_preferences"] == [{"studio": studio.slug, "theme": "ocean"}]
        preview = personal_data.preview(subject, org)
        expected = 1 if retained_membership else 2
        assert {row["label"]: row["count"] for row in preview["deleted"]}[
            "Studio appearance preferences"
        ] == expected

        request = rf.post("/")
        request.user = org_admin
        personal_data.erase(request, subject, org)
        assert not StudioPreference.objects.filter(user=subject, studio=studio).exists()
        assert StudioPreference.objects.filter(user=subject, studio=other_studio).exists() == retained_membership

    def test_the_account_survives_when_another_organization_holds_it(
        self, rf, subject, org, other_org, org_admin
    ):
        OrgMembership.objects.create(user=subject, org=other_org, role=roles.ORG_MEMBER)
        AssistantSession.objects.create(user=subject, org=other_org, title="theirs")
        request = rf.post("/")
        request.user = org_admin

        result = personal_data.erase(request, subject, org)

        assert result["full"] is False
        subject.refresh_from_db()
        assert subject.erased_at is None
        assert subject.email == "erika@demo.example"
        assert subject.is_active is True
        # This org's footprint went; the other org's did not.
        assert not OrgMembership.objects.filter(user=subject, org=org).exists()
        assert OrgMembership.objects.filter(user=subject, org=other_org).exists()
        assert AssistantSession.objects.filter(user=subject, org=other_org).count() == 1

    def test_the_last_organization_to_leave_ends_the_account(
        self, rf, subject, org, other_org, org_admin
    ):
        OrgMembership.objects.create(user=subject, org=other_org, role=roles.ORG_MEMBER)
        request = rf.post("/")
        request.user = org_admin

        personal_data.erase(request, subject, org)
        personal_data.erase(request, subject, other_org)

        subject.refresh_from_db()
        assert subject.erased_at is not None


# ── The preview is the promise ───────────────────────────────────────────────

class TestPreview:
    def test_counts_exactly_what_erase_deletes(
        self, rf, subject, org, other_org, org_admin, report_row
    ):
        from apps.accounts.models import Invitation, UserSession

        ReportFavorite.objects.create(user=subject, report=report_row)
        AssistantSession.objects.create(user=subject, org=org, title="private")
        UserSession.objects.create(
            user=subject, session_key="k" * 40, last_seen=timezone.now()
        )
        # One invitation in each org: on a full erasure both go, and the
        # preview must count them once, not once per bucket.
        Invitation.objects.create(org=org, email=subject.email)
        Invitation.objects.create(org=other_org, email=subject.email)
        promised = sum(row["count"] for row in personal_data.preview(subject, org)["deleted"])

        request = rf.post("/")
        request.user = org_admin
        result = personal_data.erase(request, subject, org)

        assert sum(result["removed"].values()) == promised

    def test_says_which_of_the_two_erasures_it_is(self, subject, org, other_org):
        assert personal_data.preview(subject, org)["full"] is True
        OrgMembership.objects.create(user=subject, org=other_org, role=roles.ORG_MEMBER)
        preview = personal_data.preview(subject, org)
        assert preview["full"] is False
        assert preview["other_org_count"] == 1


# ── The screens ──────────────────────────────────────────────────────────────

def _erase_url(org, user):
    return f"/orgs/{org.slug}/settings/members/{user.pk}/erase"


class TestExportEndpoint:
    def test_org_admin_downloads_a_json_attachment(self, login, org_admin, org, subject):
        resp = login(org_admin).get(
            f"/orgs/{org.slug}/settings/members/{subject.pk}/export.json"
        )
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/json"
        assert "attachment" in resp["Content-Disposition"]
        assert json.loads(resp.content)["account"]["email"] == subject.email
        assert AuditLog.objects.filter(action="person.export").count() == 1

    def test_a_plain_member_cannot_export_anyone(self, login, member, org, subject):
        resp = login(member).get(
            f"/orgs/{org.slug}/settings/members/{subject.pk}/export.json"
        )
        assert resp.status_code == 403

    def test_an_outsider_never_learns_the_org_exists(self, login, make_user, org, subject):
        outsider = make_user("outsider@else.example")
        resp = login(outsider).get(
            f"/orgs/{org.slug}/settings/members/{subject.pk}/export.json"
        )
        assert resp.status_code == 404


class TestEraseScreen:
    def test_states_the_three_buckets_before_the_button(
        self, login, org_admin, org, subject
    ):
        body = login(org_admin).get(_erase_url(org, subject)).content.decode()
        assert "Deleted" in body
        assert "Kept, with the person removed" in body
        assert "Kept and untouched" in body
        assert "belongs to no other organization" in body

    def test_says_so_when_the_account_belongs_to_other_organizations(
        self, login, org_admin, org, other_org, subject
    ):
        OrgMembership.objects.create(user=subject, org=other_org, role=roles.ORG_MEMBER)
        body = login(org_admin).get(_erase_url(org, subject)).content.decode()
        assert "1 other organization" in body
        assert "sign-in keeps working" in body
        assert other_org.name not in body

    def test_the_wrong_email_erases_nothing(self, login, org_admin, org, subject):
        login(org_admin).post(_erase_url(org, subject), {"confirm_email": "nope@x.example"})
        subject.refresh_from_db()
        assert subject.erased_at is None
        assert OrgMembership.objects.filter(user=subject, org=org).exists()

    def test_the_typed_email_erases(self, login, org_admin, org, subject):
        resp = login(org_admin).post(
            _erase_url(org, subject), {"confirm_email": subject.email}
        )
        assert resp.status_code == 302
        subject.refresh_from_db()
        assert subject.erased_at is not None

    def test_an_admin_cannot_erase_themselves(self, login, org_admin, org):
        login(org_admin).post(_erase_url(org, org_admin), {"confirm_email": org_admin.email})
        org_admin.refresh_from_db()
        assert org_admin.erased_at is None

    def test_an_instance_operator_is_out_of_reach(
        self, login, org_admin, org, make_user
    ):
        operator = make_user("ops@portal.local", org=org)
        operator.is_operator_flag = True
        operator.save(update_fields=["is_operator_flag"])

        login(org_admin).post(_erase_url(org, operator), {"confirm_email": operator.email})

        operator.refresh_from_db()
        assert operator.erased_at is None

    def test_the_last_admin_cannot_be_erased(self, login, org, make_user):
        admin = make_user("solo@demo.example", org=org, org_role=roles.ORG_ADMIN)
        other = make_user("second@demo.example", org=org, org_role=roles.ORG_ADMIN)
        c = login(other)
        # `other` is the second admin, so `admin` is erasable...
        c.post(_erase_url(org, admin), {"confirm_email": admin.email})
        admin.refresh_from_db()
        assert admin.erased_at is not None
        # ...and now nobody is left to be erased by.
        c.post(_erase_url(org, other), {"confirm_email": other.email})
        other.refresh_from_db()
        assert other.erased_at is None

    def test_a_plain_member_cannot_reach_the_screen(self, login, member, org, subject):
        assert login(member).get(_erase_url(org, subject)).status_code == 403


# ── The scrub list cannot silently go stale ──────────────────────────────────

_CALL_RE = re.compile(r"\baudit(?:_system)?\(")
_EMAIL_KWARG_RE = re.compile(
    r"\b(?:%s)\s*=" % "|".join(personal_data.EMAIL_METADATA_KEYS)
)
_ACTION_RE = re.compile(r'^\s*(?:request,\s*)?"([a-zA-Z0-9_.]+)"')


def _call_args(text: str, open_paren: int) -> str:
    depth = 0
    for i in range(open_paren, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:i]
    return ""


def test_every_email_bearing_audit_call_site_is_on_the_scrub_list():
    """Erasure scrubs a fixed list of actions. A new call site that stashes an
    address in metadata and is not on it would leave the erased identifier
    behind -- which is the failure this whole feature exists to prevent."""
    root = Path(settings.BASE_DIR)
    missing = set()
    for top in ("apps",):
        for path in (root / top).rglob("*.py"):
            posix = path.as_posix()
            if "/tests/" in posix or path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            for call in _CALL_RE.finditer(text):
                args = _call_args(text, call.end() - 1)
                action = _ACTION_RE.match(args)
                if action and _EMAIL_KWARG_RE.search(args):
                    if action.group(1) not in personal_data.EMAIL_BEARING_ACTIONS:
                        missing.add(f"{path.relative_to(root).as_posix()}: {action.group(1)}")
    assert not missing, (
        "these audit call sites put an email address in metadata but their action "
        "is not in apps.orgs.personal_data.EMAIL_BEARING_ACTIONS, so erasure will "
        "not reach it: " + ", ".join(sorted(missing))
    )
