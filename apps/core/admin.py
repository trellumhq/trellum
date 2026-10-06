from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only, including delete.

    The model docstring and the published audit-log documentation both
    promise an append-only
    trail. Blocking add and change without blocking delete left the bulk-delete
    action available to any superuser, which is the one operation that actually
    destroys evidence — and the trail is only worth keeping if the people it
    records cannot edit it.
    """

    list_display = [
        "created_at", "org", "actor", "impersonator", "action", "target_type", "target_id",
    ]
    list_filter = ["action", "org"]
    search_fields = ["action", "target_id", "actor__email", "impersonator__email"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
