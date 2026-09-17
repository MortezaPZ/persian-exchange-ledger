from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Currency, Party
from ledger.models import Deal, Voucher


class ReportFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(username="report-tester", password="test-only")
        cls.aed = Currency.objects.create(code="AED", name="درهم")
        cls.usd = Currency.objects.create(code="USD", name="دلار")
        cls.party = Party.objects.create(name="مشتری گزارش")
        cls.day = date(2026, 9, 17)
        for currency, pnl in ((cls.aed, "120"), (cls.usd, "730")):
            voucher = Voucher.objects.create(kind=Voucher.Kind.DEAL, status=Voucher.Status.FINAL,
                                             date=cls.day, created_by=cls.user)
            Deal.objects.create(voucher=voucher, counterparty=cls.party, currency=currency,
                                side=Deal.Side.SELL, quantity=10, unit_price=100,
                                total_base=1000, realized_pnl=Decimal(pnl), date=cls.day)

    def setUp(self):
        self.client.force_login(self.user)

    def test_daily_currency_filters_rows_and_totals_and_shows_controls(self):
        response = self.client.get(reverse("reports:daily"), {
            "from": "1405/06/26", "to": "1405/06/26", "currency": self.usd.pk, "side": "sell",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["totals"]["count"], 1)
        self.assertEqual({d.currency_id for d in response.context["deals"]}, {self.usd.pk})
        self.assertContains(response, 'name="currency"')
        self.assertContains(response, 'name="side"')
        self.assertContains(response, 'selected')

    def test_profit_currency_filters_totals_and_controls(self):
        for name, total_key in (("profit", "realized"), ("daily_profit", "profit")):
            with self.subTest(report=name):
                response = self.client.get(reverse("reports:" + name), {
                    "from": "1405/06/26", "to": "1405/06/26", "currency": self.usd.pk,
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["totals"][total_key], Decimal("730"))
                self.assertContains(response, 'name="currency"')
                self.assertNotContains(response, 'name="side"')

    def test_invalid_filters_are_rejected_not_silently_ignored(self):
        self.client.raise_request_exception = False
        for report in ("daily", "daily_profit", "profit"):
            for currency in ("not-a-number", "999999999999999999999999", "9999"):
                with self.subTest(report=report, currency=currency):
                    response = self.client.get(reverse("reports:" + report), {"currency": currency})
                    self.assertEqual(response.status_code, 400)
        response = self.client.get(reverse("reports:daily"), {"side": "invalid"})
        self.assertEqual(response.status_code, 400)

    def test_daily_side_filters_rows_and_totals(self):
        voucher = Voucher.objects.create(kind=Voucher.Kind.DEAL, status=Voucher.Status.FINAL,
                                         date=self.day, created_by=self.user)
        buy = Deal.objects.create(voucher=voucher, counterparty=self.party, currency=self.aed,
                                  side=Deal.Side.BUY, quantity=5, unit_price=80,
                                  total_base=400, date=self.day)
        response = self.client.get(reverse("reports:daily"), {
            "from": "1405/06/26", "to": "1405/06/26", "side": "buy",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["deals"]), [buy])
        self.assertEqual(response.context["totals"], {"buy": Decimal("400"), "sell": 0, "count": 1})

    def test_daily_profit_currency_uses_only_selected_stored_pnl(self):
        response = self.client.get(reverse("reports:daily_profit"), {
            "from": "1405/06/26", "to": "1405/06/26", "currency": self.aed.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["totals"]["profit"], Decimal("120"))
        self.assertEqual(response.context["rows"][0]["sell_count"], 1)
