from django.db import models


class BotConfig(models.Model):
    """تنظیمات هر پیام‌رسان.

    توکن‌ها اینجا ذخیره می‌شوند تا کاربر بتواند بدون دست زدن به کد، ربات را
    راه‌اندازی یا خاموش کند.
    """

    class Platform(models.TextChoices):
        TELEGRAM = "telegram", "تلگرام"
        WHATSAPP = "whatsapp", "واتس‌اپ"

    platform = models.CharField("پیام‌رسان", max_length=16, choices=Platform.choices, unique=True)
    is_enabled = models.BooleanField("فعال", default=False)

    token = models.CharField(
        "توکن ربات", max_length=255, blank=True,
        help_text="تلگرام: توکنی که BotFather می‌دهد. واتس‌اپ: توکن دسترسی سرویس‌دهنده.",
    )
    phone_number_id = models.CharField(
        "شناسه شماره فرستنده", max_length=64, blank=True,
        help_text="فقط واتس‌اپ — شناسه شماره‌ای که پیام از آن ارسال می‌شود.",
    )
    api_base = models.CharField(
        "آدرس پایه سرویس", max_length=255, blank=True,
        help_text="خالی بگذارید تا آدرس پیش‌فرض استفاده شود.",
    )
    webhook_secret = models.CharField(
        "کلید امنیتی وب‌هوک", max_length=128, blank=True,
        help_text="برای تأیید اینکه پیام واقعاً از سرویس‌دهنده آمده است.",
    )

    #: آخرین شناسه به‌روزرسانی که از تلگرام گرفته‌ایم (برای دریافت پیوسته)
    last_update_id = models.BigIntegerField("آخرین شناسه دریافت", default=0)

    updated_at = models.DateTimeField("آخرین تغییر", auto_now=True)

    class Meta:
        verbose_name = "تنظیمات ربات"
        verbose_name_plural = "تنظیمات ربات‌ها"

    def __str__(self):
        return self.get_platform_display()

    @property
    def is_ready(self):
        """آیا حداقل تنظیمات لازم برای کار کردن را دارد؟"""
        if not self.is_enabled or not self.token:
            return False
        if self.platform == self.Platform.WHATSAPP:
            return bool(self.phone_number_id)
        return True


class BotMessage(models.Model):
    """تاریخچه پیام‌های رد و بدل شده با ربات.

    اگر مشتری گفت «من پرسیدم و جواب ندادید»، سابقه‌اش اینجاست.
    """

    class Direction(models.TextChoices):
        IN = "in", "دریافتی"
        OUT = "out", "ارسالی"

    class Status(models.TextChoices):
        OK = "ok", "موفق"
        UNKNOWN_SENDER = "unknown", "فرستنده ناشناس"
        NOT_UNDERSTOOD = "unparsed", "دستور نامفهوم"
        FAILED = "failed", "خطا"

    platform = models.CharField("پیام‌رسان", max_length=16, choices=BotConfig.Platform.choices,
                                db_index=True)
    direction = models.CharField("جهت", max_length=4, choices=Direction.choices, db_index=True)
    status = models.CharField("وضعیت", max_length=12, choices=Status.choices,
                              default=Status.OK, db_index=True)

    sender_id = models.CharField("شناسه فرستنده", max_length=64, blank=True, db_index=True)
    party = models.ForeignKey(
        "core.Party", verbose_name="طرف حساب", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="bot_messages",
    )
    text = models.TextField("متن پیام", blank=True)
    error = models.CharField("خطا", max_length=255, blank=True)

    #: شناسه یکتای پیام در سمت پیام‌رسان — برای جلوگیری از پردازش دوباره
    external_id = models.CharField("شناسه پیام", max_length=128, blank=True, db_index=True)

    created_at = models.DateTimeField("زمان", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "پیام ربات"
        verbose_name_plural = "پیام‌های ربات"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["platform", "-created_at"]),
            models.Index(fields=["sender_id", "-created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "external_id"],
                condition=models.Q(direction="in") & ~models.Q(external_id=""),
                name="uniq_incoming_bot_message",
            )
        ]

    def __str__(self):
        return f"{self.get_platform_display()} — {self.get_direction_display()}"
