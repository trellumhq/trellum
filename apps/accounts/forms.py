from datetime import datetime, time

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone

from apps.orgs.models import Organization, slug_validator
from apps.orgs.provisioning import UserExistsError

from .models import ApiKey, User

__all__ = [
    "ApiKeyCreateForm",
    "InviteAcceptForm",
    "LoginForm",
    "MfaCodeForm",
    "OrgCreateForm",
    "PasswordPairMixin",
    "SetupAccountForm",
    "SetupOrgForm",
    "TotpConfirmForm",
    "UserExistsError",
    "create_user_account",
]


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autofocus": True}))
    password = forms.CharField(widget=forms.PasswordInput, required=False)
    # No remember-me field: InstanceConfig.session_remember_me_enabled exists
    # as a stubbed-off knob (see its docstring) but the checkbox itself is
    # deferred v1 UI -- see .lavish/session-security-design.md open question 3.


class MfaCodeForm(forms.Form):
    """The login step-up prompt: a 6-digit TOTP code, or a recovery code in
    the same field (apps.accounts.mfa distinguishes by format on submit)."""

    code = forms.CharField(
        label="Verification code", max_length=32,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "one-time-code"}),
    )


class TotpConfirmForm(forms.Form):
    """Enrollment step 2: prove the authenticator app was set up correctly."""

    code = forms.CharField(
        label="6-digit code", max_length=8,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "one-time-code"}),
    )


class PasswordPairMixin(forms.Form):
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Repeat password", widget=forms.PasswordInput)

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        if p1:
            validate_password(p1)
        return cleaned


class SetupOrgForm(forms.Form):
    """First-run wizard, step 3: the first organization.

    Collected before the account so the wizard never has to hold a plaintext
    password in the session between two requests — these two fields are the
    only thing carried across, and the account step commits both at once.
    """

    org_name = forms.CharField(
        label="Organization name", max_length=200,
        widget=forms.TextInput(attrs={"autofocus": True, "id": "id_org_name"}),
    )
    org_slug = forms.SlugField(
        label="Organization slug", max_length=64, validators=[slug_validator],
        help_text="Lowercase letters, digits, hyphens. Cannot be changed later.",
    )

    def clean_org_slug(self):
        slug = self.cleaned_data["org_slug"]
        if Organization.objects.filter(slug=slug).exists():
            raise forms.ValidationError("An organization with this slug already exists.")
        return slug


class SetupAccountForm(PasswordPairMixin):
    """First-run wizard, step 4: the instance operator's account."""

    email = forms.EmailField(widget=forms.EmailInput(attrs={"autofocus": True}))
    name = forms.CharField(label="Your name", max_length=150, required=False)

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class InviteAcceptForm(PasswordPairMixin):
    """Account creation while accepting an invitation (email is fixed)."""

    name = forms.CharField(max_length=150, required=False)


class OrgCreateForm(forms.Form):
    name = forms.CharField(label="Organization name", max_length=200)
    slug = forms.SlugField(max_length=64, validators=[slug_validator])

    def clean_slug(self):
        slug = self.cleaned_data["slug"]
        if Organization.objects.filter(slug=slug).exists():
            raise forms.ValidationError("An organization with this slug already exists.")
        return slug


def create_user_account(email: str, name: str, password: str) -> User:
    if User.objects.filter(email__iexact=email).exists():
        raise UserExistsError(email)
    return User.objects.create_user(email=email, name=name, password=password)


class ApiKeyCreateForm(forms.Form):
    """/me/api-keys: one key for one of the user's organizations. ``read``
    unless the person deliberately picks write; no expiry unless they set
    one (design §7, decision 5)."""

    name = forms.CharField(max_length=100)
    org = forms.ModelChoiceField(
        queryset=Organization.objects.none(), label="Organization", empty_label=None
    )
    scopes = forms.ChoiceField(choices=ApiKey.SCOPE_CHOICES, initial=ApiKey.READ, label="Scope")
    expires_at = forms.DateField(
        required=False, label="Expires", widget=forms.DateInput(attrs={"type": "date"})
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["org"].queryset = Organization.objects.filter(
            is_active=True, api_keys_enabled=True, memberships__user=user
        ).order_by("slug")

    def clean_expires_at(self):
        day = self.cleaned_data["expires_at"]
        if day is None:
            return None
        if day <= timezone.localdate():
            raise forms.ValidationError("Pick a date after today.")
        # The key works through the whole of the chosen day.
        return timezone.make_aware(datetime.combine(day, time.max))
