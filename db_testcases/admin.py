from django.contrib import admin

from .models import AuditLog, DatabaseConnection, TestCase, TestExecution, Theme, UserProfile, UserThemePreference


@admin.register(DatabaseConnection)
class DatabaseConnectionAdmin(admin.ModelAdmin):
    list_display = ("name", "db_type", "host", "port", "database_name", "created_at")
    search_fields = ("name", "host", "database_name")
    list_filter = ("db_type",)


@admin.register(TestCase)
class TestCaseAdmin(admin.ModelAdmin):
    list_display = ("name", "test_type", "connection", "is_active", "created_at")
    search_fields = ("name", "table_name")
    list_filter = ("test_type", "is_active")


@admin.register(TestExecution)
class TestExecutionAdmin(admin.ModelAdmin):
    list_display = ("test_case", "status", "executed_at")
    list_filter = ("status", "executed_at")
    search_fields = ("test_case__name", "details", "actual_value")


@admin.register(Theme)
class ThemeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_default", "primary_color", "accent_color")
    list_filter = ("is_default",)
    search_fields = ("name",)


@admin.register(UserThemePreference)
class UserThemePreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "theme")
    search_fields = ("user__username", "theme__name")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("user__username",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("user", "action", "target_type", "target_name", "ip_address", "created_at")
    list_filter = ("action", "target_type", "created_at")
    search_fields = ("user__username", "target_name", "details", "ip_address")
    readonly_fields = ("user", "action", "target_type", "target_id", "target_name", "details", "ip_address", "user_agent", "created_at")
    
    def has_add_permission(self, request):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
