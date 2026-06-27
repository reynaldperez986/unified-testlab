from django.contrib import admin
from .models import Environment, TestCase, TestExecution, AuditLog, UserProfile, ApiModule, ModuleEndpoint


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'is_active_profile', 'created_at']
    list_filter = ['role', 'is_active_profile']


@admin.register(Environment)
class EnvironmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'base_url', 'auth_type', 'is_active', 'created_by']
    list_filter = ['is_active', 'auth_type']


@admin.register(TestCase)
class TestCaseAdmin(admin.ModelAdmin):
    list_display = ['name', 'http_method', 'endpoint', 'module', 'project', 'is_active']
    list_filter = ['http_method', 'module', 'project', 'is_active']
    search_fields = ['name', 'endpoint']


@admin.register(TestExecution)
class TestExecutionAdmin(admin.ModelAdmin):
    list_display = ['test_case', 'environment', 'executed_by', 'result_status', 'executed_at']
    list_filter = ['result_status', 'environment']


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'action', 'target_type', 'timestamp']
    list_filter = ['action']
    readonly_fields = ['user', 'action', 'target_type', 'target_id', 'details', 'ip_address', 'timestamp']


class ModuleEndpointInline(admin.TabularInline):
    model = ModuleEndpoint
    extra = 0


@admin.register(ApiModule)
class ApiModuleAdmin(admin.ModelAdmin):
    list_display = ['name', 'source_file', 'uploaded_by', 'created_at']
    inlines = [ModuleEndpointInline]
