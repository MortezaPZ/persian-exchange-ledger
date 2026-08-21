"""راه‌اندازی اولیه سامانه.

این دستور را یک بار بعد از migrate اجرا کنید:
    python manage.py setup_sarrafi --admin-user admin --admin-pass 'رمز-قوی'

کارهایی که انجام می‌دهد:
  • مجوزها و نقش‌های پیش‌فرض را می‌سازد
  • ارزهای ریال، درهم، دلار و تتر را تعریف می‌کند
  • حساب‌های سیستمی (موقعیت ارزی، صندوق، افتتاحیه) را می‌سازد
  • یک کاربر مدیر می‌سازد

اجرای دوباره‌اش بی‌خطر است؛ چیزی را دوباره نمی‌سازد و داده‌ای را پاک نمی‌کند.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Permission, Role, User
from accounts.permissions import DEFAULT_ROLES
from core.models import Currency, Party

CURRENCIES = [
    {"code": "IRR", "name": "ریال", "symbol": "﷼", "decimal_places": 0,
     "is_base": True, "sort_order": 0},
    {"code": "AED", "name": "درهم", "symbol": "د.إ", "decimal_places": 2,
     "is_base": False, "sort_order": 1},
    {"code": "USD", "name": "دلار", "symbol": "$", "decimal_places": 2,
     "is_base": False, "sort_order": 2},
    {"code": "USDT", "name": "تتر", "symbol": "₮", "decimal_places": 2,
     "is_base": False, "sort_order": 3},
]


class Command(BaseCommand):
    help = "راه‌اندازی اولیه: مجوزها، نقش‌ها، ارزها، حساب‌های سیستمی و کاربر مدیر"

    def add_arguments(self, parser):
        parser.add_argument("--admin-user", default="admin", help="نام کاربری مدیر")
        parser.add_argument("--admin-pass", default=None, help="رمز عبور مدیر")
        parser.add_argument("--admin-name", default="مدیر سیستم", help="نام کامل مدیر")
        parser.add_argument("--skip-admin", action="store_true", help="کاربر مدیر ساخته نشود")

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("در حال راه‌اندازی…")

        # ۱) مجوزها
        Permission.sync_catalog()
        self.stdout.write(self.style.SUCCESS(f"  ✓ {Permission.objects.count()} مجوز هماهنگ شد"))

        # ۲) نقش‌ها
        #
        # هنگام به‌روزرسانی، مجوزهای «تازه‌متولدشده» باید به نقش‌های موجود هم
        # برسند، وگرنه کاربر قابلیت جدید را اصلاً نمی‌بیند. ولی نباید تنظیمات
        # دستی کاربر را خراب کنیم. پس فقط مجوزی اضافه می‌شود که هیچ نقشی در
        # سیستم آن را ندارد — یعنی واقعاً تازه است، نه چیزی که کاربر عمداً
        # از نقشی برداشته باشد.
        used_codes = set(
            Permission.objects.filter(roles__isnull=False)
            .values_list("code", flat=True)
            .distinct()
        )
        has_any_role = Role.objects.exists()

        for code, spec in DEFAULT_ROLES.items():
            role, created = Role.objects.get_or_create(
                code=code,
                defaults={"title": spec["title"], "description": spec["description"],
                          "is_system": True},
            )
            if created:
                role.permissions.set(Permission.objects.filter(code__in=spec["permissions"]))
                self.stdout.write(f"  ✓ نقش «{role.title}» ساخته شد")
                continue

            if not has_any_role:
                continue

            brand_new = [c for c in spec["permissions"] if c not in used_codes]
            if brand_new:
                role.permissions.add(*Permission.objects.filter(code__in=brand_new))
                self.stdout.write(
                    f"  ✓ {len(brand_new)} مجوز جدید به نقش «{role.title}» اضافه شد"
                )
            else:
                self.stdout.write(f"  · نقش «{role.title}» به‌روز است")

        # ۳) ارزها
        for spec in CURRENCIES:
            currency, created = Currency.objects.get_or_create(
                code=spec["code"],
                defaults={k: v for k, v in spec.items() if k != "code"},
            )
            if created:
                self.stdout.write(f"  ✓ ارز «{currency.name}» تعریف شد")

        base = Currency.objects.filter(is_base=True).first()
        if base is None:
            base = Currency.objects.filter(code="IRR").first()
            if base:
                base.is_base = True
                base.save(update_fields=["is_base"])

        # ۴) حساب‌های سیستمی
        for currency in Currency.objects.filter(is_active=True):
            Party.position_for(currency)
        self.stdout.write("  ✓ حساب‌های «موقعیت ارزی» برای همه ارزها آماده شد")

        # برای هر ارز یک صندوق ساخته می‌شود تا کاربر بتواند از همان ابتدا
        # دریافت و پرداخت ارزی ثبت کند؛ بدون این، فرم‌های دریافت/پرداخت فقط
        # حساب‌های ریالی نشان می‌دادند.
        for currency in Currency.objects.filter(is_active=True):
            _cashbox, created = Party.objects.get_or_create(
                kind=Party.Kind.CASHBOX, code=f"CASH-{currency.code}",
                defaults={
                    "name": f"صندوق {currency.name}",
                    "currency": currency,
                    "is_system": True,
                },
            )
            if created:
                self.stdout.write(f"  ✓ صندوق «{currency.name}» ساخته شد")
        Party.objects.get_or_create(
            kind=Party.Kind.EQUITY, code="OPENING",
            defaults={"name": "حساب افتتاحیه", "is_system": True},
        )
        self.stdout.write("  ✓ صندوق و حساب افتتاحیه آماده شد")

        # ۵) کاربر مدیر
        if not options["skip_admin"]:
            username = options["admin_user"]
            password = options["admin_pass"]
            admin_role = Role.objects.filter(code="admin").first()

            if User.objects.filter(username=username).exists():
                self.stdout.write(f"  · کاربر «{username}» از قبل وجود دارد (دست نخورد)")
            elif not password:
                self.stdout.write(self.style.WARNING(
                    "  ! کاربر مدیر ساخته نشد چون رمز عبور داده نشد.\n"
                    "    دوباره با --admin-pass اجرا کنید یا از createsuperuser استفاده کنید."
                ))
            elif len(password) < 8:
                self.stdout.write(self.style.ERROR("  ✗ رمز عبور باید حداقل ۸ کاراکتر باشد."))
            else:
                user = User.objects.create_user(
                    username=username, password=password,
                    full_name=options["admin_name"], role=admin_role,
                    is_staff=True, is_superuser=True,
                    must_change_password=True,
                )
                self.stdout.write(self.style.SUCCESS(f"  ✓ کاربر مدیر «{user.username}» ساخته شد"))

        self.stdout.write(self.style.SUCCESS("\nراه‌اندازی کامل شد. سرور را با runserver اجرا کنید."))
