"""آزمون‌های ابزارهای مدیر اصلی و گزارش سود روزانه.

هر دو به درخواست صریح کارفرما اضافه شده‌اند:
  • «امکان ویرایش جز صلاحیت مدیر اصلی باشه»
  • «به صورت روزانه میزان سود و زیان خرید و فروش رو هم محاسبه کنه»
"""
import datetime
from decimal import Decimal

from accounts.models import AuditLog, Permission, Role, User
from accounts.permissions import DEFAULT_ROLES

from . import balances, services
from .models import Deal, Entry, InventoryPosition, Voucher
from .tests import LedgerTestBase


class VoucherDeleteTests(LedgerTestBase):
    """حذف و ویرایش سند باید فقط از دست «مدیر اصلی» بربیاید."""

    def setUp(self):
        self.admin = User.objects.create_user(
            username="boss", password="Test-1234!", role=self.role, is_superuser=True
        )
        spec = DEFAULT_ROLES["employee"]
        role = Role.objects.create(code="employee", title=spec["title"])
        role.permissions.set(Permission.objects.filter(code__in=spec["permissions"]))
        self.employee = User.objects.create_user(
            username="clerk", password="Test-1234!", role=role
        )

    def _make_deal(self):
        return services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.aed, quantity=Decimal("100"), unit_price=Decimal("512000"),
            created_by=self.user,
        )

    def test_delete_removes_voucher_and_zeroes_balance(self):
        voucher = self._make_deal()
        self.assertNotEqual(balances.party_balances_map(self.customer), {})

        services.delete_voucher(voucher=voucher, reason="اشتباه بود", deleted_by=self.admin)

        self.assertFalse(Voucher.objects.filter(pk=voucher.pk).exists())
        self.assertEqual(Entry.objects.filter(voucher_id=voucher.pk).count(), 0)
        balance = balances.party_balances_map(self.customer)
        self.assertEqual(balance.get(self.rial.id, Decimal("0")), Decimal("0"))
        self.assertEqual(balance.get(self.aed.id, Decimal("0")), Decimal("0"))

    def test_delete_restores_inventory(self):
        services.post_deal(side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
                           currency=self.aed, quantity=Decimal("100"),
                           unit_price=Decimal("500000"), created_by=self.user)
        second = services.post_deal(side=Deal.Side.BUY, date=self.today,
                                    counterparty=self.supplier, currency=self.aed,
                                    quantity=Decimal("100"), unit_price=Decimal("520000"),
                                    created_by=self.user)
        services.delete_voucher(voucher=second, reason="اشتباه", deleted_by=self.admin)

        position = InventoryPosition.objects.get(currency=self.aed)
        self.assertEqual(position.quantity, Decimal("100"))
        self.assertEqual(position.avg_unit_cost, Decimal("500000"))

    def test_delete_is_recorded_in_audit_log(self):
        voucher = self._make_deal()
        number = voucher.number
        services.delete_voucher(voucher=voucher, reason="تست", deleted_by=self.admin)

        entry = AuditLog.objects.filter(action=AuditLog.Action.DELETE).first()
        self.assertIsNotNone(entry)
        self.assertIn(str(number), entry.summary)
        self.assertIsNotNone(entry.before)

    def test_cannot_delete_voucher_that_has_reversal(self):
        voucher = self._make_deal()
        services.void_voucher(voucher=voucher, reason="ابطال", created_by=self.user)
        with self.assertRaises(services.LedgerError):
            services.delete_voucher(voucher=voucher, reason="حذف", deleted_by=self.admin)

    def test_employee_cannot_delete_via_http(self):
        voucher = self._make_deal()
        self.client.force_login(self.employee)
        response = self.client.get(f"/vouchers/{voucher.pk}/delete/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Voucher.objects.filter(pk=voucher.pk).exists())

    def test_admin_role_without_superuser_is_blocked(self):
        """نقش مدیر کافی نیست؛ باید «مدیر اصلی» سیستم باشد."""
        voucher = self._make_deal()
        self.client.force_login(self.user)
        response = self.client.get(f"/vouchers/{voucher.pk}/delete/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Voucher.objects.filter(pk=voucher.pk).exists())

    def test_superuser_can_delete_via_http(self):
        voucher = self._make_deal()
        self.client.force_login(self.admin)
        response = self.client.post(f"/vouchers/{voucher.pk}/delete/", {"reason": "اشتباه"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Voucher.objects.filter(pk=voucher.pk).exists())

    def test_delete_requires_reason(self):
        voucher = self._make_deal()
        self.client.force_login(self.admin)
        self.client.post(f"/vouchers/{voucher.pk}/delete/", {"reason": "   "})
        self.assertTrue(Voucher.objects.filter(pk=voucher.pk).exists())

    def test_edit_deletes_and_redirects_to_prefilled_form(self):
        voucher = self._make_deal()
        self.client.force_login(self.admin)
        response = self.client.get(f"/vouchers/{voucher.pk}/edit/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/deal/new/", response["Location"])
        self.assertIn("quantity=100", response["Location"])
        self.assertFalse(Voucher.objects.filter(pk=voucher.pk).exists())


class DailyProfitReportTests(LedgerTestBase):
    """گزارش سود و زیان روزانه خرید و فروش."""

    def test_profit_appears_on_sale_day(self):
        from reports.views import _daily_profit_data

        services.post_deal(side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
                           currency=self.aed, quantity=Decimal("100"),
                           unit_price=Decimal("500000"), created_by=self.user)
        services.post_deal(side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
                           currency=self.aed, quantity=Decimal("50"),
                           unit_price=Decimal("530000"), created_by=self.user)

        rows, totals = _daily_profit_data(self.today, self.today)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["buy_total"], Decimal("50000000"))
        self.assertEqual(row["sell_total"], Decimal("26500000"))
        # (۵۳۰٬۰۰۰ − ۵۰۰٬۰۰۰) × ۵۰ = ۱٬۵۰۰٬۰۰۰
        self.assertEqual(row["profit"], Decimal("1500000"))
        self.assertEqual(totals["profit"], Decimal("1500000"))

    def test_buy_only_day_has_zero_profit(self):
        from reports.views import _daily_profit_data

        services.post_deal(side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
                           currency=self.aed, quantity=Decimal("100"),
                           unit_price=Decimal("500000"), created_by=self.user)
        rows, _totals = _daily_profit_data(self.today, self.today)
        self.assertEqual(rows[0]["profit"], Decimal("0"))
        self.assertIsNone(rows[0]["margin"])

    def test_running_profit_accumulates_across_days(self):
        from reports.views import _daily_profit_data

        yesterday = self.today - datetime.timedelta(days=1)
        services.post_deal(side=Deal.Side.BUY, date=yesterday, counterparty=self.supplier,
                           currency=self.aed, quantity=Decimal("200"),
                           unit_price=Decimal("500000"), created_by=self.user)
        services.post_deal(side=Deal.Side.SELL, date=yesterday, counterparty=self.customer,
                           currency=self.aed, quantity=Decimal("50"),
                           unit_price=Decimal("510000"), created_by=self.user)
        services.post_deal(side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
                           currency=self.aed, quantity=Decimal("50"),
                           unit_price=Decimal("520000"), created_by=self.user)

        rows, totals = _daily_profit_data(yesterday, self.today)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["profit"], Decimal("500000"))
        self.assertEqual(rows[1]["profit"], Decimal("1000000"))
        self.assertEqual(rows[1]["running_profit"], Decimal("1500000"))
        self.assertEqual(totals["profit"], Decimal("1500000"))

    def test_loss_is_reported_as_negative(self):
        from reports.views import _daily_profit_data

        services.post_deal(side=Deal.Side.BUY, date=self.today, counterparty=self.supplier,
                           currency=self.aed, quantity=Decimal("100"),
                           unit_price=Decimal("520000"), created_by=self.user)
        services.post_deal(side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
                           currency=self.aed, quantity=Decimal("100"),
                           unit_price=Decimal("500000"), created_by=self.user)

        rows, totals = _daily_profit_data(self.today, self.today)
        self.assertEqual(rows[0]["profit"], Decimal("-2000000"))
        self.assertLess(totals["profit"], 0)

    def test_employee_cannot_see_daily_profit(self):
        spec = DEFAULT_ROLES["employee"]
        role = Role.objects.create(code="employee", title=spec["title"])
        role.permissions.set(Permission.objects.filter(code__in=spec["permissions"]))
        employee = User.objects.create_user(username="clerk2", password="Test-1234!",
                                            role=role)
        self.client.force_login(employee)
        self.assertEqual(self.client.get("/reports/daily-profit/").status_code, 403)
