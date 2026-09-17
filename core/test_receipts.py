"""Private receipt downloads and isolated deployment-setting regression tests."""
import tempfile
from datetime import date
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import Resolver404, resolve

from accounts.models import Permission, Role, User
from accounts.permissions import Perm
from ledger.models import Voucher, VoucherAttachment


class ReceiptDownloadTests(TestCase):
    """مجوز مشاهده سند، شرط دانلود فیش است؛ نه مسیر عمومی."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings_override = override_settings(MEDIA_ROOT=self.temp.name)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        permission, _ = Permission.objects.get_or_create(code=Perm.VOUCHER_VIEW)
        role = Role.objects.create(code="receipt-reader", title="مشاهده فیش")
        role.permissions.add(permission)
        self.reader = User.objects.create_user(username="reader", role=role)
        self.voucher = Voucher.objects.create(
            kind=Voucher.Kind.RECEIVE, date=date(2026, 1, 1), created_by=self.reader,
        )
        self.attachment = VoucherAttachment.objects.create(
            voucher=self.voucher, uploaded_by=self.reader,
            file=SimpleUploadedFile("receipt.pdf", b"%PDF-isolated-receipt"),
        )
        self.url = f"/receipts/{self.attachment.pk}/download/"

    def test_permitted_user_downloads_private_attachment(self):
        self.client.force_login(self.reader)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        try:
            self.assertEqual(b"".join(response.streaming_content), b"%PDF-isolated-receipt")
            self.assertIn("attachment;", response["Content-Disposition"])
            self.assertEqual(response["Content-Type"], "application/octet-stream")
            self.assertIn("no-store", response["Cache-Control"])
        finally:
            response.close()

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts/login", response["Location"])

    def test_user_without_voucher_view_perm_gets_403(self):
        plain_role = Role.objects.create(code="plain", title="بدون مجوز")
        plain = User.objects.create_user(username="plain", role=plain_role)
        self.client.force_login(plain)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_missing_file_on_disk_returns_404(self):
        self.client.force_login(self.reader)
        self.attachment.file.delete(save=False)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_missing_attachment_row_returns_404(self):
        self.client.force_login(self.reader)
        response = self.client.get("/receipts/99999/download/")
        self.assertEqual(response.status_code, 404)

    def test_empty_file_field_returns_404(self):
        self.client.force_login(self.reader)
        VoucherAttachment.objects.filter(pk=self.attachment.pk).update(file="")
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_post_cannot_download_or_mutate_attachment(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.assertTrue(VoucherAttachment.objects.filter(pk=self.attachment.pk).exists())

    def test_head_is_allowed_without_body(self):
        self.client.force_login(self.reader)
        response = self.client.head(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"")
        response.close()

    def test_inactive_user_is_denied_by_the_view(self):
        from django.core.exceptions import PermissionDenied
        from django.test import RequestFactory
        from core.receipts import receipt_download

        request = RequestFactory().get(self.url)
        self.reader.is_active = False
        request.user = self.reader
        with self.assertRaises(PermissionDenied):
            receipt_download(request, self.attachment.pk)

    def test_download_filename_uses_stored_file_name(self):
        self.client.force_login(self.reader)
        response = self.client.get(self.url)
        self.assertIn("receipt.pdf", response["Content-Disposition"])
        response.close()


class ReceiptsMediaServingTests(TestCase):
    """رسانه صرافی هرگز از مسیر عمومی MEDIA_URL سرو نمی‌شود."""

    def test_media_url_is_not_a_public_route(self):
        with self.assertRaises(Resolver404):
            resolve("/media/payment_receipts/x.pdf")

    def test_media_root_lives_under_data_dir(self):
        from django.conf import settings

        self.assertEqual(Path(settings.MEDIA_ROOT), Path(settings.DATA_DIR) / "media")


class DeploymentSettingsTests(TestCase):
    def test_external_env_file_is_used_without_release_env(self):
        import json
        import os
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as root:
            external = Path(root) / "server.env"
            external.write_text("SARRAFI_MEDIA_ROOT=" + Path(root).as_posix() + "/uploads\n", encoding="utf-8")
            environment = os.environ.copy()
            for key in ("SARRAFI_MEDIA_ROOT", "PYTHON_DOTENV_DISABLED"):
                environment.pop(key, None)
            environment.update(
                SARRAFI_ENV_FILE=str(external), SARRAFI_DATA_DIR=root,
                SECRET_KEY="isolated-test-key", DEBUG="false", SARRAFI_DESKTOP="false",
                DB_ENGINE="postgresql", DB_NAME="isolated", DB_USER="isolated",
                DB_PASSWORD="", DB_HOST="127.0.0.1", DB_PORT="5432",
                ALLOWED_HOSTS="accounts.example.invalid", SECURE_SSL_REDIRECT="true",
                CSRF_TRUSTED_ORIGINS="https://accounts.example.invalid",
            )
            result = subprocess.run(
                [sys.executable, "-c", "import json; from config import settings as s; "
                 "print(json.dumps([str(s.MEDIA_ROOT), s.DEBUG, s.DATABASES['default']['ENGINE'], "
                 "s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, s.SECURE_SSL_REDIRECT, "
                 "s.ALLOWED_HOSTS, s.CSRF_TRUSTED_ORIGINS]))"],
                cwd=Path(__file__).resolve().parents[1], env=environment,
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(result.stdout), [
                str(Path(root) / "uploads"), False, "django.db.backends.postgresql",
                True, True, True, ["accounts.example.invalid"], ["https://accounts.example.invalid"],
            ])
