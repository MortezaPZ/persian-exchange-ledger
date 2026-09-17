from decimal import Decimal

from django import forms
from django.db.models import Sum

from core.fields import (AmountField, BaseAmountField, BaseCurrencyAmountMixin,
                         CurrencyChoiceField, JalaliDateField)
from core.models import Currency, Party

from .models import Deal, Entry, Voucher, InvoiceSettlement, InvoiceDelivery

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


class SettlementForm(forms.ModelForm):
    class Meta:
        model = InvoiceSettlement
        fields = ("payment", "amount", "note")
        widgets = {"payment": forms.Select(attrs=SELECT), "note": forms.Textarea(attrs=TEXTAREA)}

    def __init__(self, *args, deal=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.deal = deal
        # فقط جمع سطرهای ارز پایه همین مشتری؛ نه علامت حساب مقابل.
        customer_entries = (
            Entry.objects.filter(party=deal.counterparty, currency__is_base=True)
            .order_by().values("voucher_id").annotate(total=Sum("amount"))
        )
        customer_entries = customer_entries.filter(
            **({"total__lt": 0} if deal.side == Deal.Side.SELL else {"total__gt": 0})
        )
        # سندهایی که واقعاً بدهی/طلب همین طرف حساب را کم می‌کنند:
        #  - دریافت/پرداخت: تسویه فاکتور معمولاً با ارز پایه (ریال) است، نه ارز
        #    معامله؛ محدودکردن فهرست به ارزِ معامله باعث می‌شد لیست همیشه خالی
        #    بماند و بخش «تخصیص» عملاً دیده نشود (شکایت کارفرما).
        #  - انتقال بین مشتریان: بخش بزرگی از اسناد کارفرما از این نوع است و
        #    مثل دریافت/پرداخت فقط بدهی دو مشتری را جابه‌جا می‌کند.
        self.fields["payment"].queryset = (
            Voucher.objects.filter(
                kind__in=[Voucher.Kind.RECEIVE, Voucher.Kind.PAY, Voucher.Kind.TRANSFER],
                status=Voucher.Status.FINAL,
                pk__in=customer_entries.values("voucher_id"),
            )
            .exclude(reverses__isnull=False)
            .select_related("created_by")
            .order_by("-date", "-number")
        )

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount > self.deal.remaining_amount:
            raise forms.ValidationError("مبلغ تخصیص از مانده فاکتور بیشتر است.")
        return amount

    def clean(self):
        cleaned = super().clean()
        if not Voucher.objects.filter(
            pk=self.deal.voucher_id, status=Voucher.Status.FINAL,
            kind=Voucher.Kind.DEAL, reverses__isnull=True,
        ).exists():
            raise forms.ValidationError("تخصیص فقط برای فاکتور قطعی مجاز است.")
        invoice_base = Entry.objects.filter(
            voucher_id=self.deal.voucher_id, party=self.deal.counterparty,
            currency__is_base=True,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        if (self.deal.side == Deal.Side.SELL and invoice_base <= 0) or (
            self.deal.side == Deal.Side.BUY and invoice_base >= 0
        ):
            raise forms.ValidationError("این فاکتور بدهی یا طلب قابل تسویه در ارز پایه ندارد.")
        payment, amount = cleaned.get("payment"), cleaned.get("amount")
        if payment and InvoiceSettlement.objects.filter(
            deal=self.deal, payment=payment,
        ).exclude(pk=self.instance.pk).exists():
            self.add_error("payment", "این سند قبلا به همین فاکتور تخصیص داده شده است.")
            return cleaned

        if payment and amount is not None:
            signed_base = Entry.objects.filter(
                voucher=payment, party=self.deal.counterparty, currency__is_base=True,
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            allocated = InvoiceSettlement.objects.filter(
                payment=payment, deal__counterparty=self.deal.counterparty,
                deal__voucher__status=Voucher.Status.FINAL,
            ).exclude(pk=self.instance.pk).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            if amount > abs(signed_base) - allocated:
                self.add_error("amount", "مبلغ تخصیص از مانده قابل تخصیص سند مالی بیشتر است.")
        return cleaned


class DeliveryForm(forms.ModelForm):
    """تخصیص تحویل ارز معامله — کاملاً مستقل از تسویه ریالی."""

    class Meta:
        model = InvoiceDelivery
        fields = ("payment", "quantity", "note")
        widgets = {"payment": forms.Select(attrs=SELECT), "note": forms.Textarea(attrs=TEXTAREA)}

    def __init__(self, *args, deal=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.deal = deal
        # فقط سندهایی که ارزِ معامله را به نفع مشتری جابه‌جا می‌کنند:
        #  - فروش: مشتری طلبکار ارز است، پس واریز/انتقالِ مثبت (بدهکار کردن مشتری)
        #  - خرید: مشتری بدهکار ارز است، پس برداشت/انتقالِ منفی (بستانکار شدن مشتری)
        counterparty_entries = (
            Entry.objects.filter(party=deal.counterparty, currency=deal.currency)
            .order_by().values("voucher_id").annotate(total=Sum("amount"))
        )
        counterparty_entries = counterparty_entries.filter(
            **({"total__gt": 0} if deal.side == Deal.Side.SELL else {"total__lt": 0})
        )
        self.fields["payment"].queryset = (
            Voucher.objects.filter(
                kind__in=[Voucher.Kind.RECEIVE, Voucher.Kind.PAY, Voucher.Kind.TRANSFER],
                status=Voucher.Status.FINAL,
                pk__in=counterparty_entries.values("voucher_id"),
            )
            .exclude(reverses__isnull=False)
            .select_related("created_by")
            .order_by("-date", "-number")
        )

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if quantity > self.deal.remaining_delivery_quantity:
            raise forms.ValidationError("مقدار تحویل از مانده تحویل فاکتور بیشتر است.")
        return quantity

    def clean(self):
        cleaned = super().clean()
        if not Voucher.objects.filter(
            pk=self.deal.voucher_id, status=Voucher.Status.FINAL,
            kind=Voucher.Kind.DEAL, reverses__isnull=True,
        ).exists():
            raise forms.ValidationError("تخصیص تحویل فقط برای فاکتور قطعی مجاز است.")
        obligation = self.deal.delivery_obligation
        if self.deal.currency.is_base or (
            self.deal.side == Deal.Side.SELL and obligation >= 0
        ) or (self.deal.side == Deal.Side.BUY and obligation <= 0):
            raise forms.ValidationError("این فاکتور تعهد تحویل ارز قابل تخصیص ندارد.")
        payment = cleaned.get("payment")
        quantity = cleaned.get("quantity")
        if payment and InvoiceDelivery.objects.filter(
            deal=self.deal, payment=payment,
        ).exclude(pk=self.instance.pk).exists():
            self.add_error("payment", "این سند قبلا به همین فاکتور تخصیص داده شده است.")
            return cleaned

        if payment and quantity is not None:
            signed_total = Entry.objects.filter(
                voucher=payment, party=self.deal.counterparty, currency=self.deal.currency,
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
            allocated = InvoiceDelivery.objects.filter(
                payment=payment, deal__counterparty=self.deal.counterparty,
                deal__currency=self.deal.currency,
                deal__voucher__status=Voucher.Status.FINAL,
            ).exclude(pk=self.instance.pk).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
            if quantity > abs(signed_total) - allocated:
                self.add_error("quantity", "مقدار تحویل از مانده قابل تخصیص سند مالی بیشتر است.")
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
