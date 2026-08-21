"""آزمون‌های نوار موجودی بالای صفحه.

کارفرما خواسته بود موجودی درهم، تتر و ریال همیشه بالای صفحه دیده شود.
"""
from decimal import Decimal

from core.models import Currency, Party

from . import balances, services
from .models import Deal, Voucher
from .tests import LedgerTestBase


class TopbarBalanceTests(LedgerTestBase):
    def test_snapshot_lists_every_active_currency(self):
        rows = balances.house_currency_snapshot()
        names = [row["currency"].name for row in rows]
        self.assertIn("ریال", names)
        self.assertIn("درهم", names)
        self.assertIn("دلار", names)

    def test_base_currency_shows_bank_and_cash_total(self):
        services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("50000000"),
            created_by=self.user,
        )
        rows = {r["currency"].code: r["amount"] for r in balances.house_currency_snapshot()}
        self.assertEqual(rows["IRR"], Decimal("50000000"))

    def test_currency_without_delivery_account_does_not_touch_cashbox(self):
        """اگر ارز روی حساب طرف بماند (بدون صندوق تحویل)، هنوز وارد هیچ صندوقی نشده.

        نوار بالا باید موجودی *واقعی* صندوق را نشان بدهد، نه اینکه چقدر
        معامله «رفته» — چون کارفرما گفته بود این دو باید یکی باشند و
        نباید فرق کنند.
        """
        services.post_deal(side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
                           currency=self.aed, quantity=Decimal("100"),
                           unit_price=Decimal("500000"), created_by=self.user)
        services.post_deal(side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
                           currency=self.aed, quantity=Decimal("30"),
                           unit_price=Decimal("520000"), created_by=self.user)

        rows = {r["currency"].code: r["amount"] for r in balances.house_currency_snapshot()}
        self.assertEqual(rows["AED"], Decimal("0"))

    def test_delivery_account_reflected_accurately_in_topbar(self):
        """با صندوق تحویل، نوار بالا دقیقاً موجودی واقعی صندوق را نشان می‌دهد."""
        box = Party.objects.create(kind=Party.Kind.CASHBOX, name="صندوق درهم آزمون",
                                   currency=self.aed, code="CASH-AED-TEST")
        services.post_deal(side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
                           currency=self.aed, quantity=Decimal("100"),
                           unit_price=Decimal("500000"), created_by=self.user,
                           delivery_account=box)
        services.post_deal(side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
                           currency=self.aed, quantity=Decimal("30"),
                           unit_price=Decimal("520000"), created_by=self.user,
                           delivery_account=box)

        rows = {r["currency"].code: r["amount"] for r in balances.house_currency_snapshot()}
        self.assertEqual(rows["AED"], Decimal("70"))

    def test_multiple_cashboxes_for_same_currency_are_summed(self):
        """اگر برای یک ارز چند صندوق داشته باشیم (مثلاً «صرافی البانک»)، جمعشان نشان داده می‌شود."""
        box1 = Party.objects.create(kind=Party.Kind.CASHBOX, name="صندوق تتر ۱",
                                    currency=self.usd, code="U1")
        box2 = Party.objects.create(kind=Party.Kind.BANK, name="صرافی البانک",
                                    currency=self.usd, code="U2")
        services.post_deal(side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
                           currency=self.usd, quantity=Decimal("100"),
                           unit_price=Decimal("900000"), created_by=self.user,
                           delivery_account=box1)
        services.post_deal(side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
                           currency=self.usd, quantity=Decimal("50"),
                           unit_price=Decimal("900000"), created_by=self.user,
                           delivery_account=box2)
        rows = {r["currency"].code: r["amount"] for r in balances.house_currency_snapshot()}
        self.assertEqual(rows["USD"], Decimal("150"))

    def test_topbar_appears_on_every_page(self):
        self.client.force_login(self.user)
        for url in ["/", "/deal/new/", "/vouchers/", "/core/parties/"]:
            html = self.client.get(url).content.decode()
            self.assertIn("topbar-balances", html, f"نوار موجودی در {url} نیست")
            self.assertIn("درهم", html)

    def test_topbar_hidden_before_login(self):
        html = self.client.get("/accounts/login/").content.decode()
        self.assertNotIn("topbar-balances", html)

    def test_api_returns_all_currencies(self):
        self.client.force_login(self.user)
        data = self.client.get("/api/topbar-balances/").json()
        names = [row["name"] for row in data["rows"]]
        self.assertIn("ریال", names)
        self.assertIn("درهم", names)
        self.assertIn("تتر", names) if Currency.objects.filter(code="USDT").exists() else None

    def test_api_requires_login(self):
        response = self.client.get("/api/topbar-balances/")
        self.assertEqual(response.status_code, 302)

    def test_whole_numbers_drop_decimals(self):
        from core.money import format_amount_compact

        self.assertEqual(format_amount_compact(Decimal("8300"), 2), "8,300")
        self.assertEqual(format_amount_compact(Decimal("8300.50"), 2), "8,300.50")
        self.assertEqual(format_amount_compact(Decimal("0"), 2), "0")
        self.assertEqual(format_amount_compact(Decimal("-7000"), 2), "−7,000")
