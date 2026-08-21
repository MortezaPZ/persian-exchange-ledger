from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from core.jalali import format_jalali


class Sequence(models.Model):
    """شمارنده‌های سریالی (فعلاً فقط شماره سند).

    برای گرفتن شماره بعدی حتماً باید با select_for_update قفل شود تا اگر دو
    کاربر هم‌زمان سند ثبت کردند، شماره تکراری صادر نشود.
    """

    key = models.CharField("کلید", max_length=32, unique=True)
    value = models.BigIntegerField("مقدار فعلی", default=0)

    class Meta:
        verbose_name = "شمارنده"
        verbose_name_plural = "شمارنده‌ها"

    def __str__(self):
        return f"{self.key} = {self.value}"


class Voucher(models.Model):
    """سند: یک رویداد مالی کامل (خرید، فروش، دریافت، پرداخت، …)."""

    class Kind(models.TextChoices):
        DEAL = "deal", "معامله (خرید/فروش ارز)"
        RECEIVE = "receive", "دریافت"
        PAY = "pay", "پرداخت"
        TRANSFER = "transfer", "انتقال داخلی"
        ADJUST = "adjust", "اصلاحی"
        OPENING = "opening", "افتتاحیه"
        EXPENSE = "expense", "هزینه"
        INCOME = "income", "درآمد"

    class Status(models.TextChoices):
        DRAFT = "draft", "پیش‌نویس"
        FINAL = "final", "قطعی"
        VOID = "void", "باطل"

    number = models.BigIntegerField("شماره سند", null=True, blank=True, unique=True, db_index=True)
    kind = models.CharField("نوع سند", max_length=16, choices=Kind.choices, db_index=True)
    status = models.CharField("وضعیت", max_length=8, choices=Status.choices,
                              default=Status.DRAFT, db_index=True)
    date = models.DateField("تاریخ", db_index=True)
    description = models.TextField("شرح", blank=True)

    external_key = models.CharField(
        "کلید یکتا", max_length=128, null=True, blank=True, unique=True,
        help_text="ضد ثبت تکراری. اگر همین کلید قبلاً ثبت شده باشد، سند دوباره ثبت نمی‌شود.",
    )

    created_by = models.ForeignKey("accounts.User", verbose_name="ثبت‌کننده",
                                   on_delete=models.PROTECT, related_name="vouchers_created")
    created_at = models.DateTimeField("زمان ثبت", auto_now_add=True, db_index=True)
    finalized_at = models.DateTimeField("زمان قطعی شدن", null=True, blank=True)

    # سند برگشتی: سندی که اثر یک سند دیگر را خنثی می‌کند
    reverses = models.OneToOneField(
        "self", verbose_name="سند برگشتیِ", on_delete=models.PROTECT,
        null=True, blank=True, related_name="reversed_by",
    )
    void_reason = models.CharField("علت ابطال", max_length=255, blank=True)

    class Meta:
        verbose_name = "سند"
        verbose_name_plural = "اسناد"
        ordering = ["-date", "-number", "-id"]
        indexes = [
            models.Index(fields=["-date", "-id"]),
            models.Index(fields=["kind", "status"]),
        ]

    def __str__(self):
        return f"سند {self.number or '—'} ({self.get_kind_display()})"

    @property
    def jalali_date(self):
        return format_jalali(self.date)

    @property
    def is_editable(self):
        return self.status == self.Status.DRAFT

    @property
    def status_css(self):
        return {"draft": "badge-draft", "final": "badge-final", "void": "badge-void"}[self.status]


class Entry(models.Model):
    """سطر سند: اثر مالی سند روی یک حساب مشخص در یک ارز مشخص.

    قاعده علامت: عدد مثبت یعنی بدهکار، عدد منفی یعنی بستانکار.
    جمع مبالغ هر سند، در هر ارز به طور جداگانه، باید صفر شود.
    """

    voucher = models.ForeignKey(Voucher, verbose_name="سند", on_delete=models.CASCADE,
                                related_name="entries")
    row_no = models.PositiveSmallIntegerField("ردیف", default=1)
    party = models.ForeignKey("core.Party", verbose_name="طرف حساب",
                              on_delete=models.PROTECT, related_name="entries")
    currency = models.ForeignKey("core.Currency", verbose_name="ارز",
                                 on_delete=models.PROTECT, related_name="entries")
    amount = models.DecimalField("مبلغ (+بدهکار / −بستانکار)", max_digits=28, decimal_places=8)
    rate_to_base = models.DecimalField(
        "نرخ قفل‌شده", max_digits=28, decimal_places=8, default=Decimal("1"),
        help_text="نرخ ارز در لحظه ثبت. بعداً تغییر نمی‌کند.",
    )
    description = models.CharField("شرح سطر", max_length=255, blank=True)

    # کپی تاریخ سند برای اینکه گزارش گردش حساب با یک جدول قابل مرتب‌سازی باشد
    date = models.DateField("تاریخ", db_index=True)

    class Meta:
        verbose_name = "سطر سند"
        verbose_name_plural = "سطرهای سند"
        ordering = ["voucher_id", "row_no", "id"]
        indexes = [
            models.Index(fields=["party", "currency", "date"]),
            models.Index(fields=["currency", "date"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(amount=0), name="entry_amount_not_zero",
            ),
        ]

    def __str__(self):
        return f"{self.party} / {self.currency.code} / {self.amount}"

    @property
    def debit(self):
        return self.amount if self.amount > 0 else Decimal("0")

    @property
    def credit(self):
        return -self.amount if self.amount < 0 else Decimal("0")


class Deal(models.Model):
    """جزئیات تجاری یک معامله خرید/فروش ارز.

    جدول Entry فقط می‌گوید چقدر بدهکار و چقدر بستانکار شد؛ این جدول می‌گوید
    «۸۳۰۰ درهم به نرخ ۵۱۲۰۰ فروخته شد» و سود همان معامله چقدر بود.
    """

    class Side(models.TextChoices):
        BUY = "buy", "خرید"
        SELL = "sell", "فروش"

    voucher = models.OneToOneField(Voucher, verbose_name="سند", on_delete=models.CASCADE,
                                   related_name="deal")
    side = models.CharField("نوع معامله", max_length=4, choices=Side.choices, db_index=True)
    counterparty = models.ForeignKey("core.Party", verbose_name="طرف معامله",
                                     on_delete=models.PROTECT, related_name="deals")
    currency = models.ForeignKey("core.Currency", verbose_name="ارز",
                                 on_delete=models.PROTECT, related_name="deals")

    quantity = models.DecimalField("تعداد ارز", max_digits=28, decimal_places=8,
                                   validators=[MinValueValidator(Decimal("0.00000001"))])
    unit_price = models.DecimalField("نرخ هر واحد", max_digits=28, decimal_places=8,
                                     validators=[MinValueValidator(Decimal("0.00000001"))])
    total_base = models.DecimalField("مبلغ کل", max_digits=28, decimal_places=8)

    avg_cost_at_time = models.DecimalField("میانگین بهای تمام‌شده در لحظه معامله",
                                           max_digits=28, decimal_places=8, default=Decimal("0"))
    realized_pnl = models.DecimalField("سود/زیان تحقق‌یافته", max_digits=28, decimal_places=8,
                                       default=Decimal("0"))

    date = models.DateField("تاریخ", db_index=True)

    class Meta:
        verbose_name = "معامله"
        verbose_name_plural = "معاملات"
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["currency", "side", "date"])]

    def __str__(self):
        return f"{self.get_side_display()} {self.quantity} {self.currency.code}"


class InventoryPosition(models.Model):
    """موجودی و میانگین بهای تمام‌شده هر ارز نزد صرافی.

    این تنها جایی است که یک عدد «انباشته» نگه می‌داریم، چون محاسبه میانگین
    موزون ذاتاً ترتیبی است. هر بار خرید، میانگین به‌روز می‌شود؛ هنگام فروش،
    سود از روی همین میانگین حساب می‌شود.
    """

    currency = models.OneToOneField("core.Currency", verbose_name="ارز",
                                    on_delete=models.CASCADE, related_name="position")
    quantity = models.DecimalField("موجودی", max_digits=28, decimal_places=8, default=Decimal("0"))
    avg_unit_cost = models.DecimalField("میانگین بهای هر واحد", max_digits=28, decimal_places=8,
                                        default=Decimal("0"))
    updated_at = models.DateTimeField("آخرین تغییر", auto_now=True)

    class Meta:
        verbose_name = "موقعیت ارزی"
        verbose_name_plural = "موقعیت‌های ارزی"

    def __str__(self):
        return f"{self.currency.code}: {self.quantity} @ {self.avg_unit_cost}"
