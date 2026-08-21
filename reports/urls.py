from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("daily/", views.daily, name="daily"),
    path("daily-profit/", views.daily_profit, name="daily_profit"),
    path("profit/", views.profit, name="profit"),
    path("net-worth/", views.net_worth, name="net_worth"),
    path("trial/", views.trial, name="trial"),
    path("banks/", views.banks, name="banks"),
    path("customers/", views.customers, name="customers"),
    path("statement/<int:pk>/xlsx/", views.statement_export, name="statement_export"),
    path("statement/<int:pk>/pdf/", views.statement_pdf, name="statement_pdf"),
]
