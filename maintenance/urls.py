from django.urls import path

from . import views

app_name = "maintenance"

urlpatterns = [
    path("backup/", views.backup_dashboard, name="backup"),
    path("backup/create/", views.backup_create, name="backup_create"),
    path("backup/download/", views.backup_download, name="backup_download"),
    path("backup/restore/", views.backup_restore, name="backup_restore"),
    path("reset/", views.data_reset, name="data_reset"),
    path("mobile/", views.mobile_access, name="mobile"),
]
