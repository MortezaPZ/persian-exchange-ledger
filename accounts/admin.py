from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import AuditLog, Permission, Role, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "full_name", "role", "is_active", "is_superuser")
    list_filter = ("role", "is_active", "is_superuser")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("اطلاعات صرافی", {"fields": ("full_name", "phone", "role")}),
    )


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("title", "code", "is_system")
    filter_horizontal = ("permissions",)


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("title", "code", "group_title")
    search_fields = ("code", "title")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "username_snapshot", "action", "summary", "ip_address")
    list_filter = ("action",)
    search_fields = ("summary", "username_snapshot", "object_id")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
