"""آزمون‌های موتور حسابداری.

تمرکز این آزمون‌ها روی قواعدی است که پول را از گم شدن نجات می‌دهند:
تراز بودن سند، درست بودن مانده، اتمیک بودن ثبت، و ضد تکراری بودن.
"""
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Permission, Role, User
from accounts.permissions import DEFAULT_ROLES
from core.models import Currency, FxRate, Party
from core.money import parse_amount

from . import balances, services
from .models import Deal, Entry, InventoryPosition, Voucher


class LedgerTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Permission.sync_catalog()
        spec = DEFAULT_ROLES["admin"]
        cls.role = Role.objects.create(code="admin", title=spec["title"], is_system=True)
        cls.role.permissions.set(Permission.objects.filter(code__in=spec["permissions"]))

        cls.user = User.objects.create_user(username="tester", password="Test-1234!",
                                            full_name="کاربر آزمون", role=cls.role)

        cls.rial = Currency.objects.create(code="IRR", name="ریال", decimal_places=0,
                                           is_base=True, sort_order=0)
        cls.aed = Currency.objects.create(code="AED", name="درهم", decimal_places=2, sort_order=1)
        cls.usd = Currency.objects.create(code="USD", name="دلار", decimal_places=2, sort_order=2)

        cls.customer = Party.objects.create(kind=Party.Kind.CUSTOMER, name="علی محمدی")
        cls.supplier = Party.objects.create(kind=Party.Kind.CUSTOMER, name="رضا کریمی")
        cls.bank = Party.objects.create(kind=Party.Kind.BANK, name="بانک سامان",
                                        currency=cls.rial, code="SAMAN")
        cls.today = timezone.localdate()


class BalanceRuleTests(LedgerTestBase):
    """سند نامتراز نباید ثبت شود."""

    def test_unbalanced_voucher_is_rejected(self):
        lines = [
            services.EntryLine(self.customer, self.rial, Decimal("1000")),
            services.EntryLine(self.bank, self.rial, Decimal("-900")),  # ۱۰۰ کم است
        ]
        with self.assertRaises(services.LedgerError) as ctx:
            services.post_voucher(kind=Voucher.Kind.RECEIVE, date=self.today,
                                  lines=lines, created_by=self.user)
        self.assertIn("تراز", str(ctx.exception))
        self.assertEqual(Voucher.objects.count(), 0)
        self.assertEqual(Entry.objects.count(), 0)

    def test_balanced_voucher_is_posted(self):
        voucher = services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("50000000"),
            created_by=self.user,
        )
        self.assertEqual(voucher.status, Voucher.Status.FINAL)
        self.assertIsNotNone(voucher.number)
        self.assertEqual(voucher.entries.count(), 2)

    def test_failed_post_leaves_no_partial_rows(self):
        """اگر ثبت وسط کار شکست بخورد، هیچ سطری باقی نمی‌ماند."""
        try:
            services.post_voucher(
                kind=Voucher.Kind.DEAL, date=self.today, created_by=self.user,
                lines=[
                    services.EntryLine(self.customer, self.aed, Decimal("100")),
                    services.EntryLine(self.customer, self.rial, Decimal("500")),  # نامتراز
                ],
            )
        except services.LedgerError:
            pass
        self.assertEqual(Voucher.objects.count(), 0)
        self.assertEqual(Entry.objects.count(), 0)


class DealTests(LedgerTestBase):
    """معامله باید هر چهار سطرش را درست بسازد."""

    def test_sell_creates_four_correct_entries(self):
        voucher = services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.aed, quantity=Decimal("8300"), unit_price=Decimal("512000"),
            created_by=self.user,
        )
        self.assertEqual(voucher.entries.count(), 4)

        # مشتری بابت ریال بدهکار می‌شود
        rial_entry = voucher.entries.get(party=self.customer, currency=self.rial)
        self.assertEqual(rial_entry.amount, Decimal("4249600000"))

        # و بابت درهم بستانکار (ما به او درهم بدهکاریم تا حواله را بفرستیم)
        aed_entry = voucher.entries.get(party=self.customer, currency=self.aed)
        self.assertEqual(aed_entry.amount, Decimal("-8300"))

        deal = voucher.deal
        self.assertEqual(deal.total_base, Decimal("4249600000"))

    def test_total_is_computed_not_trusted(self):
        """مبلغ کل را سیستم ضرب می‌کند — همان چیزی که در شیت اشتباه می‌شد."""
        voucher = services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.aed, quantity=Decimal("10000"), unit_price=Decimal("518500"),
            created_by=self.user,
        )
        self.assertEqual(voucher.deal.total_base, Decimal("5185000000"))

    def test_zero_quantity_rejected(self):
        with self.assertRaises(services.LedgerError):
            services.post_deal(
                side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
                currency=self.aed, quantity=Decimal("0"), unit_price=Decimal("512000"),
                created_by=self.user,
            )

    def test_base_currency_cannot_be_traded(self):
        with self.assertRaises(services.LedgerError):
            services.post_deal(
                side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
                currency=self.rial, quantity=Decimal("10"), unit_price=Decimal("1"),
                created_by=self.user,
            )

    def test_inactive_party_rejected(self):
        self.customer.is_active = False
        self.customer.save(update_fields=["is_active"])
        with self.assertRaises(services.LedgerError) as ctx:
            services.post_deal(
                side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
                currency=self.aed, quantity=Decimal("100"), unit_price=Decimal("512000"),
                created_by=self.user,
            )
        self.assertIn("غیرفعال", str(ctx.exception))


class WeightedAverageTests(LedgerTestBase):
    """میانگین موزون و سود فروش."""

    def test_average_cost_after_two_buys(self):
        services.post_deal(side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
                           currency=self.aed, quantity=Decimal("100"),
                           unit_price=Decimal("500000"), created_by=self.user)
        services.post_deal(side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
                           currency=self.aed, quantity=Decimal("100"),
                           unit_price=Decimal("520000"), created_by=self.user)

        position = InventoryPosition.objects.get(currency=self.aed)
        self.assertEqual(position.quantity, Decimal("200"))
        self.assertEqual(position.avg_unit_cost, Decimal("510000"))

    def test_realized_profit_uses_average(self):
        services.post_deal(side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
                           currency=self.aed, quantity=Decimal("100"),
                           unit_price=Decimal("500000"), created_by=self.user)
        services.post_deal(side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
                           currency=self.aed, quantity=Decimal("100"),
                           unit_price=Decimal("520000"), created_by=self.user)
        voucher = services.post_deal(side=Deal.Side.SELL, date=self.today,
                                     counterparty=self.customer, currency=self.aed,
                                     quantity=Decimal("50"), unit_price=Decimal("530000"),
                                     created_by=self.user)
        # (۵۳۰٬۰۰۰ − ۵۱۰٬۰۰۰) × ۵۰ = ۱٬۰۰۰٬۰۰۰
        self.assertEqual(voucher.deal.realized_pnl, Decimal("1000000"))
        self.assertEqual(InventoryPosition.objects.get(currency=self.aed).quantity, Decimal("150"))


class VoidTests(LedgerTestBase):
    """ابطال فقط با سند برگشتی."""

    def test_void_creates_reversal_and_zeroes_balance(self):
        voucher = services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.aed, quantity=Decimal("100"), unit_price=Decimal("512000"),
            created_by=self.user,
        )
        before = balances.party_balances_map(self.customer)
        self.assertNotEqual(before.get(self.rial.id), Decimal("0"))

        reversal = services.void_voucher(voucher=voucher, reason="نرخ اشتباه بود",
                                         created_by=self.user)

        voucher.refresh_from_db()
        self.assertEqual(voucher.status, Voucher.Status.VOID)
        self.assertEqual(reversal.reverses_id, voucher.pk)

        # سند اصلی پاک نشده
        self.assertEqual(voucher.entries.count(), 4)

        # ولی اثرش خنثی شده
        after = balances.party_balances_map(self.customer)
        self.assertEqual(after.get(self.rial.id, Decimal("0")), Decimal("0"))
        self.assertEqual(after.get(self.aed.id, Decimal("0")), Decimal("0"))

    def test_void_restores_inventory(self):
        services.post_deal(side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
                           currency=self.aed, quantity=Decimal("100"),
                           unit_price=Decimal("500000"), created_by=self.user)
        second = services.post_deal(side=Deal.Side.BUY, date=self.today,
                                    counterparty=self.supplier, currency=self.aed,
                                    quantity=Decimal("100"), unit_price=Decimal("520000"),
                                    created_by=self.user)
        services.void_voucher(voucher=second, reason="اشتباه", created_by=self.user)

        position = InventoryPosition.objects.get(currency=self.aed)
        self.assertEqual(position.quantity, Decimal("100"))
        self.assertEqual(position.avg_unit_cost, Decimal("500000"))

    def test_cannot_void_twice(self):
        voucher = services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("1000"),
            created_by=self.user,
        )
        services.void_voucher(voucher=voucher, reason="اول", created_by=self.user)
        with self.assertRaises(services.LedgerError):
            services.void_voucher(voucher=voucher, reason="دوم", created_by=self.user)

    def test_void_requires_reason(self):
        voucher = services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("1000"),
            created_by=self.user,
        )
        with self.assertRaises(services.LedgerError):
            services.void_voucher(voucher=voucher, reason="   ", created_by=self.user)


class IdempotencyTests(LedgerTestBase):
    """کلید یکتا از ثبت دوباره جلوگیری می‌کند."""

    def test_duplicate_external_key_rejected(self):
        kwargs = dict(kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
                      account=self.bank, currency=self.rial, amount=Decimal("1000"),
                      created_by=self.user, external_key="bot-msg-42")
        services.post_cash_movement(**kwargs)
        with self.assertRaises(services.DuplicateVoucher):
            services.post_cash_movement(**kwargs)
        self.assertEqual(Voucher.objects.count(), 1)


class BalanceComputationTests(LedgerTestBase):
    """مانده همیشه از جمع سطرها حساب می‌شود، نه از عدد ذخیره‌شده."""

    def test_balance_matches_statement(self):
        services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("500000000"),
            created_by=self.user,
        )
        services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.aed, quantity=Decimal("100"), unit_price=Decimal("512000"),
            created_by=self.user,
        )

        balance = balances.party_balances_map(self.customer)
        statement = balances.account_statement(self.customer)
        closing = {c.id: amount for c, amount in statement["closing"].items()}
        self.assertEqual(balance, closing)

    def test_receive_updates_both_sides(self):
        """در شیت فقط حساب مشتری عوض می‌شد؛ اینجا موجودی بانک هم باید تغییر کند."""
        services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("50000000"),
            created_by=self.user,
        )
        customer_balance = balances.party_balances_map(self.customer)
        bank_balance = balances.party_balances_map(self.bank)

        self.assertEqual(customer_balance[self.rial.id], Decimal("-50000000"))
        self.assertEqual(bank_balance[self.rial.id], Decimal("50000000"))

    def test_system_wide_currency_totals_are_zero(self):
        """آزمون سلامت کل سیستم: جمع هر ارز روی همه حساب‌ها باید صفر باشد."""
        services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("50000000"),
            created_by=self.user,
        )
        services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.aed, quantity=Decimal("8300"), unit_price=Decimal("512000"),
            created_by=self.user,
        )
        services.post_deal(
            side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
            currency=self.usd, quantity=Decimal("7000"), unit_price=Decimal("920000"),
            created_by=self.user,
        )
        for row in balances.currency_totals():
            self.assertEqual(row["total"], Decimal("0"), f"ارز {row['currency'].name} تراز نیست")

    def test_running_balance_is_cumulative_per_currency(self):
        for amount in (Decimal("100"), Decimal("200"), Decimal("300")):
            services.post_cash_movement(
                kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
                account=self.bank, currency=self.rial, amount=amount, created_by=self.user,
            )
        statement = balances.account_statement(self.customer, currency=self.rial)
        runnings = [row["running"] for row in statement["rows"]]
        self.assertEqual(runnings, [Decimal("-100"), Decimal("-300"), Decimal("-600")])


class ValuationTests(LedgerTestBase):
    """ارزش‌گذاری ریالی با نرخ روز."""

    def test_missing_rate_is_reported_not_guessed(self):
        services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.aed, quantity=Decimal("100"), unit_price=Decimal("512000"),
            created_by=self.user,
        )
        view = balances.party_balance_view(self.customer)
        self.assertIn(self.aed, view["missing_rates"])

    def test_valuation_uses_latest_rate(self):
        FxRate.objects.create(currency=self.aed, rate_to_base=Decimal("500000"),
                              effective_at=timezone.now())
        FxRate.objects.create(currency=self.aed, rate_to_base=Decimal("520000"),
                              effective_at=timezone.now())
        services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.aed, quantity=Decimal("100"), unit_price=Decimal("512000"),
            created_by=self.user,
        )
        view = balances.party_balance_view(self.customer)
        self.assertEqual(view["missing_rates"], [])
        # ریال: +۵۱٬۲۰۰٬۰۰۰  |  درهم: −۱۰۰ × ۵۲۰٬۰۰۰ = −۵۲٬۰۰۰٬۰۰۰
        self.assertEqual(view["total_base"], Decimal("-800000"))


class AmountParsingTests(TestCase):
    """اعدادی که در فایل کارفرما با شکل‌های مختلف نوشته شده‌اند."""

    def test_parses_various_formats(self):
        self.assertEqual(parse_amount("424,960,000"), Decimal("424960000"))
        self.assertEqual(parse_amount("۴۲۴,۹۶۰,۰۰۰"), Decimal("424960000"))
        self.assertEqual(parse_amount("424.960.000"), Decimal("424960000"))
        self.assertEqual(parse_amount("50000000"), Decimal("50000000"))
        self.assertEqual(parse_amount("۸۳۰۰"), Decimal("8300"))
        self.assertEqual(parse_amount("-103260000"), Decimal("-103260000"))

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            parse_amount("سلام")


class PermissionTests(LedgerTestBase):
    """دسترسی باید سمت سرور بررسی شود، نه فقط با مخفی کردن منو."""

    def setUp(self):
        viewer_spec = DEFAULT_ROLES["viewer"]
        self.viewer_role = Role.objects.create(code="viewer", title=viewer_spec["title"])
        self.viewer_role.permissions.set(
            Permission.objects.filter(code__in=viewer_spec["permissions"])
        )
        self.viewer = User.objects.create_user(username="viewer", password="Test-1234!",
                                               role=self.viewer_role)

    def test_viewer_cannot_open_deal_form(self):
        self.client.force_login(self.viewer)
        response = self.client.get("/deal/new/")
        self.assertEqual(response.status_code, 403)

    def test_viewer_cannot_post_deal_directly(self):
        self.client.force_login(self.viewer)
        response = self.client.post("/deal/new/", {
            "date": "1405/05/17", "counterparty": self.customer.pk, "side": "sell",
            "currency": self.aed.pk, "quantity": "100", "unit_price": "512000",
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Voucher.objects.count(), 0)

    def test_viewer_cannot_see_profit_report(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get("/reports/profit/").status_code, 403)

    def test_admin_can_open_deal_form(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/deal/new/").status_code, 200)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get("/deal/new/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])


@override_settings(DISPLAY_UNIT="rial")
class AmountUnitRialTests(LedgerTestBase):
    """حالت پیش‌فرض برنامه: همه‌جا ریال.

    کارفرما خواسته بود واحد در تمام برنامه یکنواخت و ریال باشد. این آزمون‌ها
    تضمین می‌کنند عددی که کاربر وارد می‌کند، بدون هیچ تبدیلی همان عدد در
    صورتحساب دیده شود.
    """

    def test_opening_form_keeps_rial_as_is(self):
        from ledger.forms import OpeningBalanceForm

        form = OpeningBalanceForm(data={
            "date": "1405/05/20", "party": self.customer.pk, "currency": self.rial.pk,
            "direction": "credit", "amount": "50,000,000",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["amount"], Decimal("50000000"))
        self.assertEqual(form.signed_amount(), Decimal("-50000000"))

    def test_base_currency_shows_rial_in_dropdown(self):
        from ledger.forms import CashMovementForm

        form = CashMovementForm()
        labels = [label for _value, label in form.fields["currency"].choices]
        self.assertIn("ریال", labels)
        self.assertNotIn("تومان", labels)

    def test_entered_amount_appears_unchanged_in_statement(self):
        from core.money import to_display
        from ledger.forms import OpeningBalanceForm

        form = OpeningBalanceForm(data={
            "date": "1405/05/20", "party": self.customer.pk, "currency": self.rial.pk,
            "direction": "credit", "amount": "29,620,500",
        })
        self.assertTrue(form.is_valid(), form.errors)
        services.post_opening_balance(
            date=form.cleaned_data["date"], party=self.customer,
            currency=self.rial, amount=form.signed_amount(), created_by=self.user,
        )
        balance = balances.party_balances_map(self.customer)
        self.assertEqual(to_display(balance[self.rial.id]), Decimal("-29620500"))


@override_settings(DISPLAY_UNIT="toman")
class AmountUnitTomanTests(LedgerTestBase):
    """اگر روزی واحد نمایش را تومان کنیم، همه فرم‌ها باید یکسان تبدیل کنند.

    قبلاً فرم‌های دریافت/پرداخت/افتتاحیه تبدیل نمی‌کردند و مانده ده برابر
    کمتر ثبت می‌شد؛ این آزمون‌ها جلوی برگشت آن ایراد را می‌گیرند.
    """

    def test_opening_form_converts_toman_to_rial(self):
        from ledger.forms import OpeningBalanceForm

        form = OpeningBalanceForm(data={
            "date": "1405/05/20", "party": self.customer.pk, "currency": self.rial.pk,
            "direction": "credit", "amount": "50,000,000",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["amount"], Decimal("500000000"))
        self.assertEqual(form.signed_amount(), Decimal("-500000000"))

    def test_cash_form_converts_toman_to_rial(self):
        from ledger.forms import CashMovementForm

        form = CashMovementForm(data={
            "date": "1405/05/20", "party": self.customer.pk, "currency": self.rial.pk,
            "account": self.bank.pk, "amount": "50,000,000",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["amount"], Decimal("500000000"))

    def test_foreign_currency_amount_is_not_converted(self):
        """۱۰۰ درهم باید ۱۰۰ بماند، نه ۱۰۰۰."""
        from ledger.forms import CashMovementForm

        aed_bank = Party.objects.create(kind=Party.Kind.BANK, name="صندوق درهم",
                                        currency=self.aed, code="CASH-AED")
        form = CashMovementForm(data={
            "date": "1405/05/20", "party": self.customer.pk, "currency": self.aed.pk,
            "account": aed_bank.pk, "amount": "100",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["amount"], Decimal("100"))

    def test_deal_unit_price_converts_toman_to_rial(self):
        from ledger.forms import DealForm

        form = DealForm(data={
            "date": "1405/05/20", "counterparty": self.customer.pk, "side": "sell",
            "currency": self.aed.pk, "quantity": "100", "unit_price": "51,200",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["unit_price"], Decimal("512000"))
        self.assertEqual(form.cleaned_data["quantity"], Decimal("100"))

    def test_base_currency_shows_toman_in_dropdown(self):
        """در حالت تومان، فهرست ارزها باید «تومان» بنویسد، نه «ریال»."""
        from ledger.forms import CashMovementForm

        form = CashMovementForm()
        labels = [label for _value, label in form.fields["currency"].choices]
        self.assertIn("تومان", labels)
        self.assertNotIn("ریال", labels)

    def test_end_to_end_opening_balance_shows_entered_amount(self):
        """عددی که کاربر وارد می‌کند باید همان عدد در صورتحساب دیده شود."""
        from core.money import to_display
        from ledger.forms import OpeningBalanceForm

        form = OpeningBalanceForm(data={
            "date": "1405/05/20", "party": self.customer.pk, "currency": self.rial.pk,
            "direction": "credit", "amount": "50,000,000",
        })
        self.assertTrue(form.is_valid(), form.errors)

        services.post_opening_balance(
            date=form.cleaned_data["date"], party=self.customer,
            currency=self.rial, amount=form.signed_amount(), created_by=self.user,
        )
        balance = balances.party_balances_map(self.customer)
        self.assertEqual(to_display(balance[self.rial.id]), Decimal("-50000000"))
