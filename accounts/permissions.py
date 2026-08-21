"""فهرست مجوزهای ریز سیستم و نقش‌های پیش‌فرض.

هر مجوز یک «کد» یکتا دارد که در کد برنامه با آن کار می‌کنیم و یک «عنوان فارسی»
که در صفحه مدیریت نقش‌ها به کاربر نشان داده می‌شود. اضافه کردن یک مجوز جدید
فقط یک سطر در این فایل است؛ نیازی به تغییر منطق برنامه نیست.
"""


class Perm:
    # اسناد و تراکنش‌ها
    VOUCHER_ADD = "voucher.add"
    VOUCHER_VIEW = "voucher.view"
    VOUCHER_VOID = "voucher.void"
    #: ویرایش و حذف کامل سند — فقط برای مدیر اصلی
    VOUCHER_EDIT = "voucher.edit"

    # نگهداری سیستم
    BACKUP_MANAGE = "backup.manage"
    #: پاک کردن کل اسناد برای شروع دوباره — خطرناک‌ترین مجوز سیستم
    DATA_RESET = "data.reset"

    # طرف حساب‌ها
    PARTY_VIEW = "party.view"
    PARTY_MANAGE = "party.manage"

    # ارز و نرخ
    CURRENCY_MANAGE = "currency.manage"
    FXRATE_MANAGE = "fxrate.manage"

    # گزارش‌ها
    REPORT_STATEMENT = "report.statement"
    REPORT_DAILY = "report.daily"
    REPORT_PROFIT = "report.profit"
    REPORT_TRIAL = "report.trial"

    # ربات
    BOT_MANAGE = "bot.manage"

    # مدیریت
    USER_MANAGE = "user.manage"
    AUDIT_VIEW = "audit.view"


#: (کد، عنوان فارسی، دسته)
PERMISSION_CATALOG = [
    (Perm.VOUCHER_ADD, "ثبت سند و معامله", "اسناد"),
    (Perm.VOUCHER_VIEW, "مشاهده اسناد", "اسناد"),
    (Perm.VOUCHER_VOID, "ابطال سند", "اسناد"),
    (Perm.VOUCHER_EDIT, "ویرایش و حذف سند (مدیر اصلی)", "اسناد"),
    (Perm.BACKUP_MANAGE, "پشتیبان‌گیری و بازگردانی", "نگهداری"),
    (Perm.DATA_RESET, "پاک کردن کل اسناد (مدیر اصلی)", "نگهداری"),
    (Perm.PARTY_VIEW, "مشاهده طرف حساب‌ها", "طرف حساب"),
    (Perm.PARTY_MANAGE, "تعریف و ویرایش طرف حساب", "طرف حساب"),
    (Perm.CURRENCY_MANAGE, "مدیریت ارزها", "ارز و نرخ"),
    (Perm.FXRATE_MANAGE, "ثبت و به‌روزرسانی نرخ ارز", "ارز و نرخ"),
    (Perm.REPORT_STATEMENT, "گزارش گردش حساب", "گزارش‌ها"),
    (Perm.REPORT_DAILY, "گزارش خرید و فروش روزانه", "گزارش‌ها"),
    (Perm.REPORT_PROFIT, "گزارش سود و زیان تسعیر", "گزارش‌ها"),
    (Perm.REPORT_TRIAL, "گزارش تراز کل و وضعیت بانک‌ها", "گزارش‌ها"),
    (Perm.BOT_MANAGE, "مدیریت ربات تلگرام و واتس‌اپ", "ربات"),
    (Perm.USER_MANAGE, "مدیریت کاربران و نقش‌ها", "مدیریت"),
    (Perm.AUDIT_VIEW, "مشاهده تاریخچه تغییرات", "مدیریت"),
]

ALL_PERMISSION_CODES = [code for code, _title, _group in PERMISSION_CATALOG]

#: نقش‌های پیش‌فرضی که هنگام راه‌اندازی ساخته می‌شوند.
DEFAULT_ROLES = {
    "admin": {
        "title": "مدیر",
        "description": "دسترسی کامل به همه بخش‌ها، شامل مدیریت کاربران.",
        "permissions": ALL_PERMISSION_CODES,
    },
    "partner": {
        "title": "شریک",
        "description": "همه دسترسی‌ها به جز مدیریت کاربران و کارهای خطرناک نگهداری.",
        "permissions": [
            c for c in ALL_PERMISSION_CODES
            if c not in {Perm.USER_MANAGE, Perm.VOUCHER_EDIT, Perm.DATA_RESET}
        ],
    },
    "employee": {
        "title": "کارمند",
        "description": "ثبت معامله و مشاهده گردش حساب؛ بدون دسترسی به سود و مدیریت.",
        "permissions": [
            Perm.VOUCHER_ADD,
            Perm.VOUCHER_VIEW,
            Perm.PARTY_VIEW,
            Perm.PARTY_MANAGE,
            Perm.FXRATE_MANAGE,
            Perm.REPORT_STATEMENT,
            Perm.REPORT_DAILY,
        ],
    },
    "viewer": {
        "title": "ناظر",
        "description": "فقط مشاهده؛ امکان ثبت یا تغییر هیچ سندی را ندارد.",
        "permissions": [
            Perm.VOUCHER_VIEW,
            Perm.PARTY_VIEW,
            Perm.REPORT_STATEMENT,
        ],
    },
}
