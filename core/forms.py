from decimal import Decimal

from django import forms

from .fields import AmountField, BaseAmountField
from .models import Currency, FxRate, Party, RateSource, RateSourceMapping

INPUT = {"class": "input"}


class PartyForm(forms.ModelForm):
    class Meta:
        model = Party
        fields = ["kind", "name", "code", "currency", "phone", "national_id",
                  "telegram_id", "whatsapp_no", "is_active", "note"]
        labels = {
            "kind": "نوع حساب", "name": "نام", "code": "کد حساب", "currency": "ارز حساب",
            "phone": "شماره تماس", "national_id": "کد ملی / شناسه",
            "telegram_id": "شناسه تلگرام", "whatsapp_no": "شماره واتس‌اپ",
            "is_active": "فعال", "note": "توضیحات",
        }
        help_texts = {
            "telegram_id": "برای مرحله ۴ (ربات). اگر خالی باشد ربات به این مشتری پاسخ نمی‌دهد.",
            "whatsapp_no": "با کد کشور، مثلاً 989121234567",
            "is_active": "مشتری هیچ‌وقت پاک نمی‌شود؛ فقط غیرفعال می‌شود تا سوابقش بماند.",
        }
        widgets = {
            "kind": forms.Select(attrs=INPUT),
            "name": forms.TextInput(attrs=INPUT),
            "code": forms.TextInput(attrs={**INPUT, "dir": "ltr"}),
            "currency": forms.Select(attrs=INPUT),
            "phone": forms.TextInput(attrs={**INPUT, "dir": "ltr"}),
            "national_id": forms.TextInput(attrs={**INPUT, "dir": "ltr"}),
            "telegram_id": forms.TextInput(attrs={**INPUT, "dir": "ltr"}),
            "whatsapp_no": forms.TextInput(attrs={**INPUT, "dir": "ltr"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "note": forms.Textarea(attrs={**INPUT, "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"].queryset = Currency.objects.filter(is_active=True).order_by(
            "sort_order", "code"
        )
        self.fields["currency"].empty_label = "— برای مشتری خالی بماند —"
        if self.instance.pk and self.instance.is_system:
            self.fields["kind"].disabled = True
            self.fields["currency"].disabled = True

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        currency = cleaned.get("currency")
        needs_currency = {Party.Kind.BANK, Party.Kind.CASHBOX, Party.Kind.POSITION}
        if kind in needs_currency and not currency:
            self.add_error("currency", "برای بانک، صندوق و موقعیت ارزی، انتخاب ارز الزامی است.")
        if kind == Party.Kind.CUSTOMER and currency:
            self.add_error("currency", "حساب مشتری چندارزی است؛ ارز را خالی بگذارید.")
        return cleaned


class CurrencyForm(forms.ModelForm):
    class Meta:
        model = Currency
        fields = ["code", "name", "symbol", "decimal_places", "is_base", "is_active", "sort_order"]
        labels = {
            "code": "کد", "name": "نام", "symbol": "نماد", "decimal_places": "تعداد اعشار",
            "is_base": "ارز پایه", "is_active": "فعال", "sort_order": "ترتیب نمایش",
        }
        widgets = {
            "code": forms.TextInput(attrs={**INPUT, "dir": "ltr"}),
            "name": forms.TextInput(attrs=INPUT),
            "symbol": forms.TextInput(attrs={**INPUT, "dir": "ltr"}),
            "decimal_places": forms.NumberInput(attrs=INPUT),
            "is_base": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "sort_order": forms.NumberInput(attrs=INPUT),
        }

    def clean_is_base(self):
        value = self.cleaned_data["is_base"]
        if value:
            existing = Currency.objects.filter(is_base=True).exclude(pk=self.instance.pk).first()
            if existing:
                raise forms.ValidationError(
                    f"«{existing.name}» در حال حاضر ارز پایه است. ابتدا آن را از حالت پایه خارج کنید."
                )
        elif self.instance.pk and self.instance.is_base:
            raise forms.ValidationError(
                "سیستم بدون ارز پایه کار نمی‌کند. ابتدا ارز دیگری را پایه کنید."
            )
        return value


class ManualRateForm(forms.Form):
    """ورود دستی نرخ ارز."""

    currency = forms.ModelChoiceField(
        label="ارز", queryset=Currency.objects.none(),
        widget=forms.Select(attrs=INPUT), empty_label="— انتخاب کنید —",
    )
    rate = BaseAmountField(label="نرخ", min_value=Decimal("0.00000001"))
    note = forms.CharField(label="توضیح", required=False, widget=forms.TextInput(attrs=INPUT))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"].queryset = Currency.objects.filter(
            is_active=True, is_base=False
        ).order_by("sort_order", "code")


class RateSourceForm(forms.ModelForm):
    class Meta:
        model = RateSource
        fields = ["title", "kind", "url", "api_key", "timeout_seconds", "is_active", "note"]
        labels = {
            "title": "عنوان", "kind": "نوع", "url": "آدرس سرویس", "api_key": "کلید API",
            "timeout_seconds": "مهلت پاسخ (ثانیه)", "is_active": "فعال", "note": "توضیح",
        }
        widgets = {
            "title": forms.TextInput(attrs=INPUT),
            "kind": forms.Select(attrs=INPUT),
            "url": forms.URLInput(attrs={**INPUT, "dir": "ltr"}),
            "api_key": forms.TextInput(attrs={**INPUT, "dir": "ltr"}),
            "timeout_seconds": forms.NumberInput(attrs=INPUT),
            "is_active": forms.CheckboxInput(attrs={"class": "checkbox"}),
            "note": forms.TextInput(attrs=INPUT),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("kind") == RateSource.Kind.HTTP_JSON and not cleaned.get("url"):
            self.add_error("url", "برای سرویس اینترنتی، آدرس الزامی است.")
        return cleaned


class RateSourceMappingForm(forms.ModelForm):
    class Meta:
        model = RateSourceMapping
        fields = ["currency", "json_path", "multiplier"]
        labels = {"currency": "ارز", "json_path": "مسیر مقدار در پاسخ", "multiplier": "ضریب تبدیل"}
        widgets = {
            "currency": forms.Select(attrs=INPUT),
            "json_path": forms.TextInput(attrs={**INPUT, "dir": "ltr",
                                                "placeholder": "aed.value"}),
            "multiplier": forms.NumberInput(attrs={**INPUT, "dir": "ltr", "step": "any"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"].queryset = Currency.objects.filter(
            is_active=True, is_base=False
        ).order_by("sort_order", "code")
