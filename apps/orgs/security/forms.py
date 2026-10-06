from django import forms

from apps.orgs.models import OrgSecurityPolicy


class SecurityPolicyForm(forms.ModelForm):
    class Meta:
        model = OrgSecurityPolicy
        fields = ["require_mfa", "mfa_grace_days"]
        labels = {
            "require_mfa": "Require MFA for every member who signs in with a password",
            "mfa_grace_days": "Grace period (days)",
        }
        help_texts = {
            "mfa_grace_days": (
                "Members without a device may still sign in for this many days "
                "after this is turned on (or after their account is created), "
                "with a reminder banner. 0 = enrollment required immediately."
            ),
        }

    def __init__(self, *args, org, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = org

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.org = self.org
        if commit:
            obj.save()
        return obj
