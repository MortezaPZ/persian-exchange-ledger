from django.urls import path

from . import views

app_name = "bots"

urlpatterns = [
    path("", views.bot_dashboard, name="dashboard"),
    path("messages/", views.message_log, name="messages"),
    path("<str:platform>/settings/", views.bot_settings, name="settings"),
    path("<str:platform>/test/", views.bot_test, name="test"),
    path("webhook/whatsapp/", views.whatsapp_webhook, name="whatsapp_webhook"),
]
