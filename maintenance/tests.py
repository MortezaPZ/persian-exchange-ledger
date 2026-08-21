"""آزمون‌های پشتیبان‌گیری، بازگردانی و پاک‌سازی.

این‌ها مستقیماً از یک اتفاق واقعی آمده‌اند: کارفرما یک روز برنامه را باز کرد و
همه چیز صفر بود. پس اینجا سخت‌گیرانه می‌سنجیم که پشتیبان واقعاً ساخته شود،
واقعاً قابل بازگردانی باشد، و پاک‌سازی بدون پشتیبان اصلاً انجام نشود.
"""
import contextlib
import copy
import sqlite3
import tempfile
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Permission, Role, User
from accounts.permissions import DEFAULT_ROLES
from core.models import Currency, Party
from ledger import services
from ledger.models import Voucher

from . import backups


@contextlib.contextmanager
def temp_data_dir():
    """پوشه داده موقت با یک فایل پایگاه‌داده واقعی.

    آزمون‌های جنگو روی پایگاه‌داده در حافظه اجرا می‌شوند و فایلی روی دیسک
    وجود ندارد، ولی پشتیبان‌گیری ذاتاً با فایل کار می‌کند. پس اینجا یک فایل
    SQLite واقعی می‌سازیم تا رفتار واقعی سنجیده شود.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_file = root / "db.sqlite3"

        conn = sqlite3.connect(str(db_file))
        try:
            conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, note TEXT)")
            conn.execute("INSERT INTO sample (note) VALUES ('آزمون')")
            conn.commit()
        finally:
            conn.close()

        databases = copy.deepcopy(settings.DATABASES)
        databases["default"]["ENGINE"] = "django.db.backends.sqlite3"
        databases["default"]["NAME"] = str(db_file)

        with override_settings(DATA_DIR=root, DATABASES=databases):
            yield root


class MaintenanceTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Permission.sync_catalog()
        spec = DEFAULT_ROLES["admin"]
        cls.admin_role = Role.objects.create(code="admin", title=spec["title"], is_system=True)
        cls.admin_role.permissions.set(
            Permission.objects.filter(code__in=spec["permissions"])
        )
        cls.admin = User.objects.create_user(
            username="boss", password="Test-1234!", role=cls.admin_role,
            is_superuser=True, is_staff=True,
        )

        employee_spec = DEFAULT_ROLES["employee"]
        cls.employee_role = Role.objects.create(code="employee", title=employee_spec["title"])
        cls.employee_role.permissions.set(
            Permission.objects.filter(code__in=employee_spec["permissions"])
        )
        cls.employee = User.objects.create_user(
            username="clerk", password="Test-1234!", role=cls.employee_role
        )

        cls.rial = Currency.objects.create(code="IRR", name="ریال", decimal_places=0,
                                           is_base=True, sort_order=0)
        cls.aed = Currency.objects.create(code="AED", name="درهم", decimal_places=2,
                                          sort_order=1)
        cls.customer = Party.objects.create(kind=Party.Kind.CUSTOMER, name="مشتری آزمون")
        cls.bank = Party.objects.create(kind=Party.Kind.BANK, name="بانک آزمون",
                                        currency=cls.rial, code="TEST")
        cls.today = timezone.localdate()


class BackupPermissionTests(MaintenanceTestBase):
    def test_employee_cannot_reach_backup_page(self):
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get("/system/backup/").status_code, 403)

    def test_employee_cannot_reach_reset_page(self):
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get("/system/reset/").status_code, 403)

    def test_admin_can_reach_backup_page(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/system/backup/").status_code, 200)

    def test_mobile_help_page_is_open_to_any_user(self):
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get("/system/mobile/").status_code, 200)


class BackupFileTests(MaintenanceTestBase):
    """پشتیبان باید یک فایل واقعی و سالم باشد، نه یک فایل خالی."""

    def test_create_backup_produces_readable_database(self):
        with temp_data_dir():
            path = backups.create_backup(label="test")
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 0)

            conn = sqlite3.connect(str(path))
            try:
                check = conn.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(check, "ok")

    def test_list_backups_returns_newest_first(self):
        with temp_data_dir():
            backups.create_backup(label="one")
            backups.create_backup(label="two")
            rows = backups.list_backups()
            self.assertEqual(len(rows), 2)
            self.assertGreaterEqual(rows[0]["created_at"], rows[1]["created_at"])

    def test_restore_rejects_unknown_file(self):
        with temp_data_dir():
            with self.assertRaises(RuntimeError):
                backups.restore_backup("does-not-exist.sqlite3")


class DataResetTests(MaintenanceTestBase):
    def setUp(self):
        services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=self.bank, currency=self.rial, amount=Decimal("50000000"),
            created_by=self.admin,
        )

    def test_reset_requires_exact_confirm_phrase(self):
        self.client.force_login(self.admin)
        before = Voucher.objects.count()
        self.assertGreater(before, 0)

        response = self.client.post("/system/reset/", {"confirm_phrase": "بله"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Voucher.objects.count(), before)

    def test_reset_clears_vouchers_and_keeps_users(self):
        with temp_data_dir():
            self.client.force_login(self.admin)
            users_before = User.objects.count()

            response = self.client.post("/system/reset/", {"confirm_phrase": "پاک کن"})
            self.assertEqual(response.status_code, 302)

            self.assertEqual(Voucher.objects.count(), 0)
            self.assertEqual(User.objects.count(), users_before)
            self.assertTrue(Currency.objects.exists())
            # قبل از پاک‌سازی باید پشتیبان ساخته شده باشد
            self.assertTrue(backups.list_backups())

    def test_reset_keeps_customers_unless_asked(self):
        with temp_data_dir():
            self.client.force_login(self.admin)
            self.client.post("/system/reset/", {"confirm_phrase": "پاک کن"})
            self.assertTrue(
                Party.objects.filter(kind=Party.Kind.CUSTOMER, name="مشتری آزمون").exists()
            )

    def test_non_superuser_admin_role_is_blocked(self):
        """حتی کاربری با نقش مدیر، اگر «مدیر اصلی» نباشد نباید بتواند پاک کند."""
        deputy = User.objects.create_user(username="deputy", password="Test-1234!",
                                          role=self.admin_role)
        self.client.force_login(deputy)
        response = self.client.post("/system/reset/", {"confirm_phrase": "پاک کن"})
        self.assertEqual(response.status_code, 403)
        self.assertGreater(Voucher.objects.count(), 0)
