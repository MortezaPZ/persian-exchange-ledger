from django.contrib.auth.models import AbstractUser
from django.db import models

from .permissions import PERMISSION_CATALOG


class Permission(models.Model):
    """یک مجوز ریز (مثلاً «ابطال سند»)."""

    code = models.CharField("کد", max_length=64, unique=True)
    title = models.CharField("عنوان", max_length=128)
    group_title = models.CharField("دسته", max_length=64, blank=True)
    sort_order = models.PositiveIntegerField("ترتیب", default=0)

    class Meta:
        verbose_name = "مجوز"
        verbose_name_plural = "مجوزها"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.title

    @classmethod
    def sync_catalog(cls):
        """جدول مجوزها را با فهرست موجود در permissions.py هماهنگ می‌کند."""
        for order, (code, title, group_title) in enumerate(PERMISSION_CATALOG):
            cls.objects.update_or_create(
                code=code,
                defaults={"title": title, "group_title": group_title, "sort_order": order},
            )


class Role(models.Model):
    """نقش کاربری: مدیر، شریک، کارمند، ناظر و هر نقش دلخواه دیگر."""

    code = models.SlugField("کد", max_length=32, unique=True)
    title = models.CharField("عنوان نقش", max_length=64)
    description = models.CharField("توضیح", max_length=255, blank=True)
    permissions = models.ManyToManyField(
        Permission, verbose_name="مجوزها", blank=True, related_name="roles"
    )
    is_system = models.BooleanField("نقش سیستمی", default=False)

    class Meta:
        verbose_name = "نقش"
        verbose_name_plural = "نقش‌ها"
        ordering = ["id"]

    def __str__(self):
        return self.title

    def permission_codes(self):
        return set(self.permissions.values_list("code", flat=True))


class User(AbstractUser):
    """کاربر سامانه."""

    full_name = models.CharField("نام و نام خانوادگی", max_length=128, blank=True)
    role = models.ForeignKey(
        Role,
        verbose_name="نقش",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
    )
    phone = models.CharField("شماره تماس", max_length=32, blank=True)
    must_change_password = models.BooleanField(
        "باید رمز را عوض کند", default=False,
        help_text="برای کاربرانی که با رمز پیش‌فرض ساخته شده‌اند تا رمز را عوض نکرده‌اند.",
    )

    class Meta:
        verbose_name = "کاربر"
        verbose_name_plural = "کاربران"

    def __str__(self):
        return self.full_name or self.get_username()

    @property
    def display_name(self):
        return self.full_name or self.get_full_name() or self.get_username()

    def role_title(self):
        if self.is_superuser:
            return "مدیر ارشد"
        return self.role.title if self.role_id else "بدون نقش"

    def permission_codes(self):
        """مجموعه کدهای مجوز این کاربر (در طول یک درخواست کش می‌شود)."""
        if self.is_superuser:
            from .permissions import ALL_PERMISSION_CODES

            return set(ALL_PERMISSION_CODES)
        if not hasattr(self, "_perm_cache"):
            self._perm_cache = self.role.permission_codes() if self.role_id else set()
        return self._perm_cache

    def has_perm_code(self, code):
        return code in self.permission_codes()

    def has_any_perm(self, *codes):
        mine = self.permission_codes()
        return any(c in mine for c in codes)


class AuditLog(models.Model):
    """تاریخچه تغییرات: چه کسی، چه ساعتی، از چه آی‌پی، چه کاری کرد."""

    class Action(models.TextChoices):
        LOGIN = "login", "ورود به سیستم"
        LOGIN_FAILED = "login_failed", "ورود ناموفق"
        LOGOUT = "logout", "خروج از سیستم"
        CREATE = "create", "ایجاد"
        UPDATE = "update", "ویرایش"
        DELETE = "delete", "حذف"
        POST = "post", "قطعی کردن سند"
        VOID = "void", "ابطال سند"
        EXPORT = "export", "خروجی گرفتن"
        RATE_FETCH = "rate_fetch", "دریافت نرخ ارز"

    user = models.ForeignKey(
        User, verbose_name="کاربر", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="audit_logs",
    )
    username_snapshot = models.CharField("نام کاربری", max_length=150, blank=True)
    action = models.CharField("عملیات", max_length=24, choices=Action.choices)
    model_name = models.CharField("جدول", max_length=64, blank=True)
    object_id = models.CharField("شناسه رکورد", max_length=64, blank=True)
    summary = models.CharField("شرح", max_length=255, blank=True)
    ip_address = models.GenericIPAddressField("آی‌پی", null=True, blank=True)
    user_agent = models.CharField("مرورگر", max_length=255, blank=True)
    before = models.JSONField("مقدار قبل", null=True, blank=True)
    after = models.JSONField("مقدار بعد", null=True, blank=True)
    created_at = models.DateTimeField("زمان", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "تاریخچه تغییر"
        verbose_name_plural = "تاریخچه تغییرات"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["model_name", "object_id"]),
            models.Index(fields=["action", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.username_snapshot} — {self.get_action_display()}"
