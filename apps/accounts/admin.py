from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db import router, transaction

from apps.core.audit import audit

from .models import Invitation, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["email"]
    list_display = ["email", "name", "is_active", "is_superuser", "date_joined"]
    search_fields = ["email", "name"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("name",)}),
        ("Status", {"fields": ("is_active", "is_staff", "is_superuser", "dormancy_warned_at")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"fields": ("email", "password1", "password2")}),)

    def save_model(self, request, obj, form, change):
        # ModelForm has already changed obj. Read and lock the stored state
        # so retention and simultaneous admin saves cannot invent a transition.
        using = router.db_for_write(self.model)
        with transaction.atomic(using=using):
            reactivated = False
            if change:
                previous = self.model.objects.using(using).select_for_update().get(pk=obj.pk)
                reactivated = not previous.is_active and obj.is_active
            if reactivated:
                obj.dormancy_warned_at = None
            super().save_model(request, obj, form, change)
            if reactivated:
                # This operation must not succeed without its attribution. The
                # shared helper returns None on failure; roll back this save.
                if audit(request, "account.reactivate", target=obj) is None:
                    raise RuntimeError("Could not audit account reactivation")


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ["email", "org", "org_role", "created_at", "expires_at", "accepted_at"]
    list_filter = ["org"]
    search_fields = ["email"]
