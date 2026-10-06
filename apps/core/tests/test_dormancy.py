"""Account dormancy: warn, disable, erase.

Every other retention target deletes rows. This one closes people's accounts
on a timer, so most of what is worth testing is the refusals -- who is never
touched, what a missed email does, and what a single sign-in undoes.
"""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone

from apps.core import retention
from apps.core.models import AuditLog
from apps.orgs.models import OrgMembership

pytestmark = pytest.mark.django_db

User = get_user_model()

#: Threshold / disable grace / erase grace, in days. The settings file ships
#: 0/30/0 (whole pipeline off, and erase off even when it is on); this dict is
#: the fully-armed variant most stage tests need, erase included.
POLICY = {"dormant_days": 730, "dormant_disable_days": 30, "dormant_erase_days": 60}


@pytest.fixture
def dormancy_on(settings):
    settings.RETENTION = {**settings.RETENTION, **POLICY}
    return settings


def _person(email, *, org=None, signed_in_days_ago=None, joined_days_ago=1000, **extra):
    """An account with its clock placed explicitly.

    ``last_login`` and ``date_joined`` are both plain columns with defaults,
    so they are written after creation rather than passed in -- the same
    reason ``_run`` in test_retention.py backdates ``created_at``.
    """
    user = User.objects.create_user(email=email, password="pw-Str0ng-pw", **extra)
    User.objects.filter(pk=user.pk).update(
        date_joined=timezone.now() - timedelta(days=joined_days_ago),
        last_login=(
            None if signed_in_days_ago is None
            else timezone.now() - timedelta(days=signed_in_days_ago)
        ),
    )
    if org is not None:
        OrgMembership.objects.create(user=user, org=org, role="member")
    return User.objects.get(pk=user.pk)


def _warned(user, *, days_ago):
    User.objects.filter(pk=user.pk).update(
        dormancy_warned_at=timezone.now() - timedelta(days=days_ago)
    )
    return User.objects.get(pk=user.pk)


def _run_all():
    """The three stages in the order `manage.py cleanup` runs them."""
    return [
        retention.warn_dormant_accounts()[1],
        retention.disable_dormant_accounts()[1],
        retention.erase_dormant_accounts()[1],
    ]


# ── off by default ──────────────────────────────────────────────────────────

class TestOffByDefault:
    def test_the_shipped_default_touches_nobody(self, settings, org):
        """RETENTION_DORMANT_DAYS is 0 out of the box, and an upgrade must not
        start closing a customer's staff accounts because of it."""
        assert settings.RETENTION["dormant_days"] == 0
        user = _person("ancient@demo.example", org=org, signed_in_days_ago=5000)

        assert _run_all() == [0, 0, 0]

        user.refresh_from_db()
        assert user.is_active and user.erased_at is None
        assert mail.outbox == []

    def test_disable_is_the_end_of_the_shipped_pipeline(self, settings, org):
        """Turning dormancy on (just the threshold) must not opt anyone into
        erasure: the shipped erase window is 0, so warn -> disable -> stop,
        and the warning email promises exactly that -- no deletion sentence,
        no second date."""
        assert settings.RETENTION["dormant_erase_days"] == 0
        settings.RETENTION = {**settings.RETENTION, "dormant_days": 730}
        user = _person("quiet@demo.example", org=org, signed_in_days_ago=900)

        assert retention.warn_dormant_accounts()[1] == 1
        body = mail.outbox[0].body
        # "Nothing is deleted" (the disable line) may appear; the deletion
        # promise and its date must not.
        assert "and then deleted" not in body and "cannot be undone" not in body
        assert "switched off" in body

        user = _warned(user, days_ago=40)
        assert retention.disable_dormant_accounts()[1] == 1
        assert retention.erase_dormant_accounts()[1] == 0
        user.refresh_from_db()
        assert user.is_active is False and user.erased_at is None

    def test_zero_erase_window_stops_after_disabling(self, dormancy_on, org):
        dormancy_on.RETENTION = {**dormancy_on.RETENTION, "dormant_erase_days": 0}
        user = _warned(_person("x@demo.example", org=org, signed_in_days_ago=900), days_ago=200)

        assert retention.disable_dormant_accounts()[1] == 1
        assert retention.erase_dormant_accounts()[1] == 0
        user.refresh_from_db()
        assert user.is_active is False and user.erased_at is None

    def test_zero_disable_window_stops_erasure_too(self, dormancy_on, org):
        """Erasure may only follow a switched-off sign-in -- that dead login is
        the last chance a human has to notice."""
        dormancy_on.RETENTION = {**dormancy_on.RETENTION, "dormant_disable_days": 0}
        user = _warned(_person("x@demo.example", org=org, signed_in_days_ago=900), days_ago=900)

        assert retention.disable_dormant_accounts()[1] == 0
        assert retention.erase_dormant_accounts()[1] == 0
        user.refresh_from_db()
        assert user.is_active and user.erased_at is None


# ── stage one: the warning ──────────────────────────────────────────────────

class TestWarning:
    def test_a_dormant_account_is_emailed_stamped_and_audited(self, dormancy_on, org):
        user = _person("gone@demo.example", org=org, signed_in_days_ago=800)

        assert retention.warn_dormant_accounts() == ("dormancy warnings", 1)

        user.refresh_from_db()
        assert user.dormancy_warned_at is not None
        assert user.is_active is True  # the warning changes nothing else
        assert [m.to for m in mail.outbox] == [["gone@demo.example"]]
        row = AuditLog.objects.get(action="retention.dormancy_warned")
        assert row.org_id == org.pk and row.target_id == str(user.pk)

    def test_an_account_inside_the_threshold_is_left_alone(self, dormancy_on, org):
        _person("here@demo.example", org=org, signed_in_days_ago=700)

        assert retention.warn_dormant_accounts()[1] == 0
        assert mail.outbox == []

    def test_the_warning_is_sent_once_not_every_night(self, dormancy_on, org):
        _person("gone@demo.example", org=org, signed_in_days_ago=800)
        retention.warn_dormant_accounts()
        mail.outbox.clear()

        assert retention.warn_dormant_accounts()[1] == 0
        assert mail.outbox == []

    def test_a_failed_send_leaves_the_account_unwarned(self, dormancy_on, org, monkeypatch):
        """The stamp is the only authority the irreversible stages have, so it
        is written after delivery or not at all."""
        user = _person("gone@demo.example", org=org, signed_in_days_ago=800)
        monkeypatch.setattr(
            retention, "_send_dormancy_warning",
            lambda _user, **_kw: (_ for _ in ()).throw(OSError("smtp down")),
        )

        assert retention.warn_dormant_accounts()[1] == 0

        user.refresh_from_db()
        assert user.dormancy_warned_at is None
        assert not AuditLog.objects.filter(action="retention.dormancy_warned").exists()

    def test_one_bad_address_does_not_stop_the_rest(self, dormancy_on, org, monkeypatch):
        bad = _person("bad@demo.example", org=org, signed_in_days_ago=800)
        good = _person("good@demo.example", org=org, signed_in_days_ago=800)
        real = retention._send_dormancy_warning

        def flaky(user, **kwargs):
            if user.pk == bad.pk:
                raise OSError("rejected")
            return real(user, **kwargs)

        monkeypatch.setattr(retention, "_send_dormancy_warning", flaky)

        assert retention.warn_dormant_accounts()[1] == 1
        bad.refresh_from_db()
        good.refresh_from_db()
        assert bad.dormancy_warned_at is None
        assert good.dormancy_warned_at is not None

    def test_the_email_says_what_happens_when_and_how_to_stop_it(self, dormancy_on, org):
        """Written from the reader's side: both dates, and the one action that
        stops them, in a message they did not ask for."""
        from django.template.defaultfilters import date as date_filter

        user = _person("gone@demo.example", org=org, signed_in_days_ago=800)
        retention.warn_dormant_accounts()

        body = mail.outbox[0].body
        # The dates the mail quotes must be the ones the sweep will act on --
        # i.e. dated from the stamp it just wrote, not from a projection.
        disable_on, erase_on = retention._dormancy_dates(
            retention._dormant_candidates().get(pk=user.pk)
        )
        assert disable_on > timezone.now()
        assert date_filter(disable_on, "j F Y") in body
        assert date_filter(erase_on, "j F Y") in body
        assert "/login" in body
        assert "sign in once" in body.lower()
        assert "cannot be undone" in body
        assert "you do not need" in body  # ...to reply, or ask anyone


# ── stage two: sign-in switched off ─────────────────────────────────────────

class TestDisable:
    def test_sign_in_is_switched_off_after_the_grace_period(self, dormancy_on, org):
        user = _warned(_person("gone@demo.example", org=org, signed_in_days_ago=800), days_ago=31)

        assert retention.disable_dormant_accounts() == ("dormant sign-ins disabled", 1)

        user.refresh_from_db()
        assert user.is_active is False
        assert user.erased_at is None  # nothing deleted yet
        assert AuditLog.objects.filter(
            action="retention.dormancy_disabled", org=org, target_id=str(user.pk)
        ).exists()

    def test_inside_the_grace_period_nothing_happens(self, dormancy_on, org):
        user = _warned(_person("gone@demo.example", org=org, signed_in_days_ago=800), days_ago=29)

        assert retention.disable_dormant_accounts()[1] == 0
        user.refresh_from_db()
        assert user.is_active is True

    def test_an_unwarned_account_is_never_disabled(self, dormancy_on, org):
        """No stamp means nobody was told, however old the account is."""
        user = _person("gone@demo.example", org=org, signed_in_days_ago=5000)

        assert retention.disable_dormant_accounts()[1] == 0
        user.refresh_from_db()
        assert user.is_active is True


# ── a sign-in undoes all of it ──────────────────────────────────────────────

class TestSignInResets:
    def test_signing_in_after_the_warning_cancels_every_stage(self, dormancy_on, org):
        user = _warned(_person("back@demo.example", org=org, signed_in_days_ago=800), days_ago=200)
        User.objects.filter(pk=user.pk).update(last_login=timezone.now())

        assert _run_all() == [0, 0, 0]

        user.refresh_from_db()
        assert user.is_active is True and user.erased_at is None

    def test_a_spent_warning_does_not_block_a_later_one(self, dormancy_on, org):
        """Nothing clears the stamp, so the next cycle has to re-arm off a
        warning that is older than the sign-in that spent it."""
        user = _person("slow@demo.example", org=org, signed_in_days_ago=800)
        _warned(user, days_ago=1000)  # warned, then signed in 800 days ago

        assert retention.warn_dormant_accounts()[1] == 1
        user.refresh_from_db()
        assert user.dormancy_warned_at > timezone.now() - timedelta(minutes=1)


# ── who is never touched ────────────────────────────────────────────────────

class TestExclusions:
    @pytest.mark.parametrize(
        "flags",
        [{"is_superuser": True}, {"is_staff": True}, {"is_operator_flag": True}],
    )
    def test_operator_accounts_are_never_warned(self, dormancy_on, org, flags):
        """The break-glass account is exactly the one that legitimately goes
        years without a sign-in; erasing the last one locks the instance out
        of itself for good."""
        _person("root@demo.example", org=org, signed_in_days_ago=5000, **flags)

        assert _run_all() == [0, 0, 0]

    def test_an_already_erased_account_is_not_reconsidered(self, dormancy_on, org):
        user = _person("ghost@demo.example", org=org, signed_in_days_ago=5000)
        User.objects.filter(pk=user.pk).update(
            erased_at=timezone.now(), is_active=False, dormancy_warned_at=timezone.now()
        )

        assert _run_all() == [0, 0, 0]

    def test_an_account_put_back_by_an_admin_is_not_erased(self, dormancy_on, org):
        """Erasure requires the sign-in to still be switched off. Re-enabling
        the account stops it -- see disable_dormant_accounts on why the stamp
        has to be cleared as well for that to stick."""
        user = _warned(_person("back@demo.example", org=org, signed_in_days_ago=900), days_ago=200)

        assert retention.erase_dormant_accounts()[1] == 0
        user.refresh_from_db()
        assert user.erased_at is None


# ── never signed in ─────────────────────────────────────────────────────────

class TestNeverSignedIn:
    def test_an_account_never_signed_into_ages_from_the_day_it_was_created(
        self, dormancy_on, org
    ):
        user = _person("never@demo.example", org=org, joined_days_ago=800)
        assert user.last_login is None

        assert retention.warn_dormant_accounts()[1] == 1
        assert "never been signed in to" in mail.outbox[0].body

    def test_a_recently_created_unused_account_is_not_dormant(self, dormancy_on, org):
        _person("new@demo.example", org=org, joined_days_ago=10)

        assert retention.warn_dormant_accounts()[1] == 0

    def test_a_pending_invitation_is_not_an_account(self, dormancy_on, org):
        """Invitations carry their own expiry and are swept by
        purge_invitations; nothing here should be a second copy of that."""
        from apps.accounts.models import Invitation

        Invitation.objects.create(email="invited@demo.example", org=org)

        assert _run_all() == [0, 0, 0]
        assert Invitation.objects.count() == 1


# ── stage three: erasure, and the multi-org question ────────────────────────

class TestErase:
    def test_the_account_is_erased_after_the_full_window(self, dormancy_on, org):
        user = _warned(_person("gone@demo.example", org=org, signed_in_days_ago=900), days_ago=91)
        User.objects.filter(pk=user.pk).update(is_active=False)

        assert retention.erase_dormant_accounts() == ("dormant accounts erased", 1)

        user.refresh_from_db()
        assert user.erased_at is not None
        assert user.email.endswith("@erased.invalid")
        assert OrgMembership.objects.filter(user=user).count() == 0

    def test_inside_the_erase_window_the_account_survives(self, dormancy_on, org):
        user = _warned(_person("gone@demo.example", org=org, signed_in_days_ago=900), days_ago=89)
        User.objects.filter(pk=user.pk).update(is_active=False)

        assert retention.erase_dormant_accounts()[1] == 0
        user.refresh_from_db()
        assert user.erased_at is None

    def test_membership_of_a_second_org_does_not_leave_the_account_alive(
        self, dormancy_on, org, other_org
    ):
        """The scoping question. personal_data.erase is org-scoped and would
        return full=False for the first org, leaving the identity, the address
        and the sign-in intact -- which is the one thing the policy promised
        to remove.
        """
        user = _warned(_person("both@demo.example", org=org, signed_in_days_ago=900), days_ago=91)
        OrgMembership.objects.create(user=user, org=other_org, role="member")
        User.objects.filter(pk=user.pk).update(is_active=False)

        retention.erase_dormant_accounts()

        user.refresh_from_db()
        assert user.erased_at is not None
        assert user.name == "" and user.email.endswith("@erased.invalid")
        assert OrgMembership.objects.filter(user=user).count() == 0
        # Each tenant gets its own record of losing them, and so does each of
        # their audit trails.
        erased_in = set(
            AuditLog.objects.filter(action="person.erase").values_list("org__slug", flat=True)
        )
        assert erased_in == {org.slug, other_org.slug}
        told = set(
            AuditLog.objects.filter(action="retention.dormancy_erased")
            .values_list("org__slug", flat=True)
        )
        assert told == {org.slug, other_org.slug}

    def test_an_account_in_no_organization_is_still_erased(self, dormancy_on):
        user = _warned(_person("orphan@demo.example", signed_in_days_ago=900), days_ago=91)
        User.objects.filter(pk=user.pk).update(is_active=False)

        assert retention.erase_dormant_accounts()[1] == 1
        user.refresh_from_db()
        assert user.erased_at is not None

    def test_the_audit_row_never_carries_the_address(self, dormancy_on, org):
        """The pk is the handle that survives the tombstone; recording the
        address 'for the record' would undo the erasure in the record."""
        user = _warned(_person("gone@demo.example", org=org, signed_in_days_ago=900), days_ago=91)
        User.objects.filter(pk=user.pk).update(is_active=False)

        retention.erase_dormant_accounts()

        for row in AuditLog.objects.filter(action__startswith="retention.dormancy"):
            assert "gone@demo.example" not in str(row.metadata)


# ── dry run and the cleanup command ─────────────────────────────────────────

class TestDryRun:
    def test_a_dry_run_reports_without_touching_anything(self, dormancy_on, org):
        fresh = _person("fresh@demo.example", org=org, signed_in_days_ago=800)
        due = _warned(_person("due@demo.example", org=org, signed_in_days_ago=900), days_ago=91)
        User.objects.filter(pk=due.pk).update(is_active=False)

        assert retention.warn_dormant_accounts(dry_run=True)[1] == 1
        assert retention.erase_dormant_accounts(dry_run=True)[1] == 1

        fresh.refresh_from_db()
        due.refresh_from_db()
        assert fresh.dormancy_warned_at is None
        assert due.erased_at is None
        assert mail.outbox == []

    def test_the_three_stages_are_registered_as_cleanup_targets(self):
        names = [fn.__name__ for fn in retention.TARGETS]
        assert names.index("warn_dormant_accounts") < names.index("disable_dormant_accounts")
        assert names.index("disable_dormant_accounts") < names.index("erase_dormant_accounts")


# ── what the org admin sees ─────────────────────────────────────────────────

class TestDormantSoon:
    def test_nothing_is_listed_while_the_policy_is_off(self, org):
        _person("gone@demo.example", org=org, signed_in_days_ago=5000)
        assert retention.dormant_soon(org) == []

    def test_someone_approaching_the_threshold_is_listed_before_the_email(
        self, dormancy_on, org
    ):
        _person("soon@demo.example", org=org, signed_in_days_ago=710)

        rows = retention.dormant_soon(org)

        assert [r["stage"] for r in rows] == ["approaching"]
        assert rows[0]["days_left"] == 19  # 730 - 710, minus the partial day

    def test_the_stages_are_named_and_the_erase_date_is_shown(self, dormancy_on, org):
        warned = _warned(_person("warned@demo.example", org=org, signed_in_days_ago=800), days_ago=5)
        disabled = _warned(
            _person("off@demo.example", org=org, signed_in_days_ago=900), days_ago=40
        )
        User.objects.filter(pk=disabled.pk).update(is_active=False)

        by_email = {r["user"].email: r for r in retention.dormant_soon(org)}

        assert by_email["warned@demo.example"]["stage"] == "warned"
        assert by_email["off@demo.example"]["stage"] == "disabled"
        assert by_email["off@demo.example"]["erase_on"] is not None
        assert warned.pk and disabled.pk  # keep both referenced

    def test_another_organizations_members_are_not_listed(self, dormancy_on, org, other_org):
        _person("theirs@demo.example", org=other_org, signed_in_days_ago=900)

        assert retention.dormant_soon(org) == []

    def test_never_signed_in_is_flagged_rather_than_shown_as_a_date(self, dormancy_on, org):
        _person("never@demo.example", org=org, joined_days_ago=800)

        rows = retention.dormant_soon(org)
        assert rows[0]["never_signed_in"] is True
