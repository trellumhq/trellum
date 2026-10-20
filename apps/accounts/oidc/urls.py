"""Retain allauth's OIDC route names, provider IDs and configurable prefix."""
from allauth.socialaccount import app_settings
from django.urls import include, path

from . import views

urlpatterns = [
    path("<str:provider_id>/login/", views.login, name="openid_connect_login"),
    path("<str:provider_id>/login/callback/", views.callback, name="openid_connect_callback"),
]

if app_settings.OPENID_CONNECT_URL_PREFIX:
    urlpatterns = [path(f"{app_settings.OPENID_CONNECT_URL_PREFIX}/", include(urlpatterns))]
