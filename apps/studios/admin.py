from django.contrib import admin

from .models import Studio, StudioMembership, StudioRepo


class StudioMembershipInline(admin.TabularInline):
    model = StudioMembership
    extra = 0


@admin.register(Studio)
class StudioAdmin(admin.ModelAdmin):
    list_display = ["slug", "org", "name", "created_at"]
    list_filter = ["org"]
    inlines = [StudioMembershipInline]

    def get_readonly_fields(self, request, obj=None):
        return ["slug"] if obj else []


@admin.register(StudioRepo)
class StudioRepoAdmin(admin.ModelAdmin):
    list_display = ["studio", "repo_url", "branch", "sync_interval_minutes", "last_sync_at"]
