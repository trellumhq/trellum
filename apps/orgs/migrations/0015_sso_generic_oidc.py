# Generic OpenID Connect (internal planning ticket #074): an issuer URL in place of the Entra
# tenant id, a configurable groups claim, and the provider-neutral name for
# the group map. The provider id every linked identity carries in
# allauth's SocialAccount rows moves from entra-<slug> to oidc-<slug> in the
# same step, so the rename orphans no existing login.
import re

import django.core.validators
from django.db import migrations, models
from django.db.models import Value
from django.db.models.functions import Concat, Substr


_ENTRA_ISSUER_PREFIX = "https://login.microsoftonline.com/"
_ENTRA_ISSUER_SUFFIX = "/v2.0"
_ENTRA_TENANT = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,62}[A-Za-z0-9])?")


def _entra_issuer(tenant_id: str) -> str:
    tenant_id = (tenant_id or "").strip()
    if not _ENTRA_TENANT.fullmatch(tenant_id):
        return ""
    return f"{_ENTRA_ISSUER_PREFIX}{tenant_id}{_ENTRA_ISSUER_SUFFIX}"


def backfill_entra_issuers(apps, schema_editor):
    OrgSSOConfig = apps.get_model("orgs", "OrgSSOConfig")
    configs = OrgSSOConfig.objects.using(schema_editor.connection.alias)
    changed = []
    for config in configs.only("pk", "issuer_url", "entra_tenant_id").iterator():
        if config.issuer_url:
            continue
        if not config.entra_tenant_id:
            continue
        issuer = _entra_issuer(config.entra_tenant_id)
        if not issuer:
            raise ValueError("Cannot migrate an invalid non-empty Entra tenant ID.")
        config.issuer_url = issuer
        changed.append(config)
    if changed:
        configs.bulk_update(changed, ["issuer_url"])


def restore_entra_tenants(apps, schema_editor):
    OrgSSOConfig = apps.get_model("orgs", "OrgSSOConfig")
    configs = OrgSSOConfig.objects.using(schema_editor.connection.alias)
    changed = []
    for config in configs.only("pk", "issuer_url", "entra_tenant_id").iterator():
        if config.entra_tenant_id:
            continue
        issuer = config.issuer_url or ""
        if not (issuer.startswith(_ENTRA_ISSUER_PREFIX) and issuer.endswith(_ENTRA_ISSUER_SUFFIX)):
            continue
        tenant_id = issuer[len(_ENTRA_ISSUER_PREFIX):-len(_ENTRA_ISSUER_SUFFIX)]
        if _entra_issuer(tenant_id) == issuer:
            config.entra_tenant_id = tenant_id
            changed.append(config)
    if changed:
        configs.bulk_update(changed, ["entra_tenant_id"])


def _reprefix(apps, old: str, new: str) -> None:
    SocialAccount = apps.get_model("socialaccount", "SocialAccount")
    SocialAccount.objects.filter(provider__startswith=old).update(
        provider=Concat(Value(new), Substr("provider", len(old) + 1))
    )


def forwards(apps, schema_editor):
    _reprefix(apps, "entra-", "oidc-")


def backwards(apps, schema_editor):
    _reprefix(apps, "oidc-", "entra-")


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0014_orgassistantconfig_pricing_and_source_sharing'),
        ('socialaccount', '0006_alter_socialaccount_extra_data'),
    ]

    operations = [
        migrations.RenameField(
            model_name='orgssoconfig',
            old_name='entra_group_map',
            new_name='group_map',
        ),
        migrations.AlterField(
            model_name='orgssoconfig',
            name='group_map',
            field=models.JSONField(blank=True, default=dict, help_text='Identity provider group -> permission group id; synced on every login.'),
        ),
        migrations.AddField(
            model_name='orgssoconfig',
            name='groups_claim',
            field=models.CharField(default='groups', help_text="Token claim that lists the user's groups (groups, roles, or a namespaced claim).", max_length=100),
        ),
        migrations.AddField(
            model_name='orgssoconfig',
            name='issuer_url',
            field=models.URLField(blank=True, default='', help_text='OpenID Connect issuer, e.g. https://keycloak.internal/realms/acme.', max_length=500, validators=[django.core.validators.URLValidator(schemes=['https'])]),
        ),
        migrations.AlterField(
            model_name='orgssoconfig',
            name='auto_provision',
            field=models.BooleanField(default=False, help_text='Create portal accounts on first successful SSO login.'),
        ),
        migrations.AlterField(
            model_name='orgssoconfig',
            name='email_domains',
            field=models.JSONField(blank=True, default=list, help_text='Login emails at these domains are routed to single sign-on (e.g. ["demo.example"]).'),
        ),
        migrations.RunPython(backfill_entra_issuers, restore_entra_tenants),
        migrations.RemoveField(
            model_name='orgssoconfig',
            name='entra_tenant_id',
        ),
        migrations.RunPython(forwards, backwards),
    ]
