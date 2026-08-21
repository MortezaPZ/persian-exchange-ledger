from django.contrib import admin

from .models import Currency, FxRate, Party, RateSource, RateSourceMapping


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_base", "is_active", "decimal_places", "sort_order")
    list_filter = ("is_active", "is_base")


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "currency", "phone", "is_active", "is_system")
    list_filter = ("kind", "is_active")
    search_fields = ("name", "code", "phone", "telegram_id", "whatsapp_no")


class MappingInline(admin.TabularInline):
    model = RateSourceMapping
    extra = 1


@admin.register(RateSource)
class RateSourceAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "is_active")
    inlines = [MappingInline]


@admin.register(FxRate)
class FxRateAdmin(admin.ModelAdmin):
    list_display = ("currency", "rate_to_base", "effective_at", "source_label")
    list_filter = ("currency",)
    date_hierarchy = "effective_at"

    def has_change_permission(self, request, obj=None):
        return False
