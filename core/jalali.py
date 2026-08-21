"""ابزارهای تاریخ شمسی.

در پایگاه‌داده تاریخ به صورت میلادی ذخیره می‌شود (تا مرتب‌سازی و بازه‌گیری
درست کار کند) و در تمام رابط کاربری شمسی نشان داده و گرفته می‌شود.
"""
import datetime
import re

import jdatetime

_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_PERSIAN_MAP = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_SEPARATORS = re.compile(r"[/\-\.\s]+")

JALALI_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]


def normalize_digits(value):
    """ارقام فارسی/عربی را به لاتین تبدیل می‌کند."""
    if value is None:
        return ""
    return str(value).translate(_DIGIT_MAP)


def persian_digits(value):
    """ارقام لاتین را به فارسی تبدیل می‌کند — برای پیام‌هایی که به کاربر نشان داده می‌شود."""
    if value is None:
        return ""
    return str(value).translate(_PERSIAN_MAP)


def parse_jalali(value):
    """رشته تاریخ شمسی را به datetime.date میلادی تبدیل می‌کند.

    فرمت‌های پذیرفته‌شده: ۱۴۰۵/۰۵/۱۷ و 1405-05-17 و 17.05.1405 و 1405.05.17
    (فرمت نقطه‌ای «روز.ماه.سال» همان چیزی است که در گوگل‌شیت کارفرما استفاده شده.)
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.datetime):
        return value.date()

    text = normalize_digits(value).strip()
    if not text:
        return None

    parts = [p for p in _SEPARATORS.split(text) if p]
    if len(parts) != 3:
        raise ValueError(f"فرمت تاریخ نامعتبر است: {value}")

    try:
        nums = [int(p) for p in parts]
    except ValueError:
        raise ValueError(f"فرمت تاریخ نامعتبر است: {value}")

    # تشخیص اینکه سال اول آمده یا آخر
    if nums[0] > 1000:
        year, month, day = nums
    elif nums[2] > 1000:
        day, month, year = nums
    else:
        raise ValueError(f"سال در تاریخ مشخص نیست: {value}")

    if not (1 <= month <= 12 and 1 <= day <= 31):
        raise ValueError(f"روز یا ماه خارج از بازه است: {value}")

    try:
        return jdatetime.date(year, month, day).togregorian()
    except Exception as exc:
        raise ValueError(f"تاریخ شمسی نامعتبر است: {value}") from exc


def to_jalali(value):
    """datetime.date میلادی را به jdatetime.date تبدیل می‌کند."""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        value = value.date()
    return jdatetime.date.fromgregorian(date=value)


def format_jalali(value, fmt="%Y/%m/%d"):
    j = to_jalali(value)
    return j.strftime(fmt) if j else ""


def format_jalali_long(value):
    """مثال خروجی: ۱۷ مرداد ۱۴۰۵"""
    j = to_jalali(value)
    if not j:
        return ""
    return f"{j.day} {JALALI_MONTHS[j.month - 1]} {j.year}"


def today_jalali_str():
    return jdatetime.date.today().strftime("%Y/%m/%d")


def today_gregorian():
    return datetime.date.today()


def month_start_gregorian():
    """اولین روز ماه شمسی جاری، به میلادی."""
    j = jdatetime.date.today()
    return jdatetime.date(j.year, j.month, 1).togregorian()


def year_start_gregorian():
    """اولین روز سال شمسی جاری، به میلادی."""
    j = jdatetime.date.today()
    return jdatetime.date(j.year, 1, 1).togregorian()
