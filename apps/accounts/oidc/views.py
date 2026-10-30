"""Use allauth's OIDC flow with a canonical, proxy-independent callback."""
from allauth.account.internal.decorators import login_not_required
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialApp
from allauth.socialaccount.providers.oauth2.views import OAuth2CallbackView, OAuth2LoginView
from allauth.socialaccount.providers.openid_connect.views import OpenIDConnectOAuth2Adapter
from django.http import Http404

from apps.accounts import sso, sso_test


class PublicURLAdapter(OpenIDConnectOAuth2Adapter):
    def get_callback_url(self, request, app):
        return sso.redirect_uri_for_provider(self.provider_id)

    def complete_login(self, request, app, token, **kwargs):
        sociallogin = super().complete_login(request, app, token, **kwargs)
        cfg = sso.config_for_provider(sociallogin.account.provider)
        if cfg is not None and sso_test.consume(request, cfg):
            # The callback has checked OAuth state and decoded the ID token.
            # Stop before allauth's lookup(), which saves returning accounts
            # (and stored tokens) before the pre_social_login adapter hook.
            linked = SocialAccount.objects.select_related("user").filter(
                provider=sociallogin.account.provider, uid=sociallogin.account.uid,
            ).first()
            if linked is not None:
                # Preview the incoming claims against the linked user without
                # saving them or emitting social_account_updated signals.
                linked.extra_data = sociallogin.account.extra_data
                sociallogin.account = linked
                sociallogin.user = linked.user
            raise ImmediateHttpResponse(sso_test.finish_oidc(request, cfg, sociallogin))
        return sociallogin


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
