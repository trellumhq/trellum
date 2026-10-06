from django import forms

from apps.accounts import sso
from apps.orgs.models import Organization, OrgSSOConfig, PermissionGroup


class SSOConfigForm(forms.ModelForm):
    client_secret = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Client secret issued by your identity provider. Leave blank to keep the current one.",
    )
    ldap_bind_password = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Password of the bind DN. Leave blank to keep the current one.",
    )
    email_domains_text = forms.CharField(
        required=False, label="Email domains",
        help_text="One domain per line (e.g. demo.example). Logins at these domains route to single sign-on.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    group_map_text = forms.CharField(
        required=False, label="Group mapping",
        help_text=(
            "One per line: <group as it appears in the claim, or its DN in the directory> "
            "= <permission group name>. Synced on every SSO login."
        ),
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    class Meta:
        model = OrgSSOConfig
        fields = [
            "enabled", "auth_method", "issuer_url", "client_id", "client_secret",
            "ldap_server_uri", "ldap_ca_cert", "ldap_bind_dn", "ldap_bind_password",
            "ldap_user_search_base", "ldap_user_filter",
            "ldap_email_attr", "ldap_name_attr", "ldap_group_attr",
            "auto_provision", "default_org_role", "default_groups",
            "groups_claim", "extra_scopes", "allow_password_login", "enforce_sso",
        ]
        widgets = {
            "default_groups": forms.CheckboxSelectMultiple,
            "auth_method": forms.Select(attrs={"data-reveals": ""}),
            "ldap_ca_cert": forms.Textarea(attrs={"rows": 6, "spellcheck": "false"}),
        }

    def __init__(self, *args, org: Organization, **kwargs):
        instance = kwargs.get("instance")
        initial = kwargs.pop("initial", {})
        if instance is not None:
            initial.setdefault("email_domains_text", "\n".join(instance.email_domains or []))
            lines = []
            for idp_group, group_pk in (instance.group_map or {}).items():
                group = PermissionGroup.objects.filter(pk=group_pk, org=org).first()
                lines.append(f"{idp_group} = {group.name if group else group_pk}")
            initial.setdefault("group_map_text", "\n".join(lines))
        super().__init__(*args, initial=initial, **kwargs)
        self.org = org
        self.fields["default_groups"].queryset = org.permission_groups.order_by("name")
        # Directory fields are validated in _clean_ldap, and only when the
        # directory is the identity source; the select itself always submits
        # a value, so relaxing it only lets a post that omits it keep the
        # row's current source (the model default for a new row).
        for name in self.fields:
            if name == "auth_method" or name.startswith("ldap_"):
                self.fields[name].required = False
        #: The issuer's discovery document, once clean() fetched it; the view
        #: shows the resolved endpoints after save. Never stored.
        self.discovery: dict = {}

    def clean_issuer_url(self):
        # allauth appends /.well-known/openid-configuration verbatim, so a
        # trailing slash would double up; https only, or the code exchange
        # would carry the client secret in cleartext.
        url = (self.cleaned_data.get("issuer_url") or "").rstrip("/")
        if url and not url.startswith("https://"):
            raise forms.ValidationError("The issuer URL must use https.")
        return url

    def clean_extra_scopes(self):
        # Scopes go to the IdP space-separated; a comma would reach it as
        # part of a scope name ("groups,") and be refused as unknown.
        return " ".join((self.cleaned_data.get("extra_scopes") or "").replace(",", " ").split())

    def clean_email_domains_text(self):
        domains = []
        for line in (self.cleaned_data.get("email_domains_text") or "").splitlines():
            d = line.strip().lower().lstrip("@")
            if d:
                if "." not in d or " " in d:
                    raise forms.ValidationError(f"'{d}' is not a valid domain.")
                domains.append(d)
        return domains

    def clean_group_map_text(self):
        mapping = {}
        for line in (self.cleaned_data.get("group_map_text") or "").splitlines():
            if not line.strip():
                continue
            if "=" not in line:
                raise forms.ValidationError(f"'{line}' — expected: <group in claim> = <group name>")
            # Last "=", not first: a directory group is keyed by its DN,
            # and every DN is full of them (cn=analysts,ou=groups,...).
            idp_group, _, group_name = line.rpartition("=")
            idp_group, group_name = idp_group.strip(), group_name.strip()
            group = PermissionGroup.objects.filter(org=self.org, name=group_name).first()
            if group is None:
                raise forms.ValidationError(f"No permission group named '{group_name}'.")
            mapping[idp_group] = group.pk
        return mapping

    def clean(self):
        data = super().clean()
        if data.get("auth_method") == OrgSSOConfig.AUTH_LDAP:
            return self._clean_ldap(data)
        if self.errors.get("issuer_url"):
            return data  # already not a URL; nothing to discover
        if not data.get("enabled"):
            return data
        issuer_url = data.get("issuer_url") or ""
        if not issuer_url:
            self.add_error("issuer_url", "An issuer URL is required to enable single sign-on.")
            return data
        # Only when enabling: switching SSO off must never depend on the
        # IdP answering, or an outage locks enforce_sso members out for good.
        try:
            self.discovery = sso.discover(issuer_url)
        except ValueError as exc:
            self.add_error("issuer_url", str(exc))
        return data

    def _clean_ldap(self, data):
        uri = data.get("ldap_server_uri") or ""
        if uri and not uri.startswith(("ldap://", "ldaps://")):
            self.add_error("ldap_server_uri", "Enter ldap://host:389 or ldaps://host:636.")
        if data.get("enabled"):
            for name in ("ldap_server_uri", "ldap_user_search_base"):
                if not data.get(name):
                    self.add_error(name, "Required to enable directory sign-in.")
        for name in ("ldap_email_attr", "ldap_name_attr", "ldap_group_attr"):
            if not data.get(name):
                self.add_error(name, "Required for a directory.")
        if "{login}" not in (data.get("ldap_user_filter") or ""):
            self.add_error("ldap_user_filter", "The filter must contain {login}.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.org = self.org
        obj.email_domains = self.cleaned_data["email_domains_text"]
        obj.group_map = self.cleaned_data["group_map_text"]
        if obj.pk:
            stored = type(obj).objects.get(pk=obj.pk)
            if not self.cleaned_data.get("client_secret"):
                obj.client_secret = stored.client_secret
            if not self.cleaned_data.get("ldap_bind_password"):
                obj.ldap_bind_password = stored.ldap_bind_password
        if commit:
            obj.save()
            self.save_m2m()
        return obj
