"""آزمون رگرسیون: صفحه تخصیص تسویه فاکتور.

کارفرما: «قسمت اختصاص اسناد رو نمیاره». علت ریشه‌ای این بود که فهرست اسناد
قابل تخصیص فقط سندهای دریافت/پرداختِ هم‌ارزِ معامله را نشان می‌داد؛ تسویهٔ
فاکتورها با ریال (ارز پایه) انجام می‌شود و معامله هرگز ارز پایه نیست، پس
لیست همیشه خالی می‌ماند. ضمناً «انتقال بین مشتریان» هم باید در فهرست باشد.
"""
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.db.models.query import QuerySet
from django.test import TestCase

from django.utils import timezone

from accounts.models import Permission, Role, User
from accounts.permissions import DEFAULT_ROLES
from core.models import Currency, Party
from ledger import services
from ledger.forms import SettlementForm
from ledger.models import Deal, InvoiceSettlement, Voucher


class SettlementTestBase(TestCase):
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

        cls.customer = Party.objects.create(kind=Party.Kind.CUSTOMER, name="مشتری آزمایشی")
        cls.bank = Party.objects.create(kind=Party.Kind.BANK, name="بانک سامان",
                                        currency=cls.rial, code="SAMAN")
        cls.today = timezone.localdate()

    def make_deal(self, side=Deal.Side.SELL, party=None, settle_account=None):
        """فاکتور ۵٬۰۰۰٬۰۰۰ ریالی، بدون تبدیل واحد مبلغ تخصیص."""
        voucher = services.post_deal(
            side=side, date=self.today, counterparty=party or self.customer,
            currency=self.aed, quantity=Decimal("100"), unit_price=Decimal("50000"),
            created_by=self.user, settle_account=settle_account,
        )
        return voucher, voucher.deal

    def make_payment(self, amount=Decimal("3000000"), kind=Voucher.Kind.RECEIVE,
                     currency=None, party=None):
        voucher = services.post_cash_movement(
            kind=kind, date=self.today, party=party or self.customer,
            account=self.bank, currency=currency or self.rial,
            amount=amount, created_by=self.user,
        )
        return voucher


class SettlementLockTests(SettlementTestBase):
    def test_locks_both_vouchers_in_order_before_validation_and_save(self):
        # SQLite does not enforce row locks; observe evaluated ORM lock queries.
        payment = self.make_payment()
        voucher, deal = self.make_deal()
        locked = []
        original_fetch = QuerySet._fetch_all
        original_clean = SettlementForm.clean
        original_save = InvoiceSettlement.save
        initial_depth = len(connection.atomic_blocks)

        def fetch(queryset):
            original_fetch(queryset)
            if queryset.model is Voucher and queryset.query.select_for_update:
                self.assertGreater(len(connection.atomic_blocks), initial_depth)
                ids = [row.pk for row in queryset._result_cache]
                self.assertEqual(ids, sorted(ids))
                locked.extend(ids)

        def check_clean(form):
            self.assertEqual(set(locked), {voucher.pk, payment.pk})
            self.assertGreater(len(connection.atomic_blocks), initial_depth)
            return original_clean(form)

        def check_save(instance, *args, **kwargs):
            self.assertEqual(set(locked), {voucher.pk, payment.pk})
            self.assertGreater(len(connection.atomic_blocks), initial_depth)
            return original_save(instance, *args, **kwargs)

        self.client.force_login(self.user)
        with patch.object(QuerySet, "_fetch_all", fetch), \
                patch.object(SettlementForm, "clean", check_clean), \
                patch.object(InvoiceSettlement, "save", check_save):
            response = self.client.post(f"/deal/{deal.pk}/settlement/",
                                        {"payment": payment.pk, "amount": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(deal.settled_amount, Decimal("1"))


class SettlementFinalityTests(SettlementTestBase):
    def test_nonfinal_invoice_rejected_even_with_cached_voucher(self):
        voucher, deal = self.make_deal()
        self.assertEqual(deal.voucher.status, Voucher.Status.FINAL)
        payment = self.make_payment()
        for status in (Voucher.Status.DRAFT, Voucher.Status.VOID):
            with self.subTest(status=status):
                Voucher.objects.filter(pk=voucher.pk).update(status=status)
                form = SettlementForm(data={"payment": payment.pk, "amount": "1"}, deal=deal)
                self.assertFalse(form.is_valid())
                self.assertIn("قطعی", str(form.non_field_errors()))

    def test_upfront_settled_invoice_rejects_additional_allocation(self):
        for side, kind in ((Deal.Side.SELL, Voucher.Kind.RECEIVE),
                           (Deal.Side.BUY, Voucher.Kind.PAY)):
            with self.subTest(side=side):
                _, deal = self.make_deal(side=side, settle_account=self.bank)
                payment = self.make_payment(kind=kind)
                form = SettlementForm(data={"payment": payment.pk, "amount": "1"}, deal=deal)
                self.assertFalse(form.is_valid())
                self.assertIn("ارز پایه", str(form.non_field_errors()))

    def test_payment_must_remain_final_when_form_is_validated(self):
        _, deal = self.make_deal()
        payment = self.make_payment()
        for status in (Voucher.Status.DRAFT, Voucher.Status.VOID):
            with self.subTest(status=status):
                Voucher.objects.filter(pk=payment.pk).update(status=Voucher.Status.FINAL)
                form = SettlementForm(data={"payment": payment.pk, "amount": "1"}, deal=deal)
                list(form.fields["payment"].queryset)
                Voucher.objects.filter(pk=payment.pk).update(status=status)
                self.assertFalse(form.is_valid())
                self.assertIn("payment", form.errors)


class SettlementDuplicateTests(SettlementTestBase):
    def test_duplicate_allocation_returns_readable_error_not_500(self):
        _, deal = self.make_deal()
        payment = self.make_payment(amount=Decimal("300"))
        self.client.force_login(self.user)
        first = self.client.post(f"/deal/{deal.pk}/settlement/",
                                 {"payment": payment.pk, "amount": "100"})
        self.assertEqual(first.status_code, 302)
        second = self.client.post(f"/deal/{deal.pk}/settlement/",
                                  {"payment": payment.pk, "amount": "100"}, follow=True)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(InvoiceSettlement.objects.filter(deal=deal).count(), 1)
        self.assertContains(second, "قبلا")


class SettlementCapacityTests(SettlementTestBase):
    def test_payment_capacity_is_shared_across_invoices(self):
        _, first = self.make_deal()
        _, second = self.make_deal()
        payment = self.make_payment(amount=Decimal("100.25"))
        InvoiceSettlement.objects.create(deal=first, payment=payment,
                                         amount=Decimal("60.10"), created_by=self.user)
        form = SettlementForm(data={"payment": payment.pk, "amount": "40.16"}, deal=second)
        self.assertFalse(form.is_valid())
        self.assertIn("amount", form.errors)
        exact = SettlementForm(data={"payment": payment.pk, "amount": "40.15"}, deal=second)
        self.assertTrue(exact.is_valid(), exact.errors)

    def test_transfer_capacity_is_independent_for_each_customer(self):
        other = Party.objects.create(kind=Party.Kind.CUSTOMER, name="طرف دوم")
        _, sell = self.make_deal()
        _, buy = self.make_deal(side=Deal.Side.BUY, party=other)
        transfer = services.post_party_transfer(
            date=self.today, from_party=self.customer, to_party=other,
            currency=self.rial, amount=Decimal("100"), created_by=self.user,
        )
        InvoiceSettlement.objects.create(deal=sell, payment=transfer,
                                         amount=Decimal("100"), created_by=self.user)
        self.client.force_login(self.user)
        response = self.client.post(f"/deal/{buy.pk}/settlement/",
                                    {"payment": transfer.pk, "amount": "100"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(buy.settled_amount, Decimal("100"))
        _, next_sell = self.make_deal()
        form = SettlementForm(data={"payment": transfer.pk, "amount": "1"}, deal=next_sell)
        self.assertFalse(form.is_valid())

    def test_nonfinal_invoice_allocations_do_not_consume_capacity(self):
        voucher, first = self.make_deal()
        _, second = self.make_deal()
        payment = self.make_payment(amount=Decimal("100"))
        InvoiceSettlement.objects.create(deal=first, payment=payment,
                                         amount=Decimal("100"), created_by=self.user)
        for status in (Voucher.Status.DRAFT, Voucher.Status.VOID):
            with self.subTest(status=status):
                Voucher.objects.filter(pk=voucher.pk).update(status=status)
                form = SettlementForm(data={"payment": payment.pk, "amount": "100"}, deal=second)
                self.assertTrue(form.is_valid(), form.errors)


class SettlementDirectionTests(SettlementTestBase):

    def test_wrong_direction_is_rejected(self):
        other = Party.objects.create(kind=Party.Kind.CUSTOMER, name="طرف دوم")
        for side, wrong_kind in ((Deal.Side.SELL, Voucher.Kind.PAY),
                                 (Deal.Side.BUY, Voucher.Kind.RECEIVE)):
            _, deal = self.make_deal(side=side)
            wrong_cash = self.make_payment(kind=wrong_kind)
            wrong_transfer = services.post_party_transfer(
                date=self.today,
                from_party=other if side == Deal.Side.SELL else self.customer,
                to_party=self.customer if side == Deal.Side.SELL else other,
                currency=self.rial, amount=Decimal("100"), created_by=self.user,
            )
            for payment in (wrong_cash, wrong_transfer):
                with self.subTest(side=side, payment=payment.pk):
                    form = SettlementForm(data={"payment": payment.pk, "amount": "1"}, deal=deal)
                    self.assertFalse(form.is_valid())
                    self.assertIn("payment", form.errors)
                    self.assertNotIn(payment, form.fields["payment"].queryset)


class SettlementDropdownTests(SettlementTestBase):

    def test_form_lists_final_receive_pay_in_base_currency(self):
        """سند دریافت ریالی باید در فهرست تخصیص فاکتور درهمی بیاید."""
        _, deal = self.make_deal()
        payment = self.make_payment()
        form = SettlementForm(deal=deal)
        self.assertIn(payment, form.fields["payment"].queryset)

    def test_form_lists_party_transfer_vouchers(self):
        """انتقال بین مشتریان هم باید قابل تخصیص باشد (کارفرما: بخش زیادی
        از اسناد من انتقال بین مشتریانه)."""
        _, deal = self.make_deal()
        other = Party.objects.create(kind=Party.Kind.CUSTOMER, name="رضا کریمی")
        transfer = services.post_party_transfer(
            date=self.today, from_party=self.customer, to_party=other,
            currency=self.rial, amount=Decimal("2000000"), created_by=self.user,
        )
        form = SettlementForm(deal=deal)
        self.assertIn(transfer, form.fields["payment"].queryset)

    def test_form_excludes_unrelated_party(self):
        """سند مشتری بی‌ربط نباید در فهرست باشد."""
        _, deal = self.make_deal()
        stranger = Party.objects.create(kind=Party.Kind.CUSTOMER, name="بیگانه")
        unrelated = self.make_payment(party=stranger)
        form = SettlementForm(deal=deal)
        self.assertNotIn(unrelated, form.fields["payment"].queryset)

    def test_form_excludes_draft_and_void(self):
        _, deal = self.make_deal()
        payment = self.make_payment()
        Voucher.objects.filter(pk=payment.pk).update(status=Voucher.Status.VOID)
        payment.refresh_from_db()
        form = SettlementForm(deal=deal)
        self.assertNotIn(payment, form.fields["payment"].queryset)

    def test_full_end_to_end_allocation(self):
        """ثبت تخصیص از مسیر ویو + محاسبهٔ مانده و درصد تسویه."""
        deal_voucher, deal = self.make_deal()
        payment = self.make_payment(amount=Decimal("2000000"))

        self.client.force_login(self.user)
        response = self.client.post(f"/deal/{deal.pk}/settlement/", {
            "payment": str(payment.pk),
            "amount": "2000000",
            "note": "قسط اول",
        })
        self.assertEqual(InvoiceSettlement.objects.filter(deal=deal).count(), 1)
        deal.refresh_from_db()
        self.assertEqual(deal.settled_amount, Decimal("2000000"))
        self.assertEqual(deal.remaining_amount, Decimal("3000000"))
        self.assertRedirects(response, f"/vouchers/{deal_voucher.pk}/", target_status_code=200)

    def test_form_excludes_other_currency_payment(self):
        """دریافتِ مثلاً دلاری نباید برای تخصیص به فاکتور درهمی پیشنهاد شود —
        مبلغ تخصیص به ریال است و ارز سوم فقط گیج‌کننده است."""
        usd = Currency.objects.create(code="USD", name="دلار", decimal_places=2, sort_order=2)
        _, deal = self.make_deal()
        usd_bank = Party.objects.create(kind=Party.Kind.BANK, name="بانک دلاری", currency=usd)
        usd_receive = services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=usd_bank, currency=usd, amount=Decimal("1000"), created_by=self.user,
        )
        form = SettlementForm(deal=deal)
        self.assertNotIn(usd_receive, form.fields["payment"].queryset)

    def test_amount_over_remaining_rejected(self):
        _, deal = self.make_deal()
        payment = self.make_payment(amount=Decimal("9000000"))
        form = SettlementForm(data={"payment": payment.pk, "amount": "9000000", "note": ""},
                              deal=deal)
        self.assertFalse(form.is_valid())
        self.assertIn("بیشتر", form.errors["amount"][0])

    def test_rejected_allocation_shows_error_message(self):
        """تخصیص ردشده نباید بی‌صدا به صفحه برگردد — کارفرما باید خطا را ببیند."""
        deal_voucher, deal = self.make_deal()
        payment = self.make_payment(amount=Decimal("9000000"))
        self.client.force_login(self.user)
        response = self.client.post(
            f"/deal/{deal.pk}/settlement/",
            {"payment": str(payment.pk), "amount": "9000000", "note": ""},
            follow=True,
        )
        self.assertEqual(InvoiceSettlement.objects.filter(deal=deal).count(), 0)
        self.assertContains(response, "بیشتر")
