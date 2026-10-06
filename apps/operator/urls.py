"""Operator console URLs, mounted at /operator/."""
from django.urls import path

from apps.operator import views

urlpatterns = [
    path("", views.fleet, name="operator-fleet"),
    path("orgs/", views.orgs, name="operator-orgs"),
    path("orgs/<slug:org_slug>/", views.org_detail, name="operator-org-detail"),
    path("orgs/<slug:org_slug>/active", views.org_set_active, name="operator-org-active"),
    path("orgs/<slug:org_slug>/quota", views.org_set_quota, name="operator-org-quota"),
    path(
        "orgs/<slug:org_slug>/studios/<slug:studio_slug>/pool",
        views.studio_set_pool,
        name="operator-studio-pool",
    ),
    path(
        "orgs/<slug:org_slug>/impersonate/<int:user_id>",
        views.impersonate,
        name="operator-impersonate",
    ),
    path("impersonate/stop", views.impersonate_stop, name="operator-impersonate-stop"),
    path(
        "orgs/<slug:org_slug>/members/<int:user_id>/unlock",
        views.member_unlock,
        name="operator-member-unlock",
    ),
    path(
        "orgs/<slug:org_slug>/members/<int:user_id>/reset-mfa",
        views.member_reset_mfa,
        name="operator-member-reset-mfa",
    ),
]
