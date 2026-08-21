"""آزمون‌های مربوط به بازخوردهای کارفرما در ۲۵ مرداد.

هر کلاس اینجا مستقیماً به یکی از ایرادهایی که کارفرما گزارش کرده پاسخ می‌دهد،
تا اگر روزی کسی کد را عوض کرد، همان ایراد دوباره برنگردد.
"""
from decimal import Decimal

from core.models import Currency, Party

from . import balances, services
from .models import Deal, Voucher
from .tests import LedgerTestBase


class ImmediateDeliveryTests(LedgerTestBase):
    """«فروش ۵۹۸ تتر ثبت کردم ولی موجودی صندوق ارزی جمع شد به جای اینکه کسر شود.»

    علتش این بود که سیستم همیشه فرض می‌کرد ارز بعداً تحویل می‌شود. حالا اگر
    صندوق تحویل انتخاب شود، موجودی همان لحظه کم یا زیاد می‌شود.
    """

    def setUp(self):
        self.usdt_box = Party.objects.create(
            kind=Party.Kind.CASHBOX, name="صندوق تتر",
            currency=self.usd, code="CASH-USD-TEST",
        )

    def test_sell_with_delivery_account_reduces_the_cashbox(self):
        services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.usd, quantity=Decimal("598"), unit_price=Decimal("1845000"),
            created_by=self.user, delivery_account=self.usdt_box,
        )
        box = balances.party_balances_map(self.usdt_box)
        self.assertEqual(box[self.usd.id], Decimal("-598"))

    def test_sell_with_delivery_leaves_customer_currency_untouched(self):
        """مشتری ارز را گرفته، پس نباید بستانکار ارزی بماند — فقط بدهکار ریال."""
        services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.usd, quantity=Decimal("598"), unit_price=Decimal("1845000"),
            created_by=self.user, delivery_account=self.usdt_box,
        )
        balance = balances.party_balances_map(self.customer)
        self.assertNotIn(self.usd.id, balance)
        self.assertEqual(balance[self.rial.id], Decimal("1103310000"))

    def test_buy_with_delivery_account_increases_the_cashbox(self):
        services.post_deal(
            side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
            currency=self.usd, quantity=Decimal("100"), unit_price=Decimal("900000"),
            created_by=self.user, delivery_account=self.usdt_box,
        )
        box = balances.party_balances_map(self.usdt_box)
        self.assertEqual(box[self.usd.id], Decimal("100"))

    def test_without_delivery_account_currency_stays_on_the_party(self):
        """حالت حواله: ارز بعداً تحویل می‌شود، پس روی حساب طرف می‌ماند."""
        services.post_deal(
            side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
            currency=self.aed, quantity=Decimal("8300"), unit_price=Decimal("512000"),
            created_by=self.user,
        )
        balance = balances.party_balances_map(self.supplier)
        self.assertEqual(balance[self.aed.id], Decimal("8300"))

    def test_delivery_account_currency_must_match(self):
        with self.assertRaises(services.LedgerError):
            services.post_deal(
                side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
                currency=self.aed, quantity=Decimal("10"), unit_price=Decimal("500000"),
                created_by=self.user, delivery_account=self.usdt_box,
            )

    def test_position_account_is_hidden_from_house_summary(self):
        """حساب واسط نباید کنار صندوق‌های واقعی دیده شود — همان چیزی که گیج‌کننده بود."""
        services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.usd, quantity=Decimal("598"), unit_price=Decimal("1845000"),
            created_by=self.user, delivery_account=self.usdt_box,
        )
        kinds = {row["party"].kind for row in balances.house_accounts_summary()}
        self.assertNotIn(Party.Kind.POSITION, kinds)


class TotalDrivenRateTests(LedgerTestBase):
    """«بعضی مواقع مبلغ کل وارد کنم، خودش فی رو محاسبه کنه.»"""

    def test_total_amount_computes_unit_price(self):
        from .forms import DealForm

        form = DealForm(data={
            "date": "1405/05/24", "counterparty": self.customer.pk, "side": "sell",
            "currency": self.aed.pk, "quantity": "100", "total_amount": "51,000,000",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["unit_price"], Decimal("510000"))

    def test_rounded_total_wins_over_typed_rate(self):
        """اگر هر دو پر شد، مبلغ کلِ رندشده حرف آخر را می‌زند."""
        from .forms import DealForm

        form = DealForm(data={
            "date": "1405/05/24", "counterparty": self.customer.pk, "side": "sell",
            "currency": self.aed.pk, "quantity": "100",
            "unit_price": "512345", "total_amount": "51,000,000",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["unit_price"], Decimal("510000"))

    def test_rate_alone_still_works(self):
        from .forms import DealForm

        form = DealForm(data={
            "date": "1405/05/24", "counterparty": self.customer.pk, "side": "sell",
            "currency": self.aed.pk, "quantity": "100", "unit_price": "512000",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["unit_price"], Decimal("512000"))

    def test_neither_rate_nor_total_is_rejected(self):
        from .forms import DealForm

        form = DealForm(data={
            "date": "1405/05/24", "counterparty": self.customer.pk, "side": "sell",
            "currency": self.aed.pk, "quantity": "100",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("unit_price", form.errors)


class PartyToPartyTransferTests(LedgerTestBase):
    """«یه مشتری فیش رو به حساب یه مشتری دیگه واریز می‌کنه.»"""

    def test_transfer_moves_balance_between_two_customers(self):
        services.post_party_transfer(
            date=self.today, from_party=self.customer, to_party=self.supplier,
            currency=self.rial, amount=Decimal("50000000"), created_by=self.user,
        )
        payer = balances.party_balances_map(self.customer)
        receiver = balances.party_balances_map(self.supplier)

        self.assertEqual(payer[self.rial.id], Decimal("-50000000"))
        self.assertEqual(receiver[self.rial.id], Decimal("50000000"))

    def test_no_bank_or_cash_account_is_touched(self):
        services.post_party_transfer(
            date=self.today, from_party=self.customer, to_party=self.supplier,
            currency=self.rial, amount=Decimal("50000000"), created_by=self.user,
        )
        self.assertEqual(balances.party_balances_map(self.bank), {})

    def test_works_for_foreign_currency_too(self):
        services.post_party_transfer(
            date=self.today, from_party=self.customer, to_party=self.supplier,
            currency=self.aed, amount=Decimal("1000"), created_by=self.user,
        )
        self.assertEqual(
            balances.party_balances_map(self.supplier)[self.aed.id], Decimal("1000")
        )

    def test_same_party_is_rejected(self):
        with self.assertRaises(services.LedgerError):
            services.post_party_transfer(
                date=self.today, from_party=self.customer, to_party=self.customer,
                currency=self.rial, amount=Decimal("1000"), created_by=self.user,
            )

    def test_page_is_reachable(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/party-transfer/new/").status_code, 200)


class CurrencyCashboxTests(LedgerTestBase):
    """«تو فرم دریافت فقط بانک و صندوق ریالی هست، صندوق ارزی نیست.»"""

    def test_setup_creates_a_cashbox_for_every_currency(self):
        import io

        from django.core.management import call_command

        call_command("setup_sarrafi", skip_admin=True, stdout=io.StringIO())
        for currency in Currency.objects.filter(is_active=True):
            self.assertTrue(
                Party.objects.filter(kind=Party.Kind.CASHBOX, currency=currency).exists(),
                f"صندوق {currency.name} ساخته نشد",
            )

    def test_receive_form_offers_currency_cashboxes(self):
        import io

        from django.core.management import call_command

        from .forms import CashMovementForm

        call_command("setup_sarrafi", skip_admin=True, stdout=io.StringIO())
        accounts = CashMovementForm().fields["account"].queryset
        currencies = {a.currency.code for a in accounts if a.currency_id}
        self.assertIn("AED", currencies)
        self.assertIn("IRR", currencies)


class DescriptionSuggestionTests(LedgerTestBase):
    """«نمونه شرح‌ها ذخیره بشه که هر بار تایپ نکنم.»"""

    def test_previous_descriptions_are_offered(self):
        from .views import recent_descriptions

        services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.aed, quantity=Decimal("100"), unit_price=Decimal("512000"),
            created_by=self.user, description="فروش درهم به مشتری — معامله شماره ۱",
        )
        suggestions = recent_descriptions(Voucher.Kind.DEAL)
        self.assertIn("فروش درهم به مشتری — معامله شماره ۱", suggestions)

    def test_duplicates_are_collapsed(self):
        from .views import recent_descriptions

        for _ in range(3):
            services.post_deal(
                side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
                currency=self.aed, quantity=Decimal("10"), unit_price=Decimal("512000"),
                created_by=self.user, description="شرح تکراری",
            )
        suggestions = recent_descriptions(Voucher.Kind.DEAL)
        self.assertEqual(suggestions.count("شرح تکراری"), 1)

    def test_suggestions_appear_on_the_deal_page(self):
        services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.aed, quantity=Decimal("100"), unit_price=Decimal("512000"),
            created_by=self.user, description="شرح نمونه برای پیشنهاد",
        )
        self.client.force_login(self.user)
        html = self.client.get("/deal/new/").content.decode()
        self.assertIn("شرح نمونه برای پیشنهاد", html)
