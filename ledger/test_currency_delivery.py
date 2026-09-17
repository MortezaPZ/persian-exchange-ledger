"""Currency delivery is independent of the invoice's base financial settlement."""
from decimal import Decimal

from django.utils import timezone

from accounts.models import Permission, Role, User
from accounts.permissions import DEFAULT_ROLES
from core.models import Currency, Party
from core.jalali import persian_digits
from core.money import format_amount
from ledger import services
from ledger.forms import DeliveryForm
from ledger.models import Deal, InvoiceDelivery, InvoiceSettlement, Voucher
from ledger.test_settlement import SettlementTestBase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class DeliveryMigrationTests(TransactionTestCase):
    def setUp(self):
        SettlementTestBase.setUpTestData.__func__(type(self))

    def test_additive_migration_preserves_historical_base_allocations(self):
        _, deal = SettlementTestBase.make_deal(self)
        payment = SettlementTestBase.make_payment(self)
        settlement = InvoiceSettlement.objects.create(
            deal=deal, payment=payment, amount=Decimal("123456.12345678"), created_by=self.user,
        )
        previous = [("ledger", "0003_invoice_settlement_attachment")]
        current = [("ledger", "0004_invoice_delivery")]
        try:
            executor = MigrationExecutor(connection)
            executor.migrate(previous)
            old_apps = executor.loader.project_state(previous).apps
            historical = old_apps.get_model("ledger", "InvoiceSettlement").objects.get(pk=settlement.pk)
            self.assertEqual(historical.amount, Decimal("123456.12345678"))
        finally:
            MigrationExecutor(connection).migrate(current)
        settlement.refresh_from_db()
        self.assertEqual(settlement.amount, Decimal("123456.12345678"))
        self.assertEqual(InvoiceDelivery.objects.count(), 0)
        self.assertEqual(deal.settled_amount, settlement.amount)
        self.assertEqual(deal.delivered_quantity, Decimal("0"))


class CurrencyDeliveryTests(SettlementTestBase):
    def make_delivery(self, currency=None, amount="60", party=None, other=None):
        from core.models import Party
        other = other or Party.objects.create(kind=Party.Kind.CUSTOMER, name="فرستنده")
        return services.post_party_transfer(
            date=self.today, from_party=other, to_party=party or self.customer,
            currency=currency or self.aed, amount=Decimal(amount), created_by=self.user,
        )

    def allocate(self, deal, payment, quantity="40", **kwargs):
        return self.client.post(f"/deal/{deal.pk}/settlement/", {
            "allocation_kind": "delivery", "payment": payment.pk,
            "quantity": quantity, "note": "تحویل حواله",
        }, **kwargs)

    def test_upfront_delivery_cannot_be_allocated_twice(self):
        from core.models import Party
        from ledger.forms import DeliveryForm
        account = Party.objects.create(kind=Party.Kind.CASHBOX, name="صندوق درهم", currency=self.aed)
        for side in (Deal.Side.SELL, Deal.Side.BUY):
            with self.subTest(side=side):
                voucher = services.post_deal(
                    side=side, date=self.today, counterparty=self.customer,
                    currency=self.aed, quantity=Decimal("100"), unit_price=Decimal("50000"),
                    delivery_account=account, created_by=self.user,
                )
                payment = services.post_cash_movement(
                    kind=Voucher.Kind.PAY if side == Deal.Side.SELL else Voucher.Kind.RECEIVE,
                    date=self.today, party=self.customer, account=account,
                    currency=self.aed, amount=Decimal("20"), created_by=self.user,
                )
                form = DeliveryForm({"payment": payment.pk, "quantity": "1"}, deal=voucher.deal)
                self.assertFalse(form.is_valid(), "Upfront delivered invoice accepted another delivery")
                self.assertIn("تحویل", str(form.non_field_errors()))
                self.assertEqual(voucher.deal.delivered_quantity, Decimal("100"))
                self.assertEqual(voucher.deal.remaining_delivery_quantity, Decimal("0"))

    def test_delivery_allocations_share_capacity_across_invoices(self):
        _, first = self.make_deal()
        _, second = self.make_deal()
        payment = self.make_delivery(amount="100")
        self.client.force_login(self.user)
        self.allocate(first, payment, quantity="60", follow=True)
        second_response = self.allocate(second, payment, quantity="41", follow=True)
        self.assertContains(second_response, "مانده قابل تخصیص")
        self.assertEqual(second.delivered_quantity, Decimal("0"))
        self.assertEqual(self.allocate(second, payment, quantity="40").status_code, 302)
        self.assertEqual(second.delivered_quantity, Decimal("40"))

    def test_delivery_capacity_is_independent_per_counterparty_and_currency(self):
        from core.models import Currency
        from ledger.forms import DeliveryForm
        other = Party.objects.create(kind=Party.Kind.CUSTOMER, name="طرف دوم")
        usd = Currency.objects.create(code="USD", name="دلار", decimal_places=2, sort_order=2)
        _, sell = self.make_deal()
        _, buy = self.make_deal(side=Deal.Side.BUY, party=other)
        incoming = self.make_delivery(amount="100")
        InvoiceDelivery.objects.create(deal=sell, payment=incoming,
                                       quantity=Decimal("100"), created_by=self.user)
        outgoing = services.post_party_transfer(
            date=self.today, from_party=other, to_party=Party.objects.create(
                kind=Party.Kind.CUSTOMER, name="گیرنده"),
            currency=self.aed, amount=Decimal("80"), created_by=self.user,
        )
        form = DeliveryForm({"payment": outgoing.pk, "quantity": "80"}, deal=buy)
        self.assertTrue(form.is_valid(), form.errors)
        usd_deal = services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=usd, quantity=Decimal("10"), unit_price=Decimal("90000"),
            created_by=self.user,
        )
        form = DeliveryForm({"payment": incoming.pk, "quantity": "1"}, deal=usd_deal.deal)
        self.assertFalse(form.is_valid())
        self.assertIn("payment", form.errors)

    def test_final_status_only_and_duplicates_readable(self):
        voucher, deal = self.make_deal()
        payment = self.make_delivery()
        self.client.force_login(self.user)
        for status in (Voucher.Status.DRAFT, Voucher.Status.VOID):
            with self.subTest(status=status):
                Voucher.objects.filter(pk=voucher.pk).update(status=status)
                response = self.allocate(deal, payment, follow=True)
                self.assertContains(response, "قطعی")
        Voucher.objects.filter(pk=voucher.pk).update(status=Voucher.Status.FINAL)
        self.allocate(deal, payment)
        second = self.allocate(deal, payment, follow=True)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(deal.deliveries.count(), 1)
        self.assertContains(second, "قبلا")

    def test_over_delivery_rejected_with_readable_error(self):
        _, deal = self.make_deal()
        payment = self.make_delivery(amount="50")
        self.client.force_login(self.user)
        response = self.allocate(deal, payment, quantity="51", follow=True)
        self.assertEqual(InvoiceDelivery.objects.count(), 0)
        self.assertContains(response, "بیشتر")

    def test_delivered_and_remaining_render_on_voucher_detail(self):
        voucher, deal = self.make_deal()
        payment = self.make_delivery()
        self.client.force_login(self.user)
        self.allocate(deal, payment, quantity="25")
        response = self.client.get(f"/vouchers/{voucher.pk}/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("تحویل", content)
        self.assertContains(response, persian_digits(format_amount(Decimal("25"), 2)))
        self.assertContains(response, persian_digits(format_amount(Decimal("75"), 2)))
        self.assertEqual(response.context["deal"].remaining_amount, Decimal("5000000"))
        self.assertContains(response, 'name="allocation_kind"')
        self.assertContains(response, 'value="delivery"')
        self.assertContains(response, 'id="delivery_payment"')
        self.assertContains(response, 'href="/vouchers/%s/"' % payment.pk)

    def test_multi_currency_payment_has_separate_capacity_for_each_currency(self):
        usd = Currency.objects.create(code="USD", name="دلار", decimal_places=2)
        other = Party.objects.create(kind=Party.Kind.CUSTOMER, name="طرف دوم")
        _, aed_deal = self.make_deal()
        usd_deal = services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=usd, quantity=Decimal("100"), unit_price=Decimal("90000"),
            created_by=self.user,
        ).deal
        payment = services.post_voucher(
            kind=Voucher.Kind.TRANSFER, date=self.today, created_by=self.user,
            lines=[services.EntryLine(self.customer, currency, Decimal("60"))
                   for currency in (self.aed, usd)] +
                  [services.EntryLine(other, currency, Decimal("-60"))
                   for currency in (self.aed, usd)],
        )
        self.client.force_login(self.user)
        self.allocate(aed_deal, payment, "60")
        self.allocate(usd_deal, payment, "60")
        self.assertEqual(usd_deal.delivered_quantity, Decimal("60"))

    def test_all_supported_transfer_currencies_and_both_sides(self):
        currencies = [self.aed] + [Currency.objects.create(code=code, name=code, decimal_places=8)
                                    for code in ("USD", "USDT")]
        other = Party.objects.create(kind=Party.Kind.CUSTOMER, name="طرف دوم")
        self.client.force_login(self.user)
        for currency in currencies:
            for side in (Deal.Side.SELL, Deal.Side.BUY):
                with self.subTest(currency=currency.code, side=side):
                    deal = services.post_deal(
                        side=side, date=self.today, counterparty=self.customer,
                        currency=currency, quantity=Decimal("100"), unit_price=Decimal("50000"),
                        created_by=self.user,
                    ).deal
                    payment = services.post_party_transfer(
                        date=self.today, currency=currency, amount=Decimal("10.12345678"),
                        from_party=other if side == Deal.Side.SELL else self.customer,
                        to_party=self.customer if side == Deal.Side.SELL else other,
                        created_by=self.user,
                    )
                    self.allocate(deal, payment, "10.12345678")
                    self.assertEqual(deal.delivered_quantity, Decimal("10.12345678"))
                    base_payment = services.post_party_transfer(
                        date=self.today, currency=self.rial, amount=Decimal("1000"),
                        from_party=self.customer if side == Deal.Side.SELL else other,
                        to_party=other if side == Deal.Side.SELL else self.customer,
                        created_by=self.user,
                    )
                    self.client.post(f"/deal/{deal.pk}/settlement/",
                                     {"payment": base_payment.pk, "amount": "1000"})
                    self.assertEqual(deal.settled_amount, Decimal("1000"))
                    self.assertEqual(deal.remaining_amount, Decimal("4999000"))

    def test_wrong_direction_currency_party_kind_and_reversal_rejected(self):
        _, deal = self.make_deal()
        account = Party.objects.create(kind=Party.Kind.BANK, name="بانک درهم", currency=self.aed)
        other = Party.objects.create(kind=Party.Kind.CUSTOMER, name="دیگری")
        wrong = [self.make_delivery(currency=self.rial), self.make_delivery(party=other)]
        wrong.append(services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer, account=account,
            currency=self.aed, amount=Decimal("50"), created_by=self.user))
        wrong.append(services.post_opening_balance(
            date=self.today, party=self.customer, currency=self.aed,
            amount=Decimal("50"), created_by=self.user))
        original = self.make_delivery()
        wrong.append(services.void_voucher(voucher=original, reason="آزمون", created_by=self.user))
        wrong.append(original)
        for payment in wrong:
            with self.subTest(payment=payment.pk):
                form = DeliveryForm({"payment": payment.pk, "quantity": "1"}, deal=deal)
                self.assertFalse(form.is_valid())
                self.assertIn("payment", form.errors)
        valid = services.post_cash_movement(
            kind=Voucher.Kind.PAY, date=self.today, party=self.customer, account=account,
            currency=self.aed, amount=Decimal("50"), created_by=self.user)
        self.assertTrue(DeliveryForm({"payment": valid.pk, "quantity": "1"}, deal=deal).is_valid())

    def test_invalid_decimals_and_invoice_limit_rejected(self):
        _, deal = self.make_deal()
        payment = self.make_delivery(amount="1000")
        for value in ("0", "-1", "NaN", "Infinity", "1.000000001", "1e30", "101", "abc"):
            with self.subTest(value=value):
                form = DeliveryForm({"payment": payment.pk, "quantity": value}, deal=deal)
                self.assertFalse(form.is_valid())
                self.assertIn("quantity", form.errors)

    def test_nonfinal_allocations_release_capacity_and_void_payment_reopens_delivery(self):
        voucher, first = self.make_deal()
        _, second = self.make_deal()
        payment = self.make_delivery(amount="100")
        self.client.force_login(self.user)
        self.allocate(first, payment, "100")
        services.void_voucher(voucher=voucher, reason="آزمون", created_by=self.user)
        self.allocate(second, payment, "100")
        self.assertEqual(second.delivered_quantity, Decimal("100"))
        services.void_voucher(voucher=payment, reason="آزمون", created_by=self.user)
        self.assertEqual(second.delivered_quantity, Decimal("0"))
        self.assertEqual(second.remaining_delivery_quantity, Decimal("100"))

    def test_same_transfer_supports_both_customer_sides(self):
        other = Party.objects.create(kind=Party.Kind.CUSTOMER, name="طرف دوم")
        _, sell = self.make_deal()
        _, buy = self.make_deal(side=Deal.Side.BUY, party=other)
        payment = self.make_delivery(other=other, amount="100")
        self.client.force_login(self.user)
        self.allocate(sell, payment, "100")
        self.allocate(buy, payment, "100")
        self.assertEqual(sell.delivered_quantity, Decimal("100"))
        self.assertEqual(buy.delivered_quantity, Decimal("100"))

    def test_payment_finality_rechecked_after_queryset_evaluation(self):
        _, deal = self.make_deal()
        payment = self.make_delivery()
        for status in (Voucher.Status.DRAFT, Voucher.Status.VOID):
            Voucher.objects.filter(pk=payment.pk).update(status=Voucher.Status.FINAL)
            form = DeliveryForm({"payment": payment.pk, "quantity": "1"}, deal=deal)
            list(form.fields["payment"].queryset)
            Voucher.objects.filter(pk=payment.pk).update(status=status)
            self.assertFalse(form.is_valid())

    def test_permission_required_and_get_does_not_allocate(self):
        from accounts.models import User
        _, deal = self.make_deal()
        payment = self.make_delivery()
        self.assertEqual(self.allocate(deal, payment).status_code, 302)
        viewer = User.objects.create_user(username="delivery-viewer", password="test")
        self.client.force_login(viewer)
        self.assertEqual(self.allocate(deal, payment).status_code, 403)
        self.client.force_login(self.user)
        self.client.get(f"/deal/{deal.pk}/settlement/", {"payment": payment.pk, "quantity": "1"})
        self.assertEqual(deal.deliveries.count(), 0)

    def test_sorted_voucher_locks_before_delivery_validation_and_save(self):
        from unittest.mock import patch
        from django.db import connection
        from django.db.models.query import QuerySet
        payment = self.make_delivery()
        voucher, deal = self.make_deal()
        locked = []
        original_fetch = QuerySet._fetch_all
        original_clean, original_save = DeliveryForm.clean, InvoiceDelivery.save
        depth = len(connection.atomic_blocks)

        def fetch(queryset):
            original_fetch(queryset)
            if queryset.model is Voucher and queryset.query.select_for_update:
                ids = [row.pk for row in queryset._result_cache]
                self.assertEqual(ids, sorted(ids))
                locked.extend(ids)

        def clean(form):
            self.assertEqual(set(locked), {payment.pk, voucher.pk})
            self.assertGreater(len(connection.atomic_blocks), depth)
            return original_clean(form)

        def save(instance, *args, **kwargs):
            self.assertEqual(set(locked), {payment.pk, voucher.pk})
            self.assertGreater(len(connection.atomic_blocks), depth)
            return original_save(instance, *args, **kwargs)

        self.client.force_login(self.user)
        with patch.object(QuerySet, "_fetch_all", fetch), patch.object(DeliveryForm, "clean", clean), \
                patch.object(InvoiceDelivery, "save", save):
            self.allocate(deal, payment)
        self.assertEqual(deal.delivered_quantity, Decimal("40"))

    def test_receipt_link_uses_authenticated_download(self):
        from ledger.models import VoucherAttachment
        from django.urls import reverse
        voucher, _ = self.make_deal()
        attachment = VoucherAttachment.objects.create(
            voucher=voucher, file="payment_receipts/test.pdf", uploaded_by=self.user,
        )
        self.client.force_login(self.user)
        response = self.client.get(f"/vouchers/{voucher.pk}/")
        self.assertContains(response, 'href="%s"' % reverse("receipt_download", args=[attachment.pk]))
        self.assertNotContains(response, 'href="%s"' % attachment.file.url)

    def test_partial_delivery_does_not_change_base_settlement(self):
        voucher, deal = self.make_deal()
        payment = self.make_delivery()
        self.client.force_login(self.user)
        response = self.allocate(deal, payment)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(hasattr(deal, "deliveries"), "Missing independent currency delivery allocation")
        self.assertEqual(deal.deliveries.count(), 1)
        self.assertEqual(deal.delivered_quantity, Decimal("40"))
        self.assertEqual(deal.remaining_delivery_quantity, Decimal("60"))
        self.assertEqual(deal.settled_amount, Decimal("0"))
        self.assertEqual(deal.remaining_amount, deal.total_base)
        self.assertFalse(InvoiceSettlement.objects.exists())
