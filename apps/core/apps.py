from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"

    def ready(self):
        # Registers the authentication audit receivers. Imported for the side
        # effect: without this nothing listens to Django's login/logout/failed
        # signals, and the audit trail cannot answer "was this account
        # compromised?".
        from apps.core import auth_events  # noqa: F401
