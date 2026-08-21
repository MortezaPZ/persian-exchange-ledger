"""ابزارهای کار با مبالغ.

قاعده‌ها:
  • همه مبالغ ارز پایه در پایگاه‌داده به «ریال» ذخیره می‌شوند.
  • اگر تنظیم DISPLAY_UNIT برابر "toman" باشد، فقط در لحظه نمایش تقسیم بر ۱۰
    و در لحظه ورودی گرفتن ضرب در ۱۰ می‌شوند. یعنی تغییر واحد نمایش هیچ
    داده‌ای را دست نمی‌زند.
  • برای پول هرگز از float استفاده نمی‌کنیم؛ فقط Decimal.
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.conf import settings

from .jalali import normalize_digits

TOMAN_FACTOR = Decimal("10")
ZERO = Decimal("0")


def display_unit():
    return getattr(settings, "DISPLAY_UNIT", "rial").lower()


def base_unit_label():
    return "تومان" if display_unit() == "toman" else "ریال"


def to_display(amount):
    """مبلغ ریالی ذخیره‌شده را به واحد نمایش تبدیل می‌کند."""
    if amount is None:
        return None
    amount = Decimal(amount)
    if display_unit() == "toman":
        return amount / TOMAN_FACTOR
    return amount


def from_display(amount):
    """مبلغ وارد شده توسط کاربر را به ریال (واحد ذخیره‌سازی) تبدیل می‌کند."""
    if amount is None:
        return None
    amount = Decimal(amount)
    if display_unit() == "toman":
        return amount * TOMAN_FACTOR
    return amount


def parse_amount(value):
    """رشته‌ای مثل «۴۲۴,۹۶۰,۰۰۰» یا «424.960.000» را به Decimal تبدیل می‌کند.

    جداکننده هزارگان می‌تواند ویرگول فارسی/لاتین یا نقطه باشد؛ در فایل کارفرما
    هر سه شکل دیده شده. اگر نقطه به عنوان جداکننده هزارگان به کار رفته باشد
    (یعنی بعد از آن دقیقاً سه رقم آمده و بیش از یک نقطه هست) نقطه‌ها حذف
    می‌شوند، وگرنه نقطه به عنوان ممیز اعشار در نظر گرفته می‌شود.
    """
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))

    text = normalize_digits(value).strip()
    text = text.replace("٫", ".").replace("،", ",").replace(" ", "").replace("‌", "")
    if not text:
        return None

    negative = text.startswith("-")
    text = text.lstrip("+-")
    text = text.replace(",", "")

    if text.count(".") > 1:
        text = text.replace(".", "")
    elif text.count(".") == 1:
        whole, frac = text.split(".")
        # «424.960» در فایل کارفرما یعنی ۴۲۴۹۶۰، نه ۴۲۴٫۹۶
        if len(frac) == 3 and whole and not whole.startswith("0"):
            text = whole + frac

    if not text or not text.replace(".", "").isdigit():
        raise ValueError(f"عدد نامعتبر است: {value}")

    try:
        result = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"عدد نامعتبر است: {value}")
    return -result if negative else result


def quantize(amount, decimal_places=2):
    if amount is None:
        return None
    exp = Decimal(1).scaleb(-int(decimal_places))
    return Decimal(amount).quantize(exp, rounding=ROUND_HALF_UP)


def format_amount_compact(amount, decimal_places=2):
    """مثل format_amount، ولی اگر عدد رُند باشد اعشار را نشان نمی‌دهد.

    برای جاهایی مثل نوار بالای صفحه که جا تنگ است: «۸,۳۰۰» به جای «۸,۳۰۰٫۰۰».
    """
    if amount is None:
        return "—"
    amount = Decimal(amount)
    places = 0 if amount == amount.to_integral_value() else decimal_places
    return format_amount(amount, places)


def format_amount(amount, decimal_places=0, with_sign=False):
    """قالب‌بندی عدد با جداکننده هزارگان."""
    if amount is None:
        return "—"
    value = quantize(Decimal(amount), decimal_places)
    negative = value < 0
    value = abs(value)

    whole = int(value)
    formatted = f"{whole:,}"
    if decimal_places > 0:
        frac = (value - whole) * (10 ** decimal_places)
        formatted += "." + str(int(frac.to_integral_value(ROUND_HALF_UP))).rjust(decimal_places, "0")

    if negative:
        return ("−" if not with_sign else "-") + formatted
    if with_sign and whole:
        return "+" + formatted
    return formatted
