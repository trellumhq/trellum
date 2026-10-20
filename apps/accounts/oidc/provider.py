"""Keep authorization and callback adapters on the same public URL."""
from allauth.socialaccount.providers.openid_connect.provider import OpenIDConnectProvider

from .views import PublicURLAdapter


class PublicURLProvider(OpenIDConnectProvider):
    oauth2_adapter_class = PublicURLAdapter
