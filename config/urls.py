from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("ledger.urls")),
    path("accounts/", include("accounts.urls")),
    path("core/", include("core.urls")),
    path("reports/", include("reports.urls")),
    path("bots/", include("bots.urls")),
    path("system/", include("maintenance.urls")),
]
