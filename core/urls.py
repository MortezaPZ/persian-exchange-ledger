from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("parties/", views.party_list, name="party_list"),
    path("parties/new/", views.party_edit, name="party_create"),
    path("parties/<int:pk>/edit/", views.party_edit, name="party_edit"),
    path("parties/<int:pk>/", views.party_detail, name="party_detail"),

    path("currencies/", views.currency_list, name="currency_list"),
    path("currencies/new/", views.currency_edit, name="currency_create"),
    path("currencies/<int:pk>/", views.currency_edit, name="currency_edit"),

    path("rates/", views.rate_dashboard, name="rate_dashboard"),
    path("rates/manual/", views.rate_manual_add, name="rate_manual_add"),
    path("rates/fetch/", views.rate_fetch, name="rate_fetch_all"),
    path("rates/fetch/<int:pk>/", views.rate_fetch, name="rate_fetch"),
    path("rates/sources/new/", views.rate_source_edit, name="rate_source_create"),
    path("rates/sources/<int:pk>/", views.rate_source_edit, name="rate_source_edit"),
]
