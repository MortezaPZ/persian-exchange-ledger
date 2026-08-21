from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dashboard/data/", views.dashboard_data, name="dashboard_data"),
    path("api/topbar-balances/", views.topbar_balances, name="topbar_balances"),

    path("deal/new/", views.deal_create, name="deal_create"),
    path("cash/<str:kind>/new/", views.cash_create, name="cash_create"),
    path("transfer/new/", views.transfer_create, name="transfer_create"),
    path("party-transfer/new/", views.party_transfer_create, name="party_transfer_create"),
    path("expense-income/<str:kind>/new/", views.expense_income_create, name="expense_income_create"),
    path("opening/new/", views.opening_create, name="opening_create"),

    path("vouchers/", views.voucher_list, name="voucher_list"),
    path("vouchers/<int:pk>/", views.voucher_detail, name="voucher_detail"),
    path("vouchers/<int:pk>/void/", views.voucher_void, name="voucher_void"),
    path("vouchers/<int:pk>/delete/", views.voucher_delete, name="voucher_delete"),
    path("vouchers/<int:pk>/edit/", views.voucher_edit, name="voucher_edit"),

    path("statement/", views.statement, name="statement"),
    path("statement/<int:pk>/", views.statement, name="statement_detail"),
    path("api/party/<int:pk>/balance/", views.party_balance_json, name="party_balance_json"),
]
