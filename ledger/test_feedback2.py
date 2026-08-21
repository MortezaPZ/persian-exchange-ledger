"""آزمون‌های بازخورد دور دوم (کارفرما، ادامه تست دستی).

هر کلاس یکی از موارد گزارش‌شده را می‌سنجد:
  • settle_account برای خرید و فروش باید موجودی بانک واقعی را درست حساب کند
  • هزینه و درآمد باید قابل ثبت باشند
  • تراز دارایی باید عدد درست بدهد
"""
from decimal import Decimal

from core.models import Party

from . import balances, services
from .models import Deal, Voucher
from .tests import LedgerTestBase


class SettleAccountSignTests(LedgerTestBase):
    """«خرید تتر ثبت کردم، ریال بدهکار شد به جای بستانکار، صندوق تتر کم شد به جای زیاد.»

    ریشه‌اش این بود که settle_account به‌جای «پل داخلی ریال» می‌نشست، نه
    به‌جای «طرف حساب». حالا هر دو (ارز و ریال) به‌طور مستقل می‌توانند به یک
    حساب واقعی وصل شوند یا روی حساب طرف بمانند.
    """

    def setUp(self):
        self.usdt_box = Party.objects.create(
            kind=Party.Kind.CASHBOX, name="صندوق تتر", currency=self.usd, code="BOX-USD",
        )
        self.rial_bank = Party.objects.create(
            kind=Party.Kind.BANK, name="بانک تسویه", currency=self.rial, code="BANK-TEST",
        )

    def test_buy_with_settle_account_debits_the_real_bank_correctly(self):
        """خرید با تسویه فوری: بانک باید بدهکار (پول کم) شود، نه بستانکار."""
        before = balances.party_balances_map(self.rial_bank).get(self.rial.id, Decimal("0"))
        services.post_deal(
            side=Deal.Side.BUY, date=self.today, counterparty=self.customer,
            currency=self.usd, quantity=Decimal("500"), unit_price=Decimal("1800000"),
            created_by=self.user, delivery_account=self.usdt_box, settle_account=self.rial_bank,
        )
        after = balances.party_balances_map(self.rial_bank).get(self.rial.id, Decimal("0"))
        # پول از بانک خارج شده، پس مانده باید کم (منفی) شود
        self.assertEqual(after - before, Decimal("-900000000"))

    def test_buy_with_both_accounts_leaves_no_residual_customer_balance(self):
        services.post_deal(
            side=Deal.Side.BUY, date=self.today, counterparty=self.customer,
            currency=self.usd, quantity=Decimal("500"), unit_price=Decimal("1800000"),
            created_by=self.user, delivery_account=self.usdt_box, settle_account=self.rial_bank,
        )
        self.assertEqual(balances.party_balances_map(self.customer), {})

    def test_buy_cashbox_increases_not_decreases(self):
        before = balances.party_balances_map(self.usdt_box).get(self.usd.id, Decimal("0"))
        services.post_deal(
            side=Deal.Side.BUY, date=self.today, counterparty=self.customer,
            currency=self.usd, quantity=Decimal("500"), unit_price=Decimal("1800000"),
            created_by=self.user, delivery_account=self.usdt_box, settle_account=self.rial_bank,
        )
        after = balances.party_balances_map(self.usdt_box).get(self.usd.id, Decimal("0"))
        self.assertEqual(after - before, Decimal("500"))

    def test_sell_with_settle_account_credits_the_real_bank_correctly(self):
        """فروش با تسویه فوری: بانک باید بستانکار (پول زیاد) شود."""
        before = balances.party_balances_map(self.rial_bank).get(self.rial.id, Decimal("0"))
        services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.usd, quantity=Decimal("500"), unit_price=Decimal("1845000"),
            created_by=self.user, delivery_account=self.usdt_box, settle_account=self.rial_bank,
        )
        after = balances.party_balances_map(self.rial_bank).get(self.rial.id, Decimal("0"))
        self.assertEqual(after - before, Decimal("922500000"))

    def test_sell_with_both_accounts_leaves_no_residual_customer_balance(self):
        services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.usd, quantity=Decimal("500"), unit_price=Decimal("1845000"),
            created_by=self.user, delivery_account=self.usdt_box, settle_account=self.rial_bank,
        )
        self.assertEqual(balances.party_balances_map(self.customer), {})

    def test_without_settle_account_customer_still_gets_rial_balance(self):
        """بدون تسویه فوری، رفتار قدیمی دست نخورده می‌ماند."""
        voucher = services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.usd, quantity=Decimal("500"), unit_price=Decimal("1845000"),
            created_by=self.user, delivery_account=self.usdt_box,
        )
        balance = balances.party_balances_map(self.customer)
        self.assertEqual(balance[self.rial.id], Decimal("922500000"))
        self.assertEqual(voucher.entries.count(), 4)

    def test_delete_voucher_restores_both_accounts_exactly(self):
        before_bank = balances.party_balances_map(self.rial_bank).get(self.rial.id, Decimal("0"))
        before_box = balances.party_balances_map(self.usdt_box).get(self.usd.id, Decimal("0"))

        voucher = services.post_deal(
            side=Deal.Side.BUY, date=self.today, counterparty=self.customer,
            currency=self.usd, quantity=Decimal("500"), unit_price=Decimal("1800000"),
            created_by=self.user, delivery_account=self.usdt_box, settle_account=self.rial_bank,
        )
        services.delete_voucher(voucher=voucher, reason="آزمون", deleted_by=self.user)

        after_bank = balances.party_balances_map(self.rial_bank).get(self.rial.id, Decimal("0"))
        after_box = balances.party_balances_map(self.usdt_box).get(self.usd.id, Decimal("0"))
        self.assertEqual(after_bank, before_bank)
        self.assertEqual(after_box, before_box)

    def test_health_check_stays_zero_with_settle_account(self):
        """جمع کل هر ارز روی همه حساب‌ها همچنان باید صفر بماند."""
        services.post_deal(
            side=Deal.Side.BUY, date=self.today, counterparty=self.customer,
            currency=self.usd, quantity=Decimal("500"), unit_price=Decimal("1800000"),
            created_by=self.user, delivery_account=self.usdt_box, settle_account=self.rial_bank,
        )
        for row in balances.currency_totals():
            self.assertEqual(row["total"], Decimal("0"), f"{row['currency'].name} تراز نیست")


class ExpenseIncomeTests(LedgerTestBase):
    """«جایی برای ثبت سند هزینه و درآمد پیدا نکردم.»"""

    def setUp(self):
        self.rent = Party.objects.create(kind=Party.Kind.EXPENSE, name="اجاره دفتر")
        self.fee_income = Party.objects.create(kind=Party.Kind.INCOME, name="کارمزد")

    def test_expense_reduces_the_account(self):
        services.post_expense(
            date=self.today, category=self.rent, account=self.bank, currency=self.rial,
            amount=Decimal("50000000"), created_by=self.user,
        )
        self.assertEqual(
            balances.party_balances_map(self.bank)[self.rial.id], Decimal("-50000000")
        )

    def test_expense_category_balance_accumulates(self):
        for _ in range(3):
            services.post_expense(
                date=self.today, category=self.rent, account=self.bank, currency=self.rial,
                amount=Decimal("50000000"), created_by=self.user,
            )
        self.assertEqual(
            balances.party_balances_map(self.rent)[self.rial.id], Decimal("150000000")
        )

    def test_income_increases_the_account(self):
        services.post_income(
            date=self.today, category=self.fee_income, account=self.bank, currency=self.rial,
            amount=Decimal("2000000"), created_by=self.user,
        )
        self.assertEqual(
            balances.party_balances_map(self.bank)[self.rial.id], Decimal("2000000")
        )

    def test_voucher_kind_is_recorded(self):
        voucher = services.post_expense(
            date=self.today, category=self.rent, account=self.bank, currency=self.rial,
            amount=Decimal("1000000"), created_by=self.user,
        )
        self.assertEqual(voucher.kind, Voucher.Kind.EXPENSE)

    def test_zero_amount_rejected(self):
        with self.assertRaises(services.LedgerError):
            services.post_expense(
                date=self.today, category=self.rent, account=self.bank, currency=self.rial,
                amount=Decimal("0"), created_by=self.user,
            )

    def test_page_reachable_and_form_lists_categories(self):
        self.client.force_login(self.user)
        response = self.client.get("/expense-income/expense/new/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("اجاره دفتر", response.content.decode())

    def test_income_page_only_lists_income_categories(self):
        self.client.force_login(self.user)
        html = self.client.get("/expense-income/income/new/").content.decode()
        self.assertIn("کارمزد", html)
        self.assertNotIn("اجاره دفتر", html)


class NetWorthReportTests(LedgerTestBase):
    """«تراز دارایی وجود نداره.»"""

    def test_cash_only_net_worth(self):
        services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("100000000"),
            created_by=self.user,
        )
        snapshot = balances.net_worth_snapshot()
        # دریافت: بانک +۱۰۰م، مشتری −۱۰۰م (بستانکار) → جمع خالص = صفر
        self.assertEqual(snapshot["grand_total"], Decimal("0"))

    def test_unpaid_sale_increases_net_worth_via_receivable(self):
        """فروش بدون تسویه: هنوز پولی نگرفته‌ایم ولی طلب داریم — دارایی باید بالا برود."""
        services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.aed, quantity=Decimal("100"), unit_price=Decimal("500000"),
            created_by=self.user,
        )
        snapshot = balances.net_worth_snapshot()
        rial_row = next(r for r in snapshot["rows"] if r["currency"].code == "IRR")
        self.assertEqual(rial_row["customer"], Decimal("50000000"))
        self.assertEqual(snapshot["grand_total"], Decimal("50000000"))

    def test_expense_reduces_net_worth(self):
        rent = Party.objects.create(kind=Party.Kind.EXPENSE, name="اجاره آزمون")
        services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("100000000"),
            created_by=self.user,
        )
        services.post_expense(
            date=self.today, category=rent, account=self.bank, currency=self.rial,
            amount=Decimal("30000000"), created_by=self.user,
        )
        snapshot = balances.net_worth_snapshot()
        # بانک: ۱۰۰م − ۳۰م = ۷۰م، مشتری: −۱۰۰م → خالص = −۳۰م
        self.assertEqual(snapshot["grand_total"], Decimal("-30000000"))

    def test_page_reachable(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/reports/net-worth/").status_code, 200)

    def test_employee_without_trial_permission_is_blocked(self):
        from accounts.models import Permission, Role, User
        from accounts.permissions import DEFAULT_ROLES

        spec = DEFAULT_ROLES["employee"]
        role = Role.objects.create(code="employee", title=spec["title"])
        role.permissions.set(Permission.objects.filter(code__in=spec["permissions"]))
        employee = User.objects.create_user(username="clerk3", password="Test-1234!", role=role)
        self.client.force_login(employee)
        self.assertEqual(self.client.get("/reports/net-worth/").status_code, 403)
