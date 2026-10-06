"""Data preservation in the generic OIDC migration."""

import importlib
from types import SimpleNamespace

import pytest


migration = importlib.import_module("apps.orgs.migrations.0015_sso_generic_oidc")


class _Rows(list):
    def only(self, *fields):
        assert fields == ("pk", "issuer_url", "entra_tenant_id")
        return self

    def using(self, alias):
        assert alias == "default"
        return self

    def all(self):
        return self

    def iterator(self):
        return iter(self)

    def bulk_update(self, rows, fields):
        self.updated = (list(rows), fields)


class _Apps:
    def __init__(self, rows):
        self.model = SimpleNamespace(objects=rows)

    def get_model(self, app_label, model_name):
        assert (app_label, model_name) == ("orgs", "OrgSSOConfig")
        return self.model


_EDITOR = SimpleNamespace(connection=SimpleNamespace(alias="default"))


def test_entra_tenant_is_preserved_as_issuer_without_touching_other_sso_fields():
    tenant = "11111111-2222-3333-4444-555555555555"
    migrated = SimpleNamespace(
        issuer_url="", entra_tenant_id=tenant, client_id="client-1",
        client_secret="encrypted-secret", group_map={"entra-group": 7},
    )
    existing = SimpleNamespace(
        issuer_url="https://idp.example/realms/acme", entra_tenant_id="old-tenant",
    )
    empty = SimpleNamespace(issuer_url="", entra_tenant_id="")
    rows = _Rows([migrated, existing, empty])

    migration.backfill_entra_issuers(_Apps(rows), _EDITOR)

    assert migrated.issuer_url == f"https://login.microsoftonline.com/{tenant}/v2.0"
    assert migrated.client_id == "client-1"
    assert migrated.client_secret == "encrypted-secret"
    assert migrated.group_map == {"entra-group": 7}
    assert existing.issuer_url == "https://idp.example/realms/acme"
    assert empty.issuer_url == ""
    assert rows.updated == ([migrated], ["issuer_url"])


def test_malformed_nonempty_tenant_stops_before_the_field_can_be_removed():
    malformed = SimpleNamespace(issuer_url="", entra_tenant_id="tenant/../../other")
    rows = _Rows([malformed])

    with pytest.raises(ValueError, match="invalid non-empty Entra tenant ID"):
        migration.backfill_entra_issuers(_Apps(rows), _EDITOR)

    assert malformed.issuer_url == ""
    assert not hasattr(rows, "updated")


def test_reverse_restores_only_an_issuer_created_from_a_safe_tenant_id():
    restored = SimpleNamespace(
        issuer_url="https://login.microsoftonline.com/contoso.onmicrosoft.com/v2.0",
        entra_tenant_id="",
    )
    custom = SimpleNamespace(issuer_url="https://idp.example/realms/acme", entra_tenant_id="")
    existing = SimpleNamespace(
        issuer_url="https://login.microsoftonline.com/new/v2.0",
        entra_tenant_id="keep-me",
    )
    rows = _Rows([restored, custom, existing])

    migration.restore_entra_tenants(_Apps(rows), _EDITOR)

    assert restored.entra_tenant_id == "contoso.onmicrosoft.com"
    assert custom.entra_tenant_id == ""
    assert existing.entra_tenant_id == "keep-me"
    assert rows.updated == ([restored], ["entra_tenant_id"])
