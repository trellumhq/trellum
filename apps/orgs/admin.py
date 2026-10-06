from django.contrib import admin

from .models import (
    Organization,
    OrgAssistantConfig,
    OrgMembership,
    OrgSSOConfig,
    PermissionGroup,
    PermissionGroupGrant,
    PermissionGroupMembership,
)


class OrgMembershipInline(admin.TabularInline):
    model = OrgMembership
    extra = 0


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ["slug", "name", "is_active", "created_at"]
    inlines = [OrgMembershipInline]

    def get_readonly_fields(self, request, obj=None):
        return ["slug"] if obj else []


class GrantInline(admin.TabularInline):
    model = PermissionGroupGrant
    extra = 0


class GroupMembershipInline(admin.TabularInline):
    model = PermissionGroupMembership
    extra = 0


@admin.register(PermissionGroup)
class PermissionGroupAdmin(admin.ModelAdmin):
    list_display = ["name", "org", "org_role", "default_studio_role"]
    list_filter = ["org"]
    inlines = [GrantInline, GroupMembershipInline]


@admin.register(OrgSSOConfig)
class OrgSSOConfigAdmin(admin.ModelAdmin):
    list_display = ["org", "enabled", "issuer_url"]


@admin.register(OrgAssistantConfig)
class OrgAssistantConfigAdmin(admin.ModelAdmin):
    list_display = ["org", "enabled", "provider", "model"]
