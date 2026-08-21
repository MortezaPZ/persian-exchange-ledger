"""فیلدهای فرم مخصوص فارسی: تاریخ شمسی و مبلغ با جداکننده هزارگان."""
from decimal import Decimal

from django import forms

from .jalali import format_jalali, parse_jalali, today_jalali_str
from .money import (base_unit_label, format_amount, from_display, parse_amount,
                    to_display)


class JalaliDateInput(forms.TextInput):
    def __init__(self, attrs=None):
        base = {
            "class": "input date-input",
            "dir": "ltr",
            "inputmode": "numeric",
            "placeholder": "۱۴۰۵/۰۵/۱۷",
            "autocomplete": "off",
        }
        base.update(attrs or {})
        super().__init__(base)

    def format_value(self, value):
        if value in (None, ""):
            return ""
        if hasattr(value, "year"):
            return format_jalali(value)
        return str(value)


class JalaliDateField(forms.Field):
    """تاریخ را شمسی می‌گیرد و میلادی تحویل می‌دهد."""

    widget = JalaliDateInput

    def __init__(self, *args, default_today=False, **kwargs):
        self.default_today = default_today
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        if value in (None, "") and self.default_today:
            return today_jalali_str()
        if hasattr(value, "year"):
            return format_jalali(value)
        return value

    def to_python(self, value):
        if value in (None, "", []):
            return None
        try:
            return parse_jalali(value)
        except ValueError as exc:
            raise forms.ValidationError(str(exc))


class AmountInput(forms.TextInput):
    def __init__(self, attrs=None):
        base = {
            "class": "input amount-input",
            "dir": "ltr",
            "inputmode": "decimal",
            "autocomplete": "off",
        }
        base.update(attrs or {})
        super().__init__(base)


class AmountField(forms.Field):
    """مبلغ را با هر شکل نگارشی می‌پذیرد: ۴۲۴,۹۶۰,۰۰۰ یا 424.960.000 یا 424960000."""

    widget = AmountInput

    def __init__(self, *args, min_value=None, decimal_places=2, **kwargs):
        self.min_value = min_value
        self.decimal_places = decimal_places
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        if value in (None, ""):
            return value
        if isinstance(value, (Decimal, int, float)):
            return format_amount(value, self.decimal_places)
        return value

    def to_python(self, value):
        if value in (None, "", []):
            return None
        try:
            result = parse_amount(value)
        except ValueError as exc:
            raise forms.ValidationError(str(exc))
        if result is None:
            return None
        if self.min_value is not None and result < self.min_value:
            raise forms.ValidationError(f"مقدار نباید کمتر از {self.min_value} باشد.")
        return result


class BaseAmountField(AmountField):
    """مبلغ ارز پایه: ورودی به واحد نمایش (تومان) گرفته و به ریال تبدیل می‌شود.

    فقط برای فیلدهایی که *همیشه* ارز پایه‌اند (مثل «نرخ هر واحد»). برای
    فیلدهایی که ارزشان در همان فرم انتخاب می‌شود، از BaseCurrencyAmountMixin
    استفاده کنید.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("decimal_places", 0)
        super().__init__(*args, **kwargs)

    def prepare_value(self, value):
        if isinstance(value, (Decimal, int, float)):
            return format_amount(to_display(value), self.decimal_places)
        return super().prepare_value(value)

    def to_python(self, value):
        result = super().to_python(value)
        return from_display(result) if result is not None else None


class CurrencyChoiceField(forms.ModelChoiceField):
    """فهرست ارزها که برای ارز پایه، واحد نمایش را نشان می‌دهد.

    نام ارز پایه در پایگاه‌داده «ریال» است، ولی وقتی برنامه در حالت تومان کار
    می‌کند نباید در فهرست «ریال» بنویسد و مبلغ را تومان بگیرد — این دقیقاً
    همان چیزی است که کاربر را به اشتباه می‌اندازد.
    """

    def label_from_instance(self, obj):
        return base_unit_label() if obj.is_base else obj.name


class BaseCurrencyAmountMixin:
    """تبدیل مبلغ ارز پایه از واحد نمایش به واحد ذخیره‌سازی.

    فرم‌های دریافت، پرداخت، انتقال و افتتاحیه چندارزی‌اند: همان فیلد «مبلغ»
    ممکن است درهم باشد یا تومان. پس تبدیل نمی‌تواند داخل خود فیلد انجام شود و
    باید بعد از معلوم شدن ارز انجام گیرد.

    بدون این تبدیل، کاربر عدد را به تومان وارد می‌کند ولی سیستم آن را ریال
    حساب می‌کند و مانده ده برابر کمتر از واقعیت می‌شود.
    """

    amount_field_name = "amount"
    currency_field_name = "currency"

    def clean(self):
        cleaned = super().clean()
        currency = cleaned.get(self.currency_field_name)
        amount = cleaned.get(self.amount_field_name)
        if currency is not None and amount is not None and currency.is_base:
            cleaned[self.amount_field_name] = from_display(amount)
        return cleaned
