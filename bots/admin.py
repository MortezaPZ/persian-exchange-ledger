from django.contrib import admin

from .models import BotConfig, BotMessage


@admin.register(BotConfig)
class BotConfigAdmin(admin.ModelAdmin):
    list_display = ("platform", "is_enabled", "updated_at")


@admin.register(BotMessage)
class BotMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "platform", "direction", "status", "party", "sender_id")
    list_filter = ("platform", "direction", "status")
    search_fields = ("text", "sender_id", "party__name")
    readonly_fields = [f.name for f in BotMessage._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
