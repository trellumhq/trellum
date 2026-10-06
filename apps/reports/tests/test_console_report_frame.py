import pytest
from django.test import override_settings

from apps.core import roles

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def production_frame_defaults():
    with override_settings(X_FRAME_OPTIONS="DENY", CSP_REPORT_ONLY=False):
        yield


@pytest.fixture
def viewer(make_user, org, studio_tree, grant_studio):
    user = make_user("frame-viewer@demo.example", org=org)
    grant_studio(user, studio_tree, roles.VIEWER)
    return user


@pytest.fixture
def prefix(org, studio_tree):
    return f"/s/{org.slug}/{studio_tree.slug}"


@pytest.fixture
def built_report(studio_tree, write_report, report_row):
    output = studio_tree.output_dir / "player-overview"
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(
        "<html><body>report</body></html>", encoding="utf-8"
    )
    (output / "other.html").write_text(
        "<html><body>other</body></html>", encoding="utf-8"
    )
    (output / "data.json").write_text('{"value": 1}', encoding="utf-8")
    return output


def _assert_denied(response):
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers.get("Content-Security-Policy") != "frame-ancestors 'self'"


def _assert_authorized_same_origin_without_host_csp(response):
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers.get("Content-Security-Policy") != "frame-ancestors 'self'"


def test_console_hosted_focus_entry_allows_same_origin(
    login, viewer, prefix, built_report
):
    response = login(viewer).get(
        f"{prefix}/r/player-overview/index.html",
        {"_console_host": "1", "display": "focus"},
    )

    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["Content-Security-Policy"] == "frame-ancestors 'self'"


def test_direct_entry_keeps_authorized_same_origin_baseline_after_hosted_request(
    login, viewer, prefix, built_report
):
    client = login(viewer)
    hosted = client.get(
        f"{prefix}/r/player-overview/index.html",
        {"_console_host": "1", "display": "focus"},
    )
    direct = client.get(f"{prefix}/r/player-overview/index.html")

    assert hosted.headers["X-Frame-Options"] == "SAMEORIGIN"
    _assert_authorized_same_origin_without_host_csp(direct)


@pytest.mark.parametrize(
    "query",
    [
        {"_console_host": "0", "display": "focus"},
        {"_console_host": "true", "display": "focus"},
        {"_console_host": "1", "display": "console"},
        {"_console_host": "1", "display": "FOCUS"},
    ],
)
def test_invalid_host_flags_do_not_add_host_csp(
    login, viewer, prefix, built_report, query
):
    response = login(viewer).get(
        f"{prefix}/r/player-overview/index.html", query
    )

    assert response.status_code == 200
    _assert_authorized_same_origin_without_host_csp(response)


@pytest.mark.parametrize("asset", ["other.html", "data.json"])
def test_non_entry_assets_keep_authorized_same_origin_baseline(
    login, viewer, prefix, built_report, asset
):
    response = login(viewer).get(
        f"{prefix}/r/player-overview/{asset}",
        {"_console_host": "1", "display": "focus"},
    )

    assert response.status_code == 200
    _assert_authorized_same_origin_without_host_csp(response)


def test_anonymous_request_remains_denied(client, prefix, built_report):
    response = client.get(
        f"{prefix}/r/player-overview/index.html",
        {"_console_host": "1", "display": "focus"},
    )

    assert response.status_code != 200
    _assert_denied(response)


def test_unauthorized_request_remains_denied(login, member, prefix, built_report):
    response = login(member).get(
        f"{prefix}/r/player-overview/index.html",
        {"_console_host": "1", "display": "focus"},
    )

    assert response.status_code == 404
    _assert_denied(response)
