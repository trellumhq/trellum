"""The support bundle, and above all what it must never contain.

A customer emails this file to us. If a secret leaks into it, the leak is
already irreversible by the time anyone notices — so the redaction tests here
are the point of the module, not an afterthought.
"""
import json

import pytest

from apps.core import supportbundle

pytestmark = pytest.mark.django_db


@pytest.fixture
def loaded(settings, org, studio, report_row, superuser):
    """An install with enough state to be worth diagnosing."""
    from apps.runner.models import Run, WorkerHeartbeat
    from apps.studios.models import StudioRepo

    WorkerHeartbeat.objects.create(worker_id="r1", role="runner", max_concurrent=3)
    Run.objects.create(
        report=report_row,
        studio=report_row.studio,
        slug=report_row.slug,
        status=Run.SUCCESS,
        stdout_tail="REVENUE BY PLAYER: alice 4000, bob 300",
        stderr_tail="password=hunter2 in a traceback",
        peak_memory_mb=812,
    )
    StudioRepo.objects.create(
        studio=studio,
        repo_url="https://github.com/acme/reports.git",
        token="ghp_supersecrettoken",
        webhook_secret="whsec_alsosecret",
    )
    return settings


class TestRedaction:
    """Everything below is a thing that must not reach us."""

    def test_no_secret_setting_values_appear(self, loaded, settings):
        settings.SECRET_KEY = "django-secret-value-xyz"

        text = supportbundle.to_json(supportbundle.build())

        assert "django-secret-value-xyz" not in text

    def test_an_invalid_encryption_key_is_not_echoed_back(self, loaded, settings):
        """Even the broken value must not travel: it may be a mistyped copy of
        the real key, or the real key from another environment."""
        settings.SECRET_ENCRYPTION_KEY = "fernet-key-value-xyz"
        text = supportbundle.to_json(supportbundle.build())
        assert "fernet-key-value-xyz" not in text

    def test_secrets_are_reported_as_present_without_their_value(self, loaded, settings):
        settings.SECRET_ENCRYPTION_KEY = "fernet-key-value-xyz"
        collected = supportbundle.collect_settings()
        assert collected["SECRET_ENCRYPTION_KEY"]["set"] is True
        assert "fernet-key-value-xyz" not in json.dumps(collected)

    def test_database_password_never_appears(self, loaded, settings):
        settings.DATABASES = {
            **settings.DATABASES,
            "default": {**settings.DATABASES["default"], "PASSWORD": "pgpass-xyz"},
        }
        text = supportbundle.to_json(supportbundle.build())
        assert "pgpass-xyz" not in text

    def test_repo_tokens_never_appear(self, loaded):
        text = supportbundle.to_json(supportbundle.build())
        assert "ghp_supersecrettoken" not in text
        assert "whsec_alsosecret" not in text

    def test_repo_presence_is_still_reported(self, loaded):
        repos = supportbundle.collect_repos()["repos"]
        assert repos[0]["has_token"] is True
        assert "github.com/acme/reports.git" in repos[0]["repo_url"]

    def test_credentials_are_stripped_from_urls(self):
        out = supportbundle._redact_url("postgres://trellum:sup3rs3cret@db.internal:5432/x")
        assert "sup3rs3cret" not in out
        assert "db.internal:5432" in out

    def test_report_output_never_appears(self, loaded):
        """Run logs are tenant data: they can contain query results."""
        text = supportbundle.to_json(supportbundle.build())
        assert "REVENUE BY PLAYER" not in text
        assert "hunter2" not in text

    def test_datasource_credentials_never_appear(self, loaded, studio):
        from apps.datasources.models import DataSource

        DataSource.objects.create(
            studio=studio,
            name="warehouse",
            type="postgres",
            credentials={"password": "ds-secret-xyz", "user": "svc"},
        )
        text = supportbundle.to_json(supportbundle.build())
        assert "ds-secret-xyz" not in text

    def test_user_emails_are_not_enumerated(self, loaded, make_user, org):
        make_user("private.person@customer.example", org=org)
        text = supportbundle.to_json(supportbundle.build())
        assert "private.person@customer.example" not in text


class TestContent:
    """What it must contain, or it is not worth sending."""

    def test_carries_health_results(self, loaded):
        labels = {c["label"] for c in supportbundle.build()["health"]}
        assert "database" in labels and "worker" in labels

    def test_carries_the_fleet(self, loaded):
        workers = supportbundle.build()["fleet"]["workers"]
        assert workers[0]["worker_id"] == "r1"
        assert workers[0]["role"] == "runner"

    def test_carries_run_outcomes_and_memory(self, loaded):
        runs = supportbundle.build()["runs"]
        assert runs["status_counts"]["success"] == 1
        assert runs["runs"][0]["peak_memory_mb"] == 812

    def test_carries_tenancy_shape_as_counts(self, loaded):
        tenancy = supportbundle.build()["tenancy"]
        assert tenancy["totals"]["organizations"] >= 1
        assert tenancy["organizations"][0]["slug"]

    def test_carries_versions_and_config_knobs(self, loaded):
        cfg = supportbundle.build()["settings"]
        assert "TRELLUM_VERSION_SHA" in cfg
        assert "WORKER_MAX_CONCURRENT" in cfg
        assert "TRELLUM_QUOTA_BACKEND" not in cfg
        assert "TRELLUM_EXTRA_URLCONFS" not in cfg

    def test_schema_three_omits_entitlement_sections(self, loaded):
        bundle = supportbundle.build()
        assert bundle["schema"] == 3
        assert "licence" not in bundle
        assert "edition" not in bundle

    def test_carries_operational_policy(self, loaded):
        settings_section = supportbundle.build()["settings"]
        assert settings_section["TRELLUM_QUOTAS_ENABLED"] is False
        assert settings_section["TRELLUM_IMPERSONATION_ENABLED"] is True

    def test_is_json_serialisable(self, loaded):
        json.loads(supportbundle.to_json(supportbundle.build()))


class TestCommand:
    def test_writes_a_readable_archive(self, loaded, tmp_path):
        import tarfile

        from django.core.management import call_command

        out = tmp_path / "bundle.tar.gz"
        call_command("supportbundle", output=str(out))

        assert out.exists()
        with tarfile.open(out) as tar:
            member = tar.getmember("support-bundle/bundle.json")
            payload = json.loads(tar.extractfile(member).read().decode())
        assert payload["schema"] == 3
        assert "health" in payload

    def test_print_mode_emits_json(self, loaded, capsys):
        from django.core.management import call_command

        call_command("supportbundle", print_json=True)
        json.loads(capsys.readouterr().out)

    def test_a_broken_install_still_produces_a_bundle(self, loaded, settings):
        """The moment a bundle is most needed is when things are broken. A
        collector that raised would deny us the diagnostics exactly then."""
        settings.SECRET_ENCRYPTION_KEY = "not-a-valid-fernet-key"

        bundle = supportbundle.build()          # must not raise
        json.loads(supportbundle.to_json(bundle))

        # And the breakage itself is visible rather than swallowed.
        health = bundle["health"]
        assert any(
            not c["ok"] and "SECRET_ENCRYPTION_KEY" in c["label"] for c in health
        )

    def test_failing_checks_are_called_out(self, loaded, capsys, tmp_path):
        """The operator should not have to grep the archive to find the fault."""
        from django.core.management import call_command

        # No live coordinator -> the worker check fails.
        from apps.runner.models import WorkerHeartbeat

        WorkerHeartbeat.objects.update(role="runner")
        call_command("supportbundle", output=str(tmp_path / "b.tar.gz"))
        assert "Failing checks" in capsys.readouterr().out
