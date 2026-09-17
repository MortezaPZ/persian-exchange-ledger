from django.contrib import admin
from django.urls import include, path

from core.receipts import receipt_download

urlpatterns = [
    path("receipts/<int:pk>/download/", receipt_download, name="receipt_download"),
    path("admin/", admin.site.urls),
    path("", include("ledger.urls")),
    path("accounts/", include("accounts.urls")),
    path("core/", include("core.urls")),
    path("reports/", include("reports.urls")),
    path("bots/", include("bots.urls")),
    path("system/", include("maintenance.urls")),
]
