from django.contrib import admin

from .models import Deal, Entry, InventoryPosition, Sequence, Voucher


class EntryInline(admin.TabularInline):
    model = Entry
    extra = 0
    readonly_fields = ("party", "currency", "amount", "rate_to_base", "description", "date")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = ("number", "date", "kind", "status", "description", "created_by")
    list_filter = ("kind", "status")
    search_fields = ("number", "description", "external_key")
    date_hierarchy = "date"
    inlines = [EntryInline]

    def has_change_permission(self, request, obj=None):
        # سند قطعی از پنل مدیریت هم قابل ویرایش نیست
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Deal)
class DealAdmin(admin.ModelAdmin):
    list_display = ("date", "side", "currency", "quantity", "unit_price",
                    "total_base", "realized_pnl", "counterparty")
    list_filter = ("side", "currency")
    date_hierarchy = "date"

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(InventoryPosition)
class InventoryPositionAdmin(admin.ModelAdmin):
    list_display = ("currency", "quantity", "avg_unit_cost", "updated_at")


admin.site.register(Sequence)
admin.site.site_header = "مدیریت سامانه حسابداری صرافی"
admin.site.site_title = "سامانه حسابداری صرافی"
admin.site.index_title = "پنل مدیریت"
