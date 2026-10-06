from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        # Registers the login-stamping receiver (apps.accounts.session_policy)
        # that SessionSecurityMiddleware's idle/absolute-cap enforcement reads
        # on every later request. Imported for the side effect: without this,
        # a fresh login carries no sp_login_at/sp_last_seen/sp_epoch stamps.
        from apps.accounts import session_policy  # noqa: F401
