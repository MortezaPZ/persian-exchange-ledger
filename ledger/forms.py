from decimal import Decimal

from django import forms

from core.fields import (AmountField, BaseAmountField, BaseCurrencyAmountMixin,
                         CurrencyChoiceField, JalaliDateField)
from core.models import Currency, Party

from .models import Deal, Voucher

SELECT = {"class": "input"}
TEXTAREA = {"class": "input", "rows": 2}


def _customer_queryset():
    return Party.objects.filter(kind=Party.Kind.CUSTOMER, is_active=True).order_by("name")


def _house_account_queryset(currency=None):
    qs = Party.objects.filter(
        kind__in=[Party.Kind.BANK, Party.Kind.CASHBOX], is_active=True
    ).select_related("currency")
    if currency is not None:
        qs = qs.filter(currency=currency)
    return qs.order_by("kind", "name")


def _tradable_currency_queryset():
    return Currency.objects.filter(is_active=True, is_base=False).order_by("sort_order", "code")


class DealForm(forms.Form):
    """فرم ثبت معامله خرید/فروش ارز — صفحه اصلی کار کاربر."""

    date = JalaliDateField(label="تاریخ", default_today=True)
    counterparty = forms.ModelChoiceField(
        label="طرف حساب", queryset=Party.objects.none(),
        widget=forms.Select(attrs={**SELECT, "data-searchable": "1"}),
        empty_label="— انتخاب کنید —",
    )
    side = forms.ChoiceField(
        label="نوع معامله", choices=Deal.Side.choices,
        widget=forms.RadioSelect(attrs={"class": "radio"}), initial=Deal.Side.SELL,
    )
    currency = forms.ModelChoiceField(
        label="ارز", queryset=Currency.objects.none(),
        widget=forms.Select(attrs=SELECT), empty_label="— انتخاب کنید —",
    )
    quantity = AmountField(label="تعداد ارز", min_value=Decimal("0.00000001"), decimal_places=2)
    unit_price = BaseAmountField(
        label="نرخ هر واحد", min_value=Decimal("0.00000001"), required=False,
        help_text="اگر مبلغ کل را وارد کنید، نرخ خودکار حساب می‌شود.",
    )
    total_amount = BaseAmountField(
        label="مبلغ کل (اختیاری)", min_value=Decimal("0.00000001"), required=False,
        help_text="برای وقتی که مبلغ کل را رند کرده‌اید؛ نرخ از روی آن حساب می‌شود.",
    )
    delivery_account = forms.ModelChoiceField(
        label="صندوق تحویل ارز (اختیاری)", queryset=Party.objects.none(), required=False,
        widget=forms.Select(attrs=SELECT), empty_label="— تحویل نشده (روی حساب طرف می‌ماند) —",
        help_text="اگر ارز همین حالا تحویل داده یا گرفته شد، صندوق ارزی را انتخاب کنید "
                  "تا موجودی کم یا زیاد شود.",
    )
    settle_account = forms.ModelChoiceField(
        label="حساب تسویه ریالی (اختیاری)", queryset=Party.objects.none(), required=False,
        widget=forms.Select(attrs=SELECT), empty_label="— تسویه نشده (روی حساب طرف می‌ماند) —",
        help_text="اگر مبلغ ریالی همان لحظه نقد شد، بانک یا صندوق مربوطه را انتخاب کنید.",
    )
    description = forms.CharField(
        label="شرح", required=False, widget=forms.Textarea(attrs=TEXTAREA),
        help_text="خالی بگذارید تا خودکار پر شود.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["counterparty"].queryset = _customer_queryset()
        self.fields["currency"].queryset = _tradable_currency_queryset()
        base = Currency.objects.filter(is_base=True).first()
        self.fields["settle_account"].queryset = _house_account_queryset(currency=base)
        # صندوق تحویل باید فقط صندوق‌های ارزی (غیر پایه) را نشان بدهد
        self.fields["delivery_account"].queryset = _house_account_queryset().exclude(
            currency=base
        )

    def clean_quantity(self):
        value = self.cleaned_data["quantity"]
        if value is None or value <= 0:
            raise forms.ValidationError("تعداد ارز باید بزرگ‌تر از صفر باشد.")
        return value

    def clean(self):
        """نرخ یا مبلغ کل — هر کدام داده شد، دیگری از رویش حساب می‌شود.

        کارفرما گفته بود گاهی مبلغ کل را رند می‌کند و می‌خواهد نرخ خودکار
        درآید. پس هر دو راه باز است، ولی دست‌کم یکی باید پر باشد.
        """
        cleaned = super().clean()
        quantity = cleaned.get("quantity")
        unit_price = cleaned.get("unit_price")
        total = cleaned.get("total_amount")

        if not quantity:
            return cleaned

        if unit_price and total:
            # اگر هر دو داده شد، مبلغ کل حرف آخر را می‌زند (کاربر رندش کرده)
            cleaned["unit_price"] = total / quantity
        elif total:
            cleaned["unit_price"] = total / quantity
        elif not unit_price:
            self.add_error("unit_price", "نرخ هر واحد یا مبلغ کل را وارد کنید.")

        price = cleaned.get("unit_price")
        if price is not None and price <= 0:
            self.add_error("unit_price", "نرخ باید بزرگ‌تر از صفر باشد.")

        currency = cleaned.get("currency")
        delivery = cleaned.get("delivery_account")
        if delivery and currency and delivery.currency_id != currency.id:
            self.add_error(
                "delivery_account",
                f"صندوق تحویل باید از جنس {currency.name} باشد.",
            )
        return cleaned

    @property
    def total_base(self):
        qty = self.cleaned_data.get("quantity")
        price = self.cleaned_data.get("unit_price")
        if qty and price:
            return qty * price
        return None


class CashMovementForm(BaseCurrencyAmountMixin, forms.Form):
    """فرم دریافت (واریز مشتری) و پرداخت (حواله به مشتری)."""

    date = JalaliDateField(label="تاریخ", default_today=True)
    party = forms.ModelChoiceField(
        label="طرف حساب", queryset=Party.objects.none(),
        widget=forms.Select(attrs=SELECT), empty_label="— انتخاب کنید —",
    )
    currency = CurrencyChoiceField(
        label="ارز", queryset=Currency.objects.none(),
        widget=forms.Select(attrs=SELECT), empty_label="— انتخاب کنید —",
    )
    account = forms.ModelChoiceField(
        label="بانک / صندوق", queryset=Party.objects.none(),
        widget=forms.Select(attrs=SELECT), empty_label="— انتخاب کنید —",
    )
    amount = AmountField(
        label="مبلغ", min_value=Decimal("0.00000001"), decimal_places=2,
        help_text="مبلغ را به واحد همان ارزی که بالا انتخاب کرده‌اید وارد کنید.",
    )
    description = forms.CharField(
        label="شرح", required=False, widget=forms.Textarea(attrs=TEXTAREA),
        help_text="مثلاً: واریز فیش پایا به حساب بانک سامان، ساعت ۲۳:۱۷",
    )

    def __init__(self, *args, **kwargs):
        self.kind = kwargs.pop("kind", Voucher.Kind.RECEIVE)
        super().__init__(*args, **kwargs)
        self.fields["party"].queryset = _customer_queryset()
        self.fields["currency"].queryset = Currency.objects.filter(is_active=True).order_by(
            "sort_order", "code"
        )
        self.fields["account"].queryset = _house_account_queryset()

    def clean(self):
        cleaned = super().clean()
        account = cleaned.get("account")
        currency = cleaned.get("currency")
        if account and currency and account.currency_id and account.currency_id != currency.id:
            self.add_error(
                "account",
                f"حساب «{account.name}» از جنس {account.currency.name} است "
                f"و با ارز انتخابی ({currency.name}) نمی‌خواند.",
            )
        return cleaned


class TransferForm(BaseCurrencyAmountMixin, forms.Form):
    """انتقال بین حساب‌های خود صرافی."""

    date = JalaliDateField(label="تاریخ", default_today=True)
    currency = CurrencyChoiceField(
        label="ارز", queryset=Currency.objects.none(),
        widget=forms.Select(attrs=SELECT), empty_label="— انتخاب کنید —",
    )
    from_account = forms.ModelChoiceField(
        label="از حساب", queryset=Party.objects.none(),
        widget=forms.Select(attrs=SELECT), empty_label="— انتخاب کنید —",
    )
    to_account = forms.ModelChoiceField(
        label="به حساب", queryset=Party.objects.none(),
        widget=forms.Select(attrs=SELECT), empty_label="— انتخاب کنید —",
    )
    amount = AmountField(
        label="مبلغ", min_value=Decimal("0.00000001"), decimal_places=2,
        help_text="مبلغ را به واحد همان ارزی که بالا انتخاب کرده‌اید وارد کنید.",
    )
    description = forms.CharField(label="شرح", required=False,
                                  widget=forms.Textarea(attrs=TEXTAREA))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"].queryset = Currency.objects.filter(is_active=True).order_by(
            "sort_order", "code"
        )
        accounts = _house_account_queryset()
        self.fields["from_account"].queryset = accounts
        self.fields["to_account"].queryset = accounts

    def clean(self):
        cleaned = super().clean()
        src, dst = cleaned.get("from_account"), cleaned.get("to_account")
        currency = cleaned.get("currency")
        if src and dst and src.id == dst.id:
            self.add_error("to_account", "حساب مبدأ و مقصد نمی‌تواند یکی باشد.")
        for field, account in (("from_account", src), ("to_account", dst)):
            if account and currency and account.currency_id and account.currency_id != currency.id:
                self.add_error(field, f"حساب «{account.name}» از جنس {account.currency.name} است.")
        return cleaned


class OpeningBalanceForm(BaseCurrencyAmountMixin, forms.Form):
    """ثبت مانده افتتاحیه هنگام انتقال از گوگل‌شیت."""

    date = JalaliDateField(label="تاریخ افتتاحیه", default_today=True)
    party = forms.ModelChoiceField(
        label="طرف حساب", queryset=Party.objects.none(),
        widget=forms.Select(attrs=SELECT), empty_label="— انتخاب کنید —",
    )
    currency = CurrencyChoiceField(
        label="ارز", queryset=Currency.objects.none(),
        widget=forms.Select(attrs=SELECT), empty_label="— انتخاب کنید —",
    )
    direction = forms.ChoiceField(
        label="وضعیت", choices=[("debit", "بدهکار است (به ما بدهکار)"),
                                ("credit", "بستانکار است (ما به او بدهکار)")],
        widget=forms.RadioSelect(attrs={"class": "radio"}), initial="credit",
    )
    amount = AmountField(
        label="مبلغ", min_value=Decimal("0.00000001"), decimal_places=2,
        help_text="مبلغ را به واحد همان ارزی که بالا انتخاب کرده‌اید وارد کنید.",
    )
    description = forms.CharField(label="شرح", required=False,
                                  widget=forms.Textarea(attrs=TEXTAREA))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["party"].queryset = Party.objects.filter(is_active=True).order_by("kind", "name")
        self.fields["currency"].queryset = Currency.objects.filter(is_active=True).order_by(
            "sort_order", "code"
        )

    def signed_amount(self):
        amount = self.cleaned_data["amount"]
        return amount if self.cleaned_data["direction"] == "debit" else -amount


class VoidForm(forms.Form):
    reason = forms.CharField(
        label="علت ابطال",
        widget=forms.Textarea(attrs={"class": "input", "rows": 3,
                                     "placeholder": "مثلاً: نرخ اشتباه وارد شده بود"}),
        max_length=255,
    )
    confirm = forms.BooleanField(
        label="می‌دانم که سند اصلی پاک نمی‌شود و یک سند برگشتی صادر خواهد شد.",
        widget=forms.CheckboxInput(attrs={"class": "checkbox"}),
    )


class VoucherFilterForm(forms.Form):
    q = forms.CharField(label="جستجو", required=False,
                        widget=forms.TextInput(attrs={"class": "input",
                                                      "placeholder": "شرح، شماره سند یا نام طرف حساب"}))
    kind = forms.ChoiceField(label="نوع سند", required=False,
                             choices=[("", "همه")] + list(Voucher.Kind.choices),
                             widget=forms.Select(attrs=SELECT))
    status = forms.ChoiceField(label="وضعیت", required=False,
                               choices=[("", "همه")] + list(Voucher.Status.choices),
                               widget=forms.Select(attrs=SELECT))
    date_from = JalaliDateField(label="از تاریخ", required=False)
    date_to = JalaliDateField(label="تا تاریخ", required=False)


class PartyTransferForm(BaseCurrencyAmountMixin, forms.Form):
    """انتقال حساب بین دو مشتری، بدون اینکه پولی از صندوق ما رد شود."""

    date = JalaliDateField(label="تاریخ", default_today=True)
    currency = CurrencyChoiceField(
        label="ارز", queryset=Currency.objects.none(),
        widget=forms.Select(attrs=SELECT), empty_label="— انتخاب کنید —",
    )
    from_party = forms.ModelChoiceField(
        label="از حساب (پرداخت‌کننده)", queryset=Party.objects.none(),
        widget=forms.Select(attrs={**SELECT, "data-searchable": "1"}),
        empty_label="— انتخاب کنید —",
    )
    to_party = forms.ModelChoiceField(
        label="به حساب (دریافت‌کننده)", queryset=Party.objects.none(),
        widget=forms.Select(attrs={**SELECT, "data-searchable": "1"}),
        empty_label="— انتخاب کنید —",
    )
    amount = AmountField(
        label="مبلغ", min_value=Decimal("0.00000001"), decimal_places=2,
        help_text="مبلغ را به واحد همان ارزی که بالا انتخاب کرده‌اید وارد کنید.",
    )
    description = forms.CharField(label="شرح", required=False,
                                  widget=forms.Textarea(attrs=TEXTAREA))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"].queryset = Currency.objects.filter(is_active=True).order_by(
            "sort_order", "code"
        )
        parties = _customer_queryset()
        self.fields["from_party"].queryset = parties
        self.fields["to_party"].queryset = parties

    def clean(self):
        cleaned = super().clean()
        src, dst = cleaned.get("from_party"), cleaned.get("to_party")
        if src and dst and src.id == dst.id:
            self.add_error("to_party", "پرداخت‌کننده و دریافت‌کننده نمی‌تواند یکی باشد.")
        return cleaned


class ExpenseIncomeForm(BaseCurrencyAmountMixin, forms.Form):
    """ثبت هزینه یا درآمد.

    «دسته‌بندی» یک طرف حساب از نوع هزینه یا درآمد است (مثل «اجاره دفتر» یا
    «کارمزد بانکی»)؛ از همان صفحه «طرف حساب‌ها» با نوع مناسب تعریف می‌شود.
    """

    date = JalaliDateField(label="تاریخ", default_today=True)
    category = forms.ModelChoiceField(
        label="دسته‌بندی", queryset=Party.objects.none(),
        widget=forms.Select(attrs=SELECT), empty_label="— انتخاب کنید —",
        help_text="اگر دسته‌بندی مناسب نبود، از «طرف حساب‌ها» یکی جدید بسازید.",
    )
    currency = CurrencyChoiceField(
        label="ارز", queryset=Currency.objects.none(),
        widget=forms.Select(attrs=SELECT), empty_label="— انتخاب کنید —",
    )
    account = forms.ModelChoiceField(
        label="بانک / صندوق / مشتری", queryset=Party.objects.none(),
        widget=forms.Select(attrs={**SELECT, "data-searchable": "1"}),
        empty_label="— انتخاب کنید —",
        help_text="اگر هزینه را مشتری پرداخت کرده یا درآمد به حساب مشتری رفته، همان مشتری را انتخاب کنید.",
    )
    amount = AmountField(
        label="مبلغ", min_value=Decimal("0.00000001"), decimal_places=2,
        help_text="مبلغ را به واحد همان ارزی که بالا انتخاب کرده‌اید وارد کنید.",
    )
    description = forms.CharField(label="شرح", required=False,
                                  widget=forms.Textarea(attrs=TEXTAREA))

    def __init__(self, *args, **kwargs):
        self.kind = kwargs.pop("kind", Voucher.Kind.EXPENSE)
        super().__init__(*args, **kwargs)
        category_kind = (
            Party.Kind.EXPENSE if self.kind == Voucher.Kind.EXPENSE else Party.Kind.INCOME
        )
        self.fields["category"].queryset = Party.objects.filter(
            kind=category_kind, is_active=True
        ).order_by("name")
        self.fields["currency"].queryset = Currency.objects.filter(is_active=True).order_by(
            "sort_order", "code"
        )
        self.fields["account"].queryset = Party.objects.filter(
            kind__in=[Party.Kind.BANK, Party.Kind.CASHBOX, Party.Kind.CUSTOMER],
            is_active=True,
        ).select_related("currency").order_by("kind", "name")
        if self.kind == Voucher.Kind.EXPENSE:
            self.fields["account"].label = "پرداخت از (بانک / صندوق / مشتری)"
        else:
            self.fields["account"].label = "دریافت در (بانک / صندوق / مشتری)"

    def clean(self):
        cleaned = super().clean()
        account = cleaned.get("account")
        currency = cleaned.get("currency")
        if account and currency and account.currency_id and account.currency_id != currency.id:
            self.add_error(
                "account",
                f"حساب «{account.name}» از جنس {account.currency.name} است "
                f"و با ارز انتخابی ({currency.name}) نمی‌خواند.",
            )
        return cleaned
