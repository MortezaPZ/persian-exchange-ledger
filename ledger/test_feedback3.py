"""آزمون‌های بازخورد دور سوم کارفرما.

  ۱) در انتقال بین مشتریان، شرح مثل خرید/فروش در حافظه می‌ماند
  ۲) در هزینه و درآمد، مشتری هم کنار بانک و صندوق قابل انتخاب است
  ۳) صورتحساب و دفتر اسناد، سند و تاریخ جدید را بالا نشان می‌دهند
"""
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, unquote, urlparse

from core.jalali import format_jalali
from core.models import Party

from . import balances, services
from .forms import ExpenseIncomeForm
from .models import Voucher
from .tests import LedgerTestBase
from .views import recent_descriptions


class PartyTransferDescriptionMemoryTests(LedgerTestBase):
    """«در قسمت انتقال بین حساب مشتریان شرح ثبت نمی‌شود و تو حافظه نمی‌ماند.»"""

    def test_previous_descriptions_are_offered(self):
        services.post_party_transfer(
            date=self.today, from_party=self.customer, to_party=self.supplier,
            currency=self.rial, amount=Decimal("1000000"), created_by=self.user,
            description="واریز فیش مشتری الف به حساب ب",
        )
        suggestions = recent_descriptions(Voucher.Kind.TRANSFER)
        self.assertIn("واریز فیش مشتری الف به حساب ب", suggestions)

    def test_suggestions_appear_on_the_page(self):
        services.post_party_transfer(
            date=self.today, from_party=self.customer, to_party=self.supplier,
            currency=self.rial, amount=Decimal("1000000"), created_by=self.user,
            description="شرح نمونه انتقال مشتریان",
        )
        self.client.force_login(self.user)
        html = self.client.get("/party-transfer/new/").content.decode()
        self.assertIn("شرح نمونه انتقال مشتریان", html)
        self.assertIn("شرح‌های قبلی", html)

    def test_description_stays_in_the_form_after_submit(self):
        self.client.force_login(self.user)
        response = self.client.post("/party-transfer/new/", {
            "date": format_jalali(self.today),
            "from_party": self.customer.pk,
            "to_party": self.supplier.pk,
            "currency": self.rial.pk,
            "amount": "2,000,000",
            "description": "حواله مستقیم مشتری",
        })
        self.assertEqual(response.status_code, 302)
        params = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(unquote(params["description"][0]), "حواله مستقیم مشتری")

        follow = self.client.get(response["Location"])
        self.assertContains(follow, "حواله مستقیم مشتری")


class ExpenseIncomeCustomerAccountTests(LedgerTestBase):
    """«در اسناد هزینه و درآمد هم مشتریان و هم بانک و هم صندوق باشد.»"""

    def setUp(self):
        self.rent = Party.objects.create(kind=Party.Kind.EXPENSE, name="اجاره دفتر")
        self.fee_income = Party.objects.create(kind=Party.Kind.INCOME, name="کارمزد")

    def test_form_lists_customers_alongside_bank_and_cashbox(self):
        accounts = ExpenseIncomeForm(kind=Voucher.Kind.EXPENSE).fields["account"].queryset
        kinds = set(accounts.values_list("kind", flat=True))
        self.assertIn(Party.Kind.CUSTOMER, kinds)
        self.assertIn(Party.Kind.BANK, kinds)
        names = set(accounts.values_list("name", flat=True))
        self.assertIn(self.customer.name, names)
        self.assertIn(self.bank.name, names)

    def test_expense_paid_by_customer_credits_the_customer(self):
        services.post_expense(
            date=self.today, category=self.rent, account=self.customer,
            currency=self.rial, amount=Decimal("50000000"), created_by=self.user,
        )
        self.assertEqual(
            balances.party_balances_map(self.customer)[self.rial.id],
            Decimal("-50000000"),
        )
        self.assertEqual(
            balances.party_balances_map(self.rent)[self.rial.id],
            Decimal("50000000"),
        )

    def test_income_received_on_customer_account(self):
        services.post_income(
            date=self.today, category=self.fee_income, account=self.customer,
            currency=self.rial, amount=Decimal("2000000"), created_by=self.user,
        )
        self.assertEqual(
            balances.party_balances_map(self.customer)[self.rial.id],
            Decimal("2000000"),
        )

    def test_expense_page_mentions_customer_option(self):
        self.client.force_login(self.user)
        html = self.client.get("/expense-income/expense/new/").content.decode()
        self.assertIn(self.customer.name, html)
        self.assertIn("مشتری", html)


class NewestFirstListingTests(LedgerTestBase):
    """«در صورتحساب و اسناد، سند و تاریخ جدید بالا باشد.»"""

    def test_statement_puts_newest_voucher_first(self):
        older = self.today - timedelta(days=2)
        first = services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=older, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("100"),
            created_by=self.user,
        )
        second = services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("200"),
            created_by=self.user,
        )
        rows = balances.account_statement(
            self.customer, currency=self.rial, newest_first=True,
        )["rows"]
        numbers = [row["entry"].voucher.number for row in rows]
        self.assertEqual(numbers[0], second.number)
        self.assertEqual(numbers[-1], first.number)
        self.assertEqual(rows[0]["running"], Decimal("-300"))

    def test_chronological_statement_is_unchanged_by_default(self):
        """محاسبه مانده از قدیم به جدید دست‌نخورده می‌ماند (برای ربات و آزمون سلامت)."""
        for amount in (Decimal("100"), Decimal("200"), Decimal("300")):
            services.post_cash_movement(
                kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
                account=self.bank, currency=self.rial, amount=amount, created_by=self.user,
            )
        runnings = [
            row["running"]
            for row in balances.account_statement(self.customer, currency=self.rial)["rows"]
        ]
        self.assertEqual(runnings, [Decimal("-100"), Decimal("-300"), Decimal("-600")])

    def test_voucher_list_puts_newest_date_on_top(self):
        older = self.today - timedelta(days=3)
        old_v = services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=older, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("100"),
            created_by=self.user, description="سند قدیمی",
        )
        new_v = services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("200"),
            created_by=self.user, description="سند جدید",
        )
        self.client.force_login(self.user)
        html = self.client.get("/vouchers/").content.decode()
        self.assertLess(html.find("سند جدید"), html.find("سند قدیمی"))
        self.assertTrue(new_v.number > old_v.number)
