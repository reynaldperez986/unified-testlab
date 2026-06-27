from django.contrib import admin
from .models import Tenant, UserProfile


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display  = ("name", "slug", "is_active", "created_at")
    search_fields = ("name", "slug")
    ordering      = ("name",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display  = ("user", "tenant")
    list_select_related = ("user", "tenant")
    search_fields = ("user__username", "tenant__name")
    raw_id_fields = ("user",)
    ordering      = ("user__username",)
