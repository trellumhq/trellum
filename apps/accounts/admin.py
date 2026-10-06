from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Invitation, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["email"]
    list_display = ["email", "name", "is_active", "is_superuser", "date_joined"]
    search_fields = ["email", "name"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("name",)}),
        # dormancy_warned_at sits beside is_active rather than under
        # "Dates" because it is the field that decides whether re-enabling
        # an account sticks: apps.core.retention disables it again on the
        # next sweep while a live warning is still stamped here. This form
        # is the only surface in the product that can flip is_active back,
        # so it has to be the one that shows what else has to be cleared.
        ("Status", {"fields": ("is_active", "is_staff", "is_superuser", "dormancy_warned_at")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"fields": ("email", "password1", "password2")}),)


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ["email", "org", "org_role", "created_at", "expires_at", "accepted_at"]
    list_filter = ["org"]
    search_fields = ["email"]
