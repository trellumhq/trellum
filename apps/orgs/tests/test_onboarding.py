"""The getting-started checklist on the hub.

It reports state from the database rather than remembering what the wizard did,
so an org set up from the CLI, a fixture or an import shows the same truth.
"""
import pytest

from apps.accounts.models import Invitation
from apps.core import roles
from apps.datasources.models import DataSource
from apps.orgs.models import OrgMembership
from apps.studios.models import Studio, StudioRepo

pytestmark = pytest.mark.django_db


def hub(client):
    return client.get("/")


class TestVisibility:
    def test_admin_of_an_empty_org_sees_it(self, login, org_admin):
        body = hub(login(org_admin)).content
        assert b"Getting started" in body
        assert b"Create your first studio" in body

    def test_plain_members_never_see_it(self, login, member):
        assert b"Getting started" not in hub(login(member)).content

    def test_it_disappears_once_every_step_is_done(self, login, org_admin, org, studio):
        StudioRepo.objects.create(studio=studio, repo_url="https://git.example.com/r.git")
        DataSource.objects.create(studio=studio, name="warehouse", type="postgres")
        Invitation.objects.create(org=org, email="new@demo.example")
        assert b"Getting started" not in hub(login(org_admin)).content

    def test_a_partly_done_org_still_sees_it(self, login, org_admin, studio):
        body = hub(login(org_admin)).content
        assert b"Getting started" in body
        assert b"3 left" in body


class TestSteps:
    def test_studio_step_flips_when_a_studio_exists(self, login, org_admin, org):
        assert b"4 left" in hub(login(org_admin)).content
        Studio.objects.create(org=org, slug="casino", name="Casino")
        assert b"3 left" in hub(login(org_admin)).content

    def test_repo_step_needs_a_url_not_just_a_row(self, login, org_admin, studio):
        StudioRepo.objects.create(studio=studio, repo_url="")
        assert b"3 left" in hub(login(org_admin)).content
        StudioRepo.objects.filter(studio=studio).update(
            repo_url="https://git.example.com/r.git"
        )
        assert b"2 left" in hub(login(org_admin)).content

    def test_org_scoped_data_source_counts(self, login, org_admin, org, studio):
        DataSource.objects.create(org=org, name="warehouse", type="postgres")
        assert b"2 left" in hub(login(org_admin)).content

    def test_people_step_counts_a_second_member(self, login, org_admin, org, make_user):
        assert b"4 left" in hub(login(org_admin)).content
        make_user("colleague@demo.example", org=org)
        assert b"3 left" in hub(login(org_admin)).content

    def test_people_step_counts_a_pending_invitation(self, login, org_admin, org):
        Invitation.objects.create(org=org, email="new@demo.example")
        assert b"3 left" in hub(login(org_admin)).content

    def test_steps_before_a_studio_exists_point_somewhere_real(self, login, org_admin, org):
        """The repo and data-source steps have no studio to target yet; they
        must fall back to the studios screen rather than render a dead link."""
        body = hub(login(org_admin)).content.decode()
        assert f"/orgs/{org.slug}/settings/studios" in body
        assert "None" not in body

    def test_another_orgs_studio_does_not_satisfy_a_step(
        self, login, org_admin, other_org, other_studio
    ):
        StudioRepo.objects.create(
            studio=other_studio, repo_url="https://git.example.com/r.git"
        )
        assert b"4 left" in hub(login(org_admin)).content


class TestDismiss:
    def test_dismissing_hides_it_for_good(self, login, org_admin, org):
        c = login(org_admin)
        assert b"Getting started" in hub(c).content
        resp = c.post(f"/orgs/{org.slug}/onboarding/dismiss")
        assert resp.status_code == 302
        org.refresh_from_db()
        assert org.onboarding_dismissed_at is not None
        assert b"Getting started" not in hub(c).content
        # The plain empty state comes back to carry the call to action.
        assert b"Create the first studio" in hub(c).content

    def test_dismiss_requires_post(self, login, org_admin, org):
        assert login(org_admin).get(f"/orgs/{org.slug}/onboarding/dismiss").status_code == 405

    def test_a_member_cannot_dismiss(self, login, member, org):
        assert login(member).post(f"/orgs/{org.slug}/onboarding/dismiss").status_code == 403

    def test_a_stranger_gets_a_404_not_a_403(self, login, make_user, other_org):
        outsider = make_user("outsider@example.com")
        resp = login(outsider).post(f"/orgs/{other_org.slug}/onboarding/dismiss")
        assert resp.status_code == 404


class TestDataSourceStep:
    """With a repository connected the sources are declared in git, so the
    step is about the credentials the declared sources still wait for."""

    @pytest.fixture
    def repo_studio(self, studio):
        StudioRepo.objects.create(studio=studio, repo_url="https://git.example.com/r.git")
        return studio

    def declare(self, studio, name="warehouse"):
        from apps.datasources.models import RepoDataSource

        return RepoDataSource.objects.create(
            studio=studio, name=name, type="postgres", config={"host": "db"},
            source_file="data-sources/config.yaml",
        )

    def test_counts_declared_sources_waiting_for_credentials(self, login, org_admin, org, repo_studio):
        self.declare(repo_studio)
        self.declare(repo_studio, "events")
        body = hub(login(org_admin)).content.decode()
        assert "2 data sources need credentials" in body
        assert f"/s/{org.slug}/{repo_studio.slug}/settings/datasources" in body
        assert "Add a data source" not in body

    def test_singular_and_done_once_credentials_exist(self, login, org_admin, repo_studio):
        self.declare(repo_studio)
        assert b"1 data source needs credentials" in hub(login(org_admin)).content
        DataSource.objects.create(
            studio=repo_studio, name="warehouse", type="postgres",
            credentials={"user": "u", "password": "p"},
        )
        body = hub(login(org_admin)).content.decode()
        assert "Configure data sources" in body and "need credentials" not in body
        assert "1 left" in body  # only the invite step remains

    def test_without_a_repository_the_old_step_stands(self, login, org_admin, studio):
        body = hub(login(org_admin)).content.decode()
        assert "Add a data source" in body and "Configure data sources" not in body


    def test_label_and_done_agree_while_some_sources_wait(self, login, org_admin, repo_studio):
        self.declare(repo_studio)
        self.declare(repo_studio, "events")
        DataSource.objects.create(
            studio=repo_studio, name="warehouse", type="postgres",
            credentials={"user": "u", "password": "p"},
        )
        body = hub(login(org_admin)).content.decode()
        assert "1 data source needs credentials" in body
        assert "2 left" in body  # the step is not done while one still waits
