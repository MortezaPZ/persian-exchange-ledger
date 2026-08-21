from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


class Currency(models.Model):
    """فهرست ارزهایی که صرافی با آن‌ها کار می‌کند.

    برای اضافه کردن ارز جدید (یورو، لیر، …) فقط یک سطر به این جدول اضافه
    می‌شود؛ هیچ تغییری در برنامه لازم نیست.
    """

    code = models.CharField("کد", max_length=8, unique=True)
    name = models.CharField("نام", max_length=32)
    symbol = models.CharField("نماد", max_length=8, blank=True)
    decimal_places = models.PositiveSmallIntegerField("تعداد اعشار", default=2)
    is_base = models.BooleanField(
        "ارز پایه", default=False,
        help_text="ارزی که همه چیز نسبت به آن سنجیده می‌شود. فقط یک ارز می‌تواند پایه باشد.",
    )
    is_active = models.BooleanField("فعال", default=True)
    sort_order = models.PositiveIntegerField("ترتیب نمایش", default=0)

    class Meta:
        verbose_name = "ارز"
        verbose_name_plural = "ارزها"
        ordering = ["sort_order", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_base"], condition=models.Q(is_base=True), name="only_one_base_currency"
            )
        ]

    def __str__(self):
        return self.name

    @property
    def quantum(self):
        return Decimal(1).scaleb(-self.decimal_places)

    @classmethod
    def base(cls):
        return cls.objects.filter(is_base=True).first()


class Party(models.Model):
    """طرف حساب: مشتری، بانک، صندوق، یا موقعیت ارزی خود صرافی.

    اینکه بانک هم در همین جدول باشد عمداً است: با این کار هر رویداد مالی
    دقیقاً دو طرف دارد و موجودی بانک‌های خودمان هم خودکار محاسبه می‌شود.
    """

    class Kind(models.TextChoices):
        CUSTOMER = "customer", "مشتری"
        BANK = "bank", "بانک"
        CASHBOX = "cashbox", "صندوق"
        POSITION = "position", "موقعیت ارزی صرافی"
        EXPENSE = "expense", "دسته‌بندی هزینه"
        INCOME = "income", "دسته‌بندی درآمد"
        EQUITY = "equity", "سرمایه"

    kind = models.CharField("نوع", max_length=16, choices=Kind.choices, default=Kind.CUSTOMER, db_index=True)
    name = models.CharField("نام", max_length=128)
    code = models.CharField("کد حساب", max_length=32, blank=True, db_index=True)
    phone = models.CharField("شماره تماس", max_length=32, blank=True)
    national_id = models.CharField("کد ملی / شناسه", max_length=32, blank=True)

    telegram_id = models.CharField("شناسه تلگرام", max_length=64, blank=True, db_index=True)
    whatsapp_no = models.CharField("شماره واتس‌اپ", max_length=32, blank=True, db_index=True)

    # فقط برای حساب‌های تک‌ارزی مثل «بانک سامان (ریالی)» یا «موقعیت درهم»
    currency = models.ForeignKey(
        Currency, verbose_name="ارز حساب", on_delete=models.PROTECT,
        null=True, blank=True, related_name="parties",
        help_text="برای بانک، صندوق و موقعیت ارزی الزامی است. مشتری‌ها چندارزی‌اند و خالی می‌ماند.",
    )
    is_system = models.BooleanField("حساب سیستمی", default=False,
                                    help_text="حساب‌های سیستمی قابل حذف نیستند.")
    is_active = models.BooleanField("فعال", default=True, db_index=True)
    note = models.TextField("توضیحات", blank=True)

    created_at = models.DateTimeField("زمان ایجاد", auto_now_add=True)
    updated_at = models.DateTimeField("آخرین تغییر", auto_now=True)

    class Meta:
        verbose_name = "طرف حساب"
        verbose_name_plural = "طرف حساب‌ها"
        ordering = ["kind", "name"]
        indexes = [models.Index(fields=["kind", "is_active"])]

    def __str__(self):
        return self.name

    def clean(self):
        single_currency_kinds = {self.Kind.BANK, self.Kind.CASHBOX, self.Kind.POSITION}
        if self.kind in single_currency_kinds and self.currency_id is None:
            raise ValidationError({"currency": "برای بانک، صندوق و موقعیت ارزی، انتخاب ارز الزامی است."})
        if self.kind == self.Kind.CUSTOMER and self.currency_id is not None:
            raise ValidationError({"currency": "حساب مشتری چندارزی است و نباید ارز مشخصی داشته باشد."})

    @property
    def kind_label(self):
        return self.get_kind_display()

    @classmethod
    def position_for(cls, currency):
        """حساب «موقعیت ارزی» مربوط به یک ارز را برمی‌گرداند (در صورت نبود، می‌سازد)."""
        party, _created = cls.objects.get_or_create(
            kind=cls.Kind.POSITION,
            currency=currency,
            defaults={
                "name": f"موقعیت ارزی صرافی — {currency.name}",
                "code": f"POS-{currency.code}",
                "is_system": True,
            },
        )
        return party


class RateSource(models.Model):
    """منبع دریافت نرخ ارز.

    نوع «دستی» یعنی کاربر خودش نرخ را وارد می‌کند. نوع «سرویس اینترنتی» یک
    آدرس JSON می‌گیرد و مسیر رسیدن به عدد نرخ در پاسخ را از روی مسیر نقطه‌ای
    (مثل: "aed.value") پیدا می‌کند — بنابراین اتصال به هر سایت نرخ ارزی
    بدون تغییر کد ممکن است.
    """

    class Kind(models.TextChoices):
        MANUAL = "manual", "ورود دستی"
        HTTP_JSON = "http_json", "سرویس اینترنتی (JSON)"

    title = models.CharField("عنوان", max_length=64)
    kind = models.CharField("نوع", max_length=16, choices=Kind.choices, default=Kind.MANUAL)
    url = models.URLField("آدرس سرویس", blank=True, max_length=500)
    api_key = models.CharField("کلید API", max_length=255, blank=True,
                               help_text="در صورت نیاز، در آدرس با {api_key} جایگزین می‌شود.")
    timeout_seconds = models.PositiveSmallIntegerField("مهلت پاسخ (ثانیه)", default=10)
    is_active = models.BooleanField("فعال", default=True)
    note = models.CharField("توضیح", max_length=255, blank=True)

    class Meta:
        verbose_name = "منبع نرخ"
        verbose_name_plural = "منابع نرخ"
        ordering = ["id"]

    def __str__(self):
        return self.title


class RateSourceMapping(models.Model):
    """نگاشت هر ارز به مسیر مقدارش در پاسخ JSON منبع."""

    source = models.ForeignKey(RateSource, verbose_name="منبع", on_delete=models.CASCADE,
                               related_name="mappings")
    currency = models.ForeignKey(Currency, verbose_name="ارز", on_delete=models.CASCADE,
                                 related_name="rate_mappings")
    json_path = models.CharField(
        "مسیر مقدار در پاسخ", max_length=255,
        help_text='مسیر نقطه‌ای تا عدد نرخ. مثال: "aed.value" یا "data.0.price"',
    )
    multiplier = models.DecimalField(
        "ضریب تبدیل", max_digits=18, decimal_places=8, default=Decimal("1"),
        help_text="اگر سرویس نرخ را به تومان می‌دهد و پایه شما ریال است، ۱۰ بگذارید.",
    )

    class Meta:
        verbose_name = "نگاشت نرخ"
        verbose_name_plural = "نگاشت‌های نرخ"
        constraints = [
            models.UniqueConstraint(fields=["source", "currency"], name="uniq_source_currency_mapping")
        ]

    def __str__(self):
        return f"{self.source.title} → {self.currency.name}"


class FxRate(models.Model):
    """تاریخچه نرخ ارز.

    نرخ‌ها هرگز روی هم نوشته نمی‌شوند: نرخ دیروز پاک نمی‌شود تا نرخ امروز
    جایش بنشیند. به همین دلیل گزارش سه ماه پیش، سه ماه دیگر هم همان اعداد
    را می‌دهد.
    """

    currency = models.ForeignKey(Currency, verbose_name="ارز", on_delete=models.PROTECT,
                                 related_name="rates")
    rate_to_base = models.DecimalField(
        "نرخ به ارز پایه", max_digits=28, decimal_places=8,
        validators=[MinValueValidator(Decimal("0.00000001"))],
    )
    source = models.ForeignKey(RateSource, verbose_name="منبع", on_delete=models.SET_NULL,
                               null=True, blank=True, related_name="rates")
    source_label = models.CharField("عنوان منبع", max_length=64, blank=True)
    effective_at = models.DateTimeField("زمان اعتبار", db_index=True)
    created_by = models.ForeignKey("accounts.User", verbose_name="ثبت‌کننده",
                                   on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True)

    class Meta:
        verbose_name = "نرخ ارز"
        verbose_name_plural = "نرخ‌های ارز"
        ordering = ["-effective_at", "-id"]
        indexes = [models.Index(fields=["currency", "-effective_at"])]

    def __str__(self):
        return f"{self.currency.code} = {self.rate_to_base}"
