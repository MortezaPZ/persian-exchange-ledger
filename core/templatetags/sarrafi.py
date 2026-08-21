"""فیلترها و تگ‌های قالب مخصوص این پروژه."""
from decimal import Decimal

from django import template
from django.utils.safestring import mark_safe

from core import jalali, money

register = template.Library()

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


@register.filter(name="fa")
def to_persian_digits(value):
    """ارقام لاتین را به فارسی تبدیل می‌کند."""
    if value is None:
        return ""
    return str(value).translate(PERSIAN_DIGITS)


@register.filter(name="jalali")
def jalali_filter(value, fmt="%Y/%m/%d"):
    return jalali.format_jalali(value, fmt).translate(PERSIAN_DIGITS)


@register.filter(name="jalali_long")
def jalali_long_filter(value):
    return jalali.format_jalali_long(value).translate(PERSIAN_DIGITS)


@register.filter(name="jalali_dt")
def jalali_datetime_filter(value):
    """تاریخ و ساعت شمسی، مثلاً ۱۴۰۵/۰۵/۱۷ ساعت ۱۴:۳۲"""
    if value is None:
        return ""
    from django.utils import timezone

    local = timezone.localtime(value) if timezone.is_aware(value) else value
    text = f"{jalali.format_jalali(local.date())} — {local.strftime('%H:%M')}"
    return text.translate(PERSIAN_DIGITS)


@register.filter(name="amount")
def amount_filter(value, decimal_places=0):
    """قالب‌بندی عدد با جداکننده هزارگان و ارقام فارسی."""
    if value is None:
        return "—"
    return money.format_amount(value, int(decimal_places)).translate(PERSIAN_DIGITS)


@register.simple_tag(name="topbar_amount")
def topbar_amount(row):
    """مبلغ فشرده برای نوار بالای صفحه — بدون اعشار اضافی."""
    currency = row["currency"]
    amount = row["amount"]
    if currency.is_base:
        return money.format_amount(money.to_display(amount), 0).translate(PERSIAN_DIGITS)
    return money.format_amount_compact(amount, currency.decimal_places).translate(PERSIAN_DIGITS)


@register.filter(name="base_amount")
def base_amount_filter(value):
    """مبلغ ریالی ذخیره‌شده را به واحد نمایش (تومان) تبدیل و قالب‌بندی می‌کند."""
    if value is None:
        return "—"
    return money.format_amount(money.to_display(value), 0).translate(PERSIAN_DIGITS)


@register.simple_tag(name="money_cell")
def money_cell(amount, currency):
    """یک سلول مبلغ با رنگ درست.

    قرمز = بدهکار (مثبت)، سبز = بستانکار (منفی) — همان چیزی که کارفرما خواست.
    """
    if amount is None:
        return mark_safe('<span class="muted">—</span>')

    # currency=None یعنی مبلغ به ارز پایه است (مثل جمع کل به ریال)، پس مثل
    # ارز پایه به واحد نمایش تبدیل می‌شود.
    amount = Decimal(amount)
    if currency is None or getattr(currency, "is_base", False):
        text = money.format_amount(money.to_display(abs(amount)), 0)
    else:
        text = money.format_amount(abs(amount), currency.decimal_places)
    text = text.translate(PERSIAN_DIGITS)

    if amount > 0:
        return mark_safe(f'<span class="amount debit">{text} <em>بدهکار</em></span>')
    if amount < 0:
        return mark_safe(f'<span class="amount credit">{text} <em>بستانکار</em></span>')
    return mark_safe(f'<span class="amount zero">{text}</span>')


@register.simple_tag(name="plain_money")
def plain_money(amount, currency):
    """مبلغ بدون برچسب بدهکار/بستانکار، ولی با علامت و رنگ."""
    if amount is None:
        return mark_safe('<span class="muted">—</span>')
    amount = Decimal(amount)
    if currency is None or getattr(currency, "is_base", False):
        text = money.format_amount(money.to_display(amount), 0)
    else:
        text = money.format_amount(amount, currency.decimal_places)
    text = text.translate(PERSIAN_DIGITS)
    css = "debit" if amount > 0 else ("credit" if amount < 0 else "zero")
    return mark_safe(f'<span class="amount {css}">{text}</span>')


@register.filter(name="unit_label")
def unit_label(currency):
    if currency is None:
        return ""
    if getattr(currency, "is_base", False):
        return money.base_unit_label()
    return currency.name


@register.filter(name="has_perm_code")
def has_perm_code(user, code):
    if not user or not user.is_authenticated:
        return False
    return user.has_perm_code(code)


@register.filter(name="abs_value")
def abs_value(value):
    try:
        return abs(Decimal(value))
    except Exception:
        return value


@register.simple_tag(takes_context=True)
def querystring_export(context, fmt):
    """آدرس گزارش فعلی به‌علاوه پارامتر خروجی — تا فیلترها حفظ شوند."""
    request = context.get("request")
    params = request.GET.copy() if request else None
    if params is None:
        return mark_safe(f"?export={fmt}")
    params["export"] = fmt
    return mark_safe("?" + params.urlencode())


@register.simple_tag(takes_context=True)
def query_string(context, **kwargs):
    """رشته کوئری فعلی را با تغییرات داده‌شده بازمی‌سازد (برای صفحه‌بندی)."""
    request = context.get("request")
    params = request.GET.copy() if request else {}
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode() if hasattr(params, "urlencode") else ""
    return mark_safe(f"?{encoded}" if encoded else "")
