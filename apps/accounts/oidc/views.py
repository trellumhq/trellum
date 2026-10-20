"""Use allauth's OIDC flow with a canonical, proxy-independent callback."""
from allauth.account.internal.decorators import login_not_required
from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers.oauth2.views import OAuth2CallbackView, OAuth2LoginView
from allauth.socialaccount.providers.openid_connect.views import OpenIDConnectOAuth2Adapter
from django.http import Http404

from apps.accounts.sso import redirect_uri_for_provider


class PublicURLAdapter(OpenIDConnectOAuth2Adapter):
    def get_callback_url(self, request, app):
        return redirect_uri_for_provider(self.provider_id)


@login_not_required
def login(request, provider_id):
    try:
        return OAuth2LoginView.adapter_view(PublicURLAdapter(request, provider_id))(request)
    except SocialApp.DoesNotExist as exc:
        raise Http404 from exc


@login_not_required
def callback(request, provider_id):
    try:
        return OAuth2CallbackView.adapter_view(PublicURLAdapter(request, provider_id))(request)
    except SocialApp.DoesNotExist as exc:
        raise Http404 from exc
