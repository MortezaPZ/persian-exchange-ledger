"""آزمون‌های ربات.

تمرکز روی دو تضمین امنیتی است:
  ۱) غریبه هیچ اطلاعاتی نمی‌گیرد
  ۲) ربات فقط می‌خواند و هرگز چیزی ثبت نمی‌کند
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import Permission, Role, User
from accounts.permissions import DEFAULT_ROLES
from core.models import Currency, FxRate, Party
from ledger import services
from ledger.models import Deal, Voucher

from .models import BotConfig, BotMessage
from .services import handle_incoming, resolve_party


class BotTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Permission.sync_catalog()
        spec = DEFAULT_ROLES["admin"]
        cls.role = Role.objects.create(code="admin", title=spec["title"], is_system=True)
        cls.role.permissions.set(Permission.objects.filter(code__in=spec["permissions"]))
        cls.user = User.objects.create_user(username="tester", password="Test-1234!",
                                            role=cls.role)

        cls.rial = Currency.objects.create(code="IRR", name="ریال", decimal_places=0,
                                           is_base=True, sort_order=0)
        cls.aed = Currency.objects.create(code="AED", name="درهم", decimal_places=2,
                                          sort_order=1)

        cls.customer = Party.objects.create(
            kind=Party.Kind.CUSTOMER, name="علی محمدی",
            telegram_id="123456789", whatsapp_no="989121234567",
        )
        cls.bank = Party.objects.create(kind=Party.Kind.BANK, name="بانک سامان",
                                        currency=cls.rial, code="SAMAN")
        cls.today = timezone.localdate()

        BotConfig.objects.create(platform=BotConfig.Platform.TELEGRAM,
                                 is_enabled=True, token="test-token")


class SenderResolutionTests(BotTestBase):
    def test_resolves_telegram_id(self):
        party = resolve_party(BotConfig.Platform.TELEGRAM, "123456789")
        self.assertEqual(party, self.customer)

    def test_resolves_whatsapp_with_country_code(self):
        self.assertEqual(
            resolve_party(BotConfig.Platform.WHATSAPP, "989121234567"), self.customer
        )

    def test_resolves_whatsapp_with_leading_zero(self):
        """شماره ممکن است با ۰۹۱۲ بیاید ولی با ۹۸۹۱۲ ذخیره شده باشد."""
        self.assertEqual(
            resolve_party(BotConfig.Platform.WHATSAPP, "09121234567"), self.customer
        )

    def test_unknown_sender_returns_none(self):
        self.assertIsNone(resolve_party(BotConfig.Platform.TELEGRAM, "999999"))
        self.assertIsNone(resolve_party(BotConfig.Platform.WHATSAPP, "989350000000"))

    def test_inactive_customer_not_resolved(self):
        self.customer.is_active = False
        self.customer.save(update_fields=["is_active"])
        self.assertIsNone(resolve_party(BotConfig.Platform.TELEGRAM, "123456789"))

    def test_empty_sender_returns_none(self):
        self.assertIsNone(resolve_party(BotConfig.Platform.TELEGRAM, ""))
        self.assertIsNone(resolve_party(BotConfig.Platform.WHATSAPP, None))


class BalanceReplyTests(BotTestBase):
    def setUp(self):
        # واریز ۵۰,۰۰۰,۰۰۰ ریال، سپس فروش ۱۰۰ درهم به ارزش ۵۱,۲۰۰,۰۰۰ ریال.
        # نتیجه: مشتری در ریال بدهکار و در درهم بستانکار می‌شود، پس پاسخ ربات
        # هر دو حالت را نشان می‌دهد.
        services.post_cash_movement(
            kind=Voucher.Kind.RECEIVE, date=self.today, party=self.customer,
            account=self.bank, currency=self.rial,
            amount=Decimal("50000000"), created_by=self.user,
        )
        services.post_deal(
            side=Deal.Side.SELL, date=self.today, counterparty=self.customer,
            currency=self.aed, quantity=Decimal("100"),
            unit_price=Decimal("512000"), created_by=self.user,
        )

    def test_balance_reply_contains_both_currencies(self):
        reply, _ = handle_incoming(BotConfig.Platform.TELEGRAM, "123456789", "مانده")
        self.assertIn("علی محمدی", reply)
        self.assertIn("درهم", reply)
        self.assertIn("بدهکار", reply)
        self.assertIn("بستانکار", reply)

    def test_balance_matches_ledger(self):
        """عددی که ربات می‌گوید باید دقیقاً همان چیزی باشد که در سامانه هست."""
        from ledger import balances

        reply, _ = handle_incoming(BotConfig.Platform.TELEGRAM, "123456789", "موجودی")
        balance = balances.party_balances_map(self.customer)

        # ۱۰۰ درهم بستانکار
        self.assertEqual(balance[self.aed.id], Decimal("-100"))
        self.assertIn("۱۰۰", reply)

    def test_statement_command(self):
        reply, _ = handle_incoming(BotConfig.Platform.TELEGRAM, "123456789", "گردش")
        self.assertIn("تراکنش آخر", reply)

    def test_help_command(self):
        reply, _ = handle_incoming(BotConfig.Platform.TELEGRAM, "123456789", "راهنما")
        self.assertIn("مانده", reply)
        self.assertIn("امکان ثبت یا تغییر", reply)

    def test_persian_digits_in_reply(self):
        reply, _ = handle_incoming(BotConfig.Platform.TELEGRAM, "123456789", "مانده")
        self.assertTrue(any(digit in reply for digit in "۰۱۲۳۴۵۶۷۸۹"))


class SecurityTests(BotTestBase):
    def test_unknown_sender_gets_no_financial_data(self):
        reply, _ = handle_incoming(BotConfig.Platform.TELEGRAM, "999999", "مانده")
        self.assertIn("ثبت نشده", reply)
        # هیچ نامی، مبلغی یا نام ارزی نباید لو برود
        self.assertNotIn("محمدی", reply)
        self.assertNotIn("درهم", reply)
        self.assertNotIn("بدهکار", reply)

    def test_unknown_sender_is_logged(self):
        handle_incoming(BotConfig.Platform.TELEGRAM, "999999", "مانده")
        record = BotMessage.objects.filter(
            direction=BotMessage.Direction.IN, sender_id="999999"
        ).first()
        self.assertIsNotNone(record)
        self.assertEqual(record.status, BotMessage.Status.UNKNOWN_SENDER)
        self.assertIsNone(record.party)

    def test_bot_never_creates_vouchers(self):
        """هر پیامی که بفرستند، نباید سندی ساخته شود."""
        before = Voucher.objects.count()
        for text in [
            "مانده", "گردش", "راهنما", "سلام",
            "ثبت کن ۱۰۰۰ درهم", "واریز شد ۵۰۰۰۰۰۰۰",
            "/start", "delete all", "درهم بخر",
        ]:
            handle_incoming(BotConfig.Platform.TELEGRAM, "123456789", text)
        self.assertEqual(Voucher.objects.count(), before)

    def test_unrecognized_command_gets_help(self):
        reply, _ = handle_incoming(BotConfig.Platform.TELEGRAM, "123456789", "سلام خوبی")
        self.assertIn("متوجه پیام شما نشدم", reply)

    def test_duplicate_message_ignored(self):
        """اگر سرویس پیام را دوباره بفرستد، دوباره پردازش نمی‌شود."""
        first, _ = handle_incoming(
            BotConfig.Platform.TELEGRAM, "123456789", "مانده", external_id="msg-1"
        )
        second, _ = handle_incoming(
            BotConfig.Platform.TELEGRAM, "123456789", "مانده", external_id="msg-1"
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(
            BotMessage.objects.filter(
                direction=BotMessage.Direction.IN, external_id="msg-1"
            ).count(),
            1,
        )


class BotPermissionTests(BotTestBase):
    def setUp(self):
        spec = DEFAULT_ROLES["employee"]
        role = Role.objects.create(code="employee", title=spec["title"])
        role.permissions.set(Permission.objects.filter(code__in=spec["permissions"]))
        self.employee = User.objects.create_user(username="clerk", password="Test-1234!",
                                                 role=role)

    def test_employee_cannot_open_bot_settings(self):
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get("/bots/").status_code, 403)
        self.assertEqual(self.client.get("/bots/telegram/settings/").status_code, 403)

    def test_admin_can_open_bot_dashboard(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/bots/").status_code, 200)


class WhatsAppWebhookTests(BotTestBase):
    def setUp(self):
        BotConfig.objects.create(
            platform=BotConfig.Platform.WHATSAPP, is_enabled=True,
            token="wa-token", phone_number_id="555", webhook_secret="s3cret",
        )

    def test_verification_requires_correct_token(self):
        bad = self.client.get("/bots/webhook/whatsapp/",
                              {"hub.verify_token": "wrong", "hub.challenge": "abc"})
        self.assertEqual(bad.status_code, 403)

        good = self.client.get("/bots/webhook/whatsapp/",
                               {"hub.verify_token": "s3cret", "hub.challenge": "abc"})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.content.decode(), "abc")

    def test_wrong_secret_header_rejected(self):
        response = self.client.post(
            "/bots/webhook/whatsapp/", data="{}", content_type="application/json",
            headers={"x-webhook-secret": "wrong"},
        )
        self.assertEqual(response.status_code, 403)

    def test_disabled_bot_rejects_webhook(self):
        BotConfig.objects.filter(platform=BotConfig.Platform.WHATSAPP).update(is_enabled=False)
        response = self.client.post("/bots/webhook/whatsapp/", data="{}",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_malformed_body_returns_400(self):
        response = self.client.post("/bots/webhook/whatsapp/", data="not-json",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)
