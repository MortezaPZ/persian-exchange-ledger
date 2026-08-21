"""موتور ثبت سند.

قواعدی که این ماژول تضمین می‌کند:

  ۱) هر سند یا کامل ثبت می‌شود یا اصلاً ثبت نمی‌شود (تراکنش اتمیک).
  ۲) جمع مبالغ هر سند در هر ارز باید صفر شود، وگرنه چیزی ثبت نمی‌شود.
  ۳) سند قطعی هرگز ویرایش یا حذف نمی‌شود؛ اصلاح فقط با «سند برگشتی».
  ۴) کلید یکتا از ثبت دوباره یک رویداد جلوگیری می‌کند.
  ۵) هر ثبت در تاریخچه تغییرات ردپا می‌گذارد.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from accounts import services as audit
from accounts.models import AuditLog
from core.models import Currency, Party
from core.money import base_unit_label, format_amount, to_display
from core.rates import latest_rate

from .models import Deal, Entry, InventoryPosition, Sequence, Voucher

ZERO = Decimal("0")


class LedgerError(Exception):
    """خطای قابل نمایش به کاربر (پیام فارسی و مشخص)."""


class DuplicateVoucher(LedgerError):
    """این رویداد قبلاً با همین کلید یکتا ثبت شده است."""

    def __init__(self, voucher):
        self.voucher = voucher
        super().__init__(f"این سند قبلاً با شماره {voucher.number} ثبت شده است.")


@dataclass
class EntryLine:
    """یک سطر پیشنهادی برای سند، پیش از ثبت."""

    party: Party
    currency: Currency
    amount: Decimal          # مثبت = بدهکار، منفی = بستانکار
    description: str = ""
    rate_to_base: Optional[Decimal] = None


# --------------------------------------------------------------------------
# ابزارهای داخلی
# --------------------------------------------------------------------------
def _next_voucher_number():
    """شماره سند بعدی را زیر قفل پایگاه‌داده می‌گیرد."""
    seq, _created = Sequence.objects.get_or_create(key="voucher_number")
    seq = Sequence.objects.select_for_update().get(pk=seq.pk)
    seq.value = F("value") + 1
    seq.save(update_fields=["value"])
    seq.refresh_from_db(fields=["value"])
    return seq.value


def _assert_balanced(lines):
    """آزمون تراز: جمع هر ارز باید دقیقاً صفر باشد."""
    totals = {}
    for line in lines:
        totals[line.currency] = totals.get(line.currency, ZERO) + Decimal(line.amount)

    unbalanced = {c: t for c, t in totals.items() if t != ZERO}
    if unbalanced:
        detail = "، ".join(f"{c.name}: {t:+}" for c, t in unbalanced.items())
        raise LedgerError(
            "سند تراز نیست و ثبت نشد. جمع هر ارز باید صفر شود. اختلاف — " + detail
        )


def _resolve_rate(currency, when):
    if currency.is_base:
        return Decimal("1")
    rate = latest_rate(currency, at=when)
    return rate if rate is not None else ZERO


def describe(amount, currency):
    """مبلغ را همان‌طور که کاربر می‌بیند توصیف می‌کند (ارز پایه به واحد نمایش).

    شرح خودکار سند باید با چیزی که کاربر در فرم وارد کرده بخواند؛ اگر کاربر
    نرخ را به تومان زده، شرح هم باید تومان بنویسد نه ریال.
    """
    if currency.is_base:
        return f"{format_amount(to_display(amount), 0)} {base_unit_label()}"
    return f"{format_amount(amount, currency.decimal_places)} {currency.name}"


# --------------------------------------------------------------------------
# ثبت سند عمومی
# --------------------------------------------------------------------------
@transaction.atomic
def post_voucher(*, kind, date, lines, description="", created_by, external_key=None,
                 deal=None, reverses=None, audit_summary=None):
    """یک سند تراز شده را به صورت قطعی ثبت می‌کند.

    lines: فهرستی از EntryLine
    deal:  در صورت معامله بودن، دیکشنری جزئیات معامله
    خروجی: شیء Voucher قطعی‌شده
    """
    if not lines:
        raise LedgerError("سند بدون سطر قابل ثبت نیست.")

    if external_key:
        existing = Voucher.objects.filter(external_key=external_key).first()
        if existing:
            raise DuplicateVoucher(existing)

    _assert_balanced(lines)

    now = timezone.now()
    voucher = Voucher.objects.create(
        kind=kind,
        status=Voucher.Status.DRAFT,
        date=date,
        description=description,
        external_key=external_key or None,
        created_by=created_by,
        reverses=reverses,
    )

    entry_objects = []
    for index, line in enumerate(lines, start=1):
        amount = Decimal(line.amount)
        if amount == ZERO:
            raise LedgerError("سطر با مبلغ صفر مجاز نیست.")
        rate = line.rate_to_base if line.rate_to_base is not None else _resolve_rate(line.currency, now)
        entry_objects.append(
            Entry(
                voucher=voucher,
                row_no=index,
                party=line.party,
                currency=line.currency,
                amount=amount,
                rate_to_base=rate,
                description=line.description[:255],
                date=date,
            )
        )
    Entry.objects.bulk_create(entry_objects)

    if deal is not None:
        Deal.objects.create(voucher=voucher, date=date, **deal)

    # آزمون تراز، این بار از روی چیزی که واقعاً در پایگاه‌داده نوشته شد
    _verify_persisted_balance(voucher)

    voucher.number = _next_voucher_number()
    voucher.status = Voucher.Status.FINAL
    voucher.finalized_at = now
    voucher.save(update_fields=["number", "status", "finalized_at"])

    audit.log(
        AuditLog.Action.POST,
        summary=audit_summary or f"ثبت {voucher.get_kind_display()} شماره {voucher.number}",
        model_name="Voucher",
        object_id=voucher.pk,
        after={
            "number": voucher.number,
            "kind": voucher.kind,
            "date": str(voucher.date),
            "lines": [
                {"party": e.party.name, "currency": e.currency.code, "amount": str(e.amount)}
                for e in entry_objects
            ],
        },
        user=created_by,
    )
    return voucher


def _verify_persisted_balance(voucher):
    """تراز را از روی داده‌های ذخیره‌شده دوباره می‌سنجد.

    این بررسی دوم عمدی است: اگر روزی باگی در ساخت سطرها پیش بیاید، باز هم
    سند نامتراز وارد پایگاه‌داده نمی‌شود.
    """
    rows = (
        Entry.objects.filter(voucher=voucher)
        .values("currency_id")
        .annotate(total=Sum("amount"))
    )
    bad = [r for r in rows if r["total"] != ZERO]
    if bad:
        codes = {c.id: c.name for c in Currency.objects.filter(id__in=[r["currency_id"] for r in bad])}
        detail = "، ".join(f"{codes.get(r['currency_id'], r['currency_id'])}: {r['total']:+}" for r in bad)
        raise LedgerError("آزمون تراز پس از ثبت شکست خورد؛ سند لغو شد. اختلاف — " + detail)


# --------------------------------------------------------------------------
# معامله خرید/فروش ارز
# --------------------------------------------------------------------------
@transaction.atomic
def post_deal(*, side, date, counterparty, currency, quantity, unit_price,
              description="", created_by, external_key=None, settle_account=None,
              delivery_account=None):
    """ثبت یک معامله خرید یا فروش ارز.

    مثال فروش ۸۳۰۰ درهم به نرخ ۵۱٬۲۰۰ به آقای محمدی، چهار سطر می‌سازد:
        درهم : موقعیت ارزی درهم      بدهکار   ۸,۳۰۰
        درهم : علی محمدی           بستانکار ۸,۳۰۰
        ریال : علی محمدی           بدهکار   ۴۲۴,۹۶۰,۰۰۰
        ریال : موقعیت ارزی ریال      بستانکار ۴۲۴,۹۶۰,۰۰۰

    یعنی مشتری بابت ریال به ما بدهکار می‌شود و ما بابت درهم به او بدهکار
    می‌شویم — تا وقتی حواله را بفرستیم.

    اگر delivery_account داده شود، یعنی ارز همان لحظه تحویل داده/گرفته شده؛
    طرف ارزی به‌جای مشتری، آن صندوق می‌شود. اگر settle_account داده شود،
    یعنی ریال همان لحظه نقد شده؛ طرف ریالی به‌جای مشتری، آن بانک/صندوق
    می‌شود. هر کدام مستقل عمل می‌کنند.
    """
    base = Currency.objects.filter(is_base=True).first()
    if base is None:
        raise LedgerError("ارز پایه تعریف نشده است. ابتدا در بخش ارزها یک ارز را پایه کنید.")
    if currency.id == base.id:
        raise LedgerError("ارز معامله نمی‌تواند همان ارز پایه باشد.")

    quantity = Decimal(quantity)
    unit_price = Decimal(unit_price)
    if quantity <= ZERO:
        raise LedgerError("تعداد ارز باید بزرگ‌تر از صفر باشد.")
    if unit_price <= ZERO:
        raise LedgerError("نرخ باید بزرگ‌تر از صفر باشد.")
    if not counterparty.is_active:
        raise LedgerError(f"طرف حساب «{counterparty.name}» غیرفعال است.")
    if not currency.is_active:
        raise LedgerError(f"ارز «{currency.name}» غیرفعال است.")

    total_base = quantity * unit_price

    currency_position = Party.position_for(currency)
    base_position = Party.position_for(base)
    if settle_account is not None:
        if settle_account.currency_id != base.id:
            raise LedgerError("حساب تسویه باید از جنس ارز پایه باشد.")
        if not settle_account.is_active:
            raise LedgerError(f"حساب «{settle_account.name}» غیرفعال است.")

    # اگر ارز همان لحظه تحویل داده یا گرفته شود، طرف ارزی به صندوق/بانک ارزی
    # می‌خورد و موجودی واقعی کم یا زیاد می‌شود. اگر تحویل بعداً باشد (مثل
    # حواله درهم)، طرف ارزی روی حساب خود طرف معامله می‌ماند تا بعداً تسویه شود.
    if delivery_account is not None:
        if delivery_account.currency_id != currency.id:
            raise LedgerError(
                f"حساب تحویل باید از جنس {currency.name} باشد، "
                f"ولی «{delivery_account.name}» از جنس "
                f"{delivery_account.currency.name if delivery_account.currency_id else 'چندارزی'} است."
            )
        if not delivery_account.is_active:
            raise LedgerError(f"حساب «{delivery_account.name}» غیرفعال است.")

    # طرف ارزی و طرف ریالی هر کدام مستقل تصمیم می‌گیرند «همین حالا با یک حساب
    # واقعی تسویه شود» یا «روی حساب خود طرف معامله به صورت بدهی/طلب بماند».
    # base_position هیچ‌وقت عوض نمی‌شود — همیشه پل داخلی ریال است، دقیقاً مثل
    # currency_position برای طرف ارزی. قبلاً settle_account به‌جای این پل
    # می‌نشست که باعث می‌شد هم علامت حساب بانکی برعکس شود، هم مشتری همچنان
    # (اشتباهاً) بدهکار/بستانکار بماند با اینکه پول همان لحظه تسویه شده بود.
    currency_side = delivery_account or counterparty
    base_side = settle_account or counterparty

    if side == Deal.Side.SELL:
        lines = [
            EntryLine(currency_position, currency, quantity, "خروج ارز از موقعیت صرافی"),
            EntryLine(
                currency_side, currency, -quantity,
                "خروج ارز از صندوق" if delivery_account else "طلب ارزی مشتری بابت فروش",
            ),
            EntryLine(
                base_side, base, total_base,
                "ورود مبلغ به حساب تسویه" if settle_account else "بدهی ریالی مشتری بابت فروش ارز",
            ),
            EntryLine(base_position, base, -total_base, "معادل ریالی فروش"),
        ]
    elif side == Deal.Side.BUY:
        lines = [
            EntryLine(
                currency_side, currency, quantity,
                "ورود ارز به صندوق" if delivery_account else "بدهی ارزی طرف حساب بابت خرید",
            ),
            EntryLine(currency_position, currency, -quantity, "ورود ارز به موقعیت صرافی"),
            EntryLine(base_position, base, total_base, "معادل ریالی خرید"),
            EntryLine(
                base_side, base, -total_base,
                "خروج مبلغ از حساب تسویه" if settle_account else "طلب ریالی طرف حساب بابت خرید ارز",
            ),
        ]
    else:
        raise LedgerError("نوع معامله نامعتبر است.")

    # نرخ لحظه معامله روی سطرهای ارزی قفل می‌شود؛ سطرهای ارز پایه نرخ ۱ دارند.
    for line in lines:
        line.rate_to_base = unit_price if line.currency.id == currency.id else Decimal("1")

    avg_cost, realized = _apply_inventory(side, currency, quantity, unit_price)

    side_label = "فروش" if side == Deal.Side.SELL else "خرید"
    auto_desc = (
        f"{side_label} {describe(quantity, currency)} به نرخ {describe(unit_price, base)}"
        f" — جمع {describe(total_base, base)} — {counterparty.name}"
    )

    return post_voucher(
        kind=Voucher.Kind.DEAL,
        date=date,
        lines=lines,
        description=description or auto_desc,
        created_by=created_by,
        external_key=external_key,
        deal={
            "side": side,
            "counterparty": counterparty,
            "currency": currency,
            "quantity": quantity,
            "unit_price": unit_price,
            "total_base": total_base,
            "avg_cost_at_time": avg_cost,
            "realized_pnl": realized,
        },
        audit_summary=auto_desc,
    )


def _apply_inventory(side, currency, quantity, unit_price):
    """میانگین موزون بهای تمام‌شده را به‌روز می‌کند و سود فروش را برمی‌گرداند.

    روش: میانگین موزون. هر بار خرید، میانگین قیمت خریدها به‌روز می‌شود؛
    هنگام فروش، سود = (نرخ فروش − میانگین) × تعداد.
    ردیف موقعیت با select_for_update قفل می‌شود تا دو معامله هم‌زمان میانگین
    را خراب نکنند.
    """
    position, _created = InventoryPosition.objects.get_or_create(currency=currency)
    position = InventoryPosition.objects.select_for_update().get(pk=position.pk)

    old_qty = position.quantity
    old_avg = position.avg_unit_cost

    if side == Deal.Side.BUY:
        new_qty = old_qty + quantity
        if new_qty > ZERO:
            # اگر موجودی قبلی منفی بود (فروش استقراضی)، میانگین از نو ساخته می‌شود
            if old_qty <= ZERO:
                new_avg = unit_price
            else:
                new_avg = ((old_qty * old_avg) + (quantity * unit_price)) / new_qty
        else:
            new_avg = old_avg
        realized = ZERO
        avg_at_time = old_avg
    else:  # SELL — فروش میانگین را عوض نمی‌کند، فقط موجودی را کم می‌کند
        avg_at_time = old_avg if old_qty > ZERO else unit_price
        realized = (unit_price - avg_at_time) * quantity
        new_qty = old_qty - quantity
        new_avg = ZERO if new_qty == ZERO else old_avg

    position.quantity = new_qty
    position.avg_unit_cost = new_avg
    position.save(update_fields=["quantity", "avg_unit_cost", "updated_at"])
    return avg_at_time, realized


def _reverse_inventory(deal):
    """اثر یک معامله را روی موجودی و میانگین، دقیقاً برمی‌گرداند.

    برگرداندن فروش ساده است چون فروش میانگین را دست نزده بود: تعداد را
    اضافه می‌کنیم و میانگین سر جایش می‌ماند.

    برگرداندن خرید نیاز به «واکردن» میانگین دارد. اگر پیش از خرید موجودی q۰
    با میانگین m۰ داشتیم و q واحد به قیمت p خریدیم، میانگین جدید از رابطه
    m۱ = (q۰·m۰ + q·p) / (q۰+q) به دست آمده بود. پس معکوسش:
    m۰ = (q۱·m۱ − q·p) / (q۱−q)
    """
    position, _created = InventoryPosition.objects.get_or_create(currency=deal.currency)
    position = InventoryPosition.objects.select_for_update().get(pk=position.pk)

    qty, avg = position.quantity, position.avg_unit_cost

    if deal.side == Deal.Side.SELL:
        position.quantity = qty + deal.quantity
        if avg == ZERO:
            position.avg_unit_cost = deal.avg_cost_at_time
    else:  # BUY
        new_qty = qty - deal.quantity
        if new_qty > ZERO:
            position.avg_unit_cost = ((qty * avg) - (deal.quantity * deal.unit_price)) / new_qty
        else:
            position.avg_unit_cost = ZERO
        position.quantity = new_qty

    position.save(update_fields=["quantity", "avg_unit_cost", "updated_at"])


# --------------------------------------------------------------------------
# دریافت و پرداخت
# --------------------------------------------------------------------------
@transaction.atomic
def post_cash_movement(*, kind, date, party, account, currency, amount,
                       description="", created_by, external_key=None):
    """ثبت دریافت (واریز مشتری) یا پرداخت (حواله به مشتری).

    دریافت ۵۰٬۰۰۰٬۰۰۰ ریال به حساب بانک سامان:
        ریال : بانک سامان    بدهکار   ۵۰,۰۰۰,۰۰۰
        ریال : مشتری         بستانکار ۵۰,۰۰۰,۰۰۰
    """
    amount = Decimal(amount)
    if amount <= ZERO:
        raise LedgerError("مبلغ باید بزرگ‌تر از صفر باشد.")
    if not party.is_active:
        raise LedgerError(f"طرف حساب «{party.name}» غیرفعال است.")
    if not account.is_active:
        raise LedgerError(f"حساب «{account.name}» غیرفعال است.")
    if account.currency_id and account.currency_id != currency.id:
        raise LedgerError(
            f"حساب «{account.name}» از جنس {account.currency.name} است و با {currency.name} نمی‌خواند."
        )

    if kind == Voucher.Kind.RECEIVE:
        lines = [
            EntryLine(account, currency, amount, "ورود وجه"),
            EntryLine(party, currency, -amount, "بستانکار شدن بابت دریافت"),
        ]
        summary = f"دریافت {describe(amount, currency)} از {party.name} به {account.name}"
    elif kind == Voucher.Kind.PAY:
        lines = [
            EntryLine(party, currency, amount, "بدهکار شدن بابت پرداخت"),
            EntryLine(account, currency, -amount, "خروج وجه"),
        ]
        summary = f"پرداخت {describe(amount, currency)} به {party.name} از {account.name}"
    else:
        raise LedgerError("نوع سند برای این عملیات نامعتبر است.")

    return post_voucher(
        kind=kind,
        date=date,
        lines=lines,
        description=description or summary,
        created_by=created_by,
        external_key=external_key,
        audit_summary=summary,
    )


@transaction.atomic
def post_transfer(*, date, from_account, to_account, currency, amount,
                  description="", created_by, external_key=None):
    """انتقال داخلی بین حساب‌های خود صرافی (مثلاً از صندوق به بانک)."""
    amount = Decimal(amount)
    if amount <= ZERO:
        raise LedgerError("مبلغ باید بزرگ‌تر از صفر باشد.")
    if from_account.id == to_account.id:
        raise LedgerError("حساب مبدأ و مقصد نمی‌تواند یکی باشد.")

    lines = [
        EntryLine(to_account, currency, amount, "ورود به حساب مقصد"),
        EntryLine(from_account, currency, -amount, "خروج از حساب مبدأ"),
    ]
    summary = f"انتقال {describe(amount, currency)} از {from_account.name} به {to_account.name}"
    return post_voucher(
        kind=Voucher.Kind.TRANSFER,
        date=date,
        lines=lines,
        description=description or summary,
        created_by=created_by,
        external_key=external_key,
        audit_summary=summary,
    )


def _assert_expense_income_account(account):
    allowed = {Party.Kind.BANK, Party.Kind.CASHBOX, Party.Kind.CUSTOMER}
    if account.kind not in allowed:
        raise LedgerError("طرف حساب باید بانک، صندوق یا مشتری باشد.")


@transaction.atomic
def post_expense(*, date, category, account, currency, amount, description="",
                 created_by, external_key=None):
    """ثبت یک هزینه (اجاره، حقوق، قبض، …).

    مبلغ از یک بانک، صندوق، یا حساب مشتری خارج می‌شود و روی یک «دسته‌بندی
    هزینه» ثبت می‌گردد. مشتری وقتی انتخاب می‌شود که خودش هزینه را پرداخته
    باشد — در این حالت بستانکار می‌شود.
    مانده هر دسته‌بندی، جمع کل هزینه‌های همان دسته را نشان می‌دهد — مثلاً
    مانده «اجاره دفتر» یعنی روی‌هم چقدر بابت اجاره پرداخت شده.
    """
    amount = Decimal(amount)
    if amount <= ZERO:
        raise LedgerError("مبلغ باید بزرگ‌تر از صفر باشد.")
    _assert_expense_income_account(account)
    if not account.is_active:
        raise LedgerError(f"حساب «{account.name}» غیرفعال است.")
    if account.currency_id and account.currency_id != currency.id:
        raise LedgerError(f"حساب «{account.name}» از جنس {account.currency.name} است.")
    if not category.is_active:
        raise LedgerError(f"دسته‌بندی «{category.name}» غیرفعال است.")

    lines = [
        EntryLine(category, currency, amount, "ثبت هزینه"),
        EntryLine(account, currency, -amount, "خروج وجه بابت هزینه"),
    ]
    summary = f"هزینه «{category.name}» — {amount:g} {currency.name} از {account.name}"
    return post_voucher(
        kind=Voucher.Kind.EXPENSE, date=date, lines=lines,
        description=description or summary, created_by=created_by,
        external_key=external_key, audit_summary=summary,
    )


@transaction.atomic
def post_income(*, date, category, account, currency, amount, description="",
                created_by, external_key=None):
    """ثبت یک درآمد (کارمزد، سود جانبی، …) که ربطی به معامله خرید/فروش ندارد.

    وجه می‌تواند وارد بانک/صندوق شود، یا روی حساب یک مشتری بنشیند — اگر
    مشتری خودش درآمد را پرداخته یا دریافت کرده باشد.
    """
    amount = Decimal(amount)
    if amount <= ZERO:
        raise LedgerError("مبلغ باید بزرگ‌تر از صفر باشد.")
    _assert_expense_income_account(account)
    if not account.is_active:
        raise LedgerError(f"حساب «{account.name}» غیرفعال است.")
    if account.currency_id and account.currency_id != currency.id:
        raise LedgerError(f"حساب «{account.name}» از جنس {account.currency.name} است.")
    if not category.is_active:
        raise LedgerError(f"دسته‌بندی «{category.name}» غیرفعال است.")

    lines = [
        EntryLine(account, currency, amount, "ورود وجه بابت درآمد"),
        EntryLine(category, currency, -amount, "ثبت درآمد"),
    ]
    summary = f"درآمد «{category.name}» — {amount:g} {currency.name} به {account.name}"
    return post_voucher(
        kind=Voucher.Kind.INCOME, date=date, lines=lines,
        description=description or summary, created_by=created_by,
        external_key=external_key, audit_summary=summary,
    )


@transaction.atomic
def post_party_transfer(*, date, from_party, to_party, currency, amount,
                        description="", created_by, external_key=None):
    """انتقال حساب بین دو طرف حساب، بدون اینکه پولی از صندوق ما رد شود.

    نمونه‌ای که کارفرما گفته بود: مشتری «الف» فیشی را مستقیم به حساب مشتری
    «ب» واریز می‌کند. هیچ وجهی وارد بانک یا صندوق صرافی نمی‌شود، ولی بدهی و
    طلب این دو نفر نزد ما جابه‌جا می‌شود.

    از دید حسابداری: پرداخت‌کننده بستانکار می‌شود (طلبش از ما زیاد می‌شود) و
    دریافت‌کننده بدهکار.
    """
    amount = Decimal(amount)
    if amount <= ZERO:
        raise LedgerError("مبلغ باید بزرگ‌تر از صفر باشد.")
    if from_party.id == to_party.id:
        raise LedgerError("طرف پرداخت‌کننده و دریافت‌کننده نمی‌تواند یکی باشد.")
    if not from_party.is_active:
        raise LedgerError(f"طرف حساب «{from_party.name}» غیرفعال است.")
    if not to_party.is_active:
        raise LedgerError(f"طرف حساب «{to_party.name}» غیرفعال است.")

    lines = [
        EntryLine(to_party, currency, amount, f"دریافت از {from_party.name}"),
        EntryLine(from_party, currency, -amount, f"پرداخت به {to_party.name}"),
    ]
    summary = (
        f"انتقال {amount:g} {currency.name} از {from_party.name} به {to_party.name}"
    )
    return post_voucher(
        kind=Voucher.Kind.TRANSFER,
        date=date,
        lines=lines,
        description=description or summary,
        created_by=created_by,
        external_key=external_key,
        audit_summary=summary,
    )


@transaction.atomic
def post_opening_balance(*, date, party, currency, amount, created_by,
                         description="", external_key=None):
    """ثبت مانده افتتاحیه هنگام انتقال از گوگل‌شیت.

    amount مثبت یعنی طرف حساب به ما بدهکار است، منفی یعنی بستانکار.
    طرف مقابلِ سند، حساب «سرمایه/افتتاحیه» است تا سند تراز بماند.
    """
    amount = Decimal(amount)
    if amount == ZERO:
        raise LedgerError("مانده افتتاحیه صفر ثبت نمی‌شود.")

    equity, _created = Party.objects.get_or_create(
        kind=Party.Kind.EQUITY,
        code="OPENING",
        defaults={"name": "حساب افتتاحیه", "is_system": True},
    )

    lines = [
        EntryLine(party, currency, amount, "مانده افتتاحیه"),
        EntryLine(equity, currency, -amount, "طرف مقابل مانده افتتاحیه"),
    ]
    state = "بدهکار" if amount > ZERO else "بستانکار"
    summary = f"مانده افتتاحیه {party.name} — {describe(abs(amount), currency)} {state}"
    return post_voucher(
        kind=Voucher.Kind.OPENING,
        date=date,
        lines=lines,
        description=description or summary,
        created_by=created_by,
        external_key=external_key,
        audit_summary=summary,
    )


# --------------------------------------------------------------------------
# ابطال با سند برگشتی
# --------------------------------------------------------------------------
@transaction.atomic
def void_voucher(*, voucher, reason, created_by):
    """سند قطعی را با صدور «سند برگشتی» خنثی می‌کند.

    سند اصلی سر جایش می‌ماند و شماره‌اش هم عوض نمی‌شود؛ فقط وضعیتش «باطل»
    می‌شود و یک سند جدید با مبالغ قرینه ثبت می‌گردد. این‌طور هم اشتباه اصلاح
    می‌شود و هم ردپایش می‌ماند.
    """
    voucher = Voucher.objects.select_for_update().get(pk=voucher.pk)

    if voucher.status == Voucher.Status.VOID:
        raise LedgerError("این سند قبلاً باطل شده است.")
    if voucher.status != Voucher.Status.FINAL:
        raise LedgerError("فقط سند قطعی را می‌توان با سند برگشتی ابطال کرد.")
    if hasattr(voucher, "reversed_by"):
        raise LedgerError("برای این سند قبلاً سند برگشتی صادر شده است.")
    if not reason or not reason.strip():
        raise LedgerError("ثبت علت ابطال الزامی است.")

    original_entries = list(voucher.entries.select_related("party", "currency"))
    lines = [
        EntryLine(
            party=e.party,
            currency=e.currency,
            amount=-e.amount,
            description=f"برگشت سند {voucher.number}",
            rate_to_base=e.rate_to_base,
        )
        for e in original_entries
    ]

    # اثر معامله روی موجودی و میانگین بهای تمام‌شده هم باید برگردد
    deal = getattr(voucher, "deal", None)
    if deal is not None:
        _reverse_inventory(deal)

    reversal = post_voucher(
        kind=Voucher.Kind.ADJUST,
        date=voucher.date,
        lines=lines,
        description=f"سند برگشتی برای سند {voucher.number} — علت: {reason}",
        created_by=created_by,
        reverses=voucher,
        audit_summary=f"ابطال سند {voucher.number} — علت: {reason}",
    )

    voucher.status = Voucher.Status.VOID
    voucher.void_reason = reason[:255]
    voucher.save(update_fields=["status", "void_reason"])

    audit.log(
        AuditLog.Action.VOID,
        summary=f"ابطال سند {voucher.number} با سند برگشتی {reversal.number} — علت: {reason}",
        model_name="Voucher",
        object_id=voucher.pk,
        before={"status": Voucher.Status.FINAL},
        after={"status": Voucher.Status.VOID, "reversal_number": reversal.number},
        user=created_by,
    )
    return reversal


@transaction.atomic
def delete_voucher(*, voucher, reason, deleted_by):
    """سند را کاملاً از پایگاه‌داده حذف می‌کند.

    این کار برخلاف قاعده حسابداری است و عمداً فقط برای مدیر اصلی و برای دوره
    آزمایش باز شده است — کارفرما خواسته بود بتواند داده‌های آزمایشی را اصلاح
    کند و هر بار از اول وارد نکند.

    برخلاف «ابطال»، اینجا هیچ سند برگشتی ساخته نمی‌شود و ردی از خود سند
    نمی‌ماند؛ ولی خلاصه‌اش در تاریخچه تغییرات ثبت می‌شود تا دست‌کم معلوم باشد
    چه کسی چه چیزی را حذف کرده.
    """
    voucher = Voucher.objects.select_for_update().get(pk=voucher.pk)

    if hasattr(voucher, "reversed_by"):
        raise LedgerError(
            "برای این سند سند برگشتی صادر شده است؛ ابتدا سند برگشتی را حذف کنید."
        )
    if voucher.reverses_id is not None:
        raise LedgerError(
            "این سند خودش یک سند برگشتی است و جداگانه حذف نمی‌شود."
        )

    snapshot = {
        "number": voucher.number,
        "kind": voucher.kind,
        "date": str(voucher.date),
        "description": voucher.description,
        "lines": [
            {"party": e.party.name, "currency": e.currency.code, "amount": str(e.amount)}
            for e in voucher.entries.select_related("party", "currency")
        ],
    }

    # اثر معامله روی موجودی و میانگین بهای تمام‌شده باید برگردد
    deal = getattr(voucher, "deal", None)
    if deal is not None:
        _reverse_inventory(deal)

    number = voucher.number
    voucher.delete()  # سطرها و جزئیات معامله با cascade پاک می‌شوند

    audit.log(
        AuditLog.Action.DELETE,
        summary=f"حذف کامل سند {number} — علت: {reason}",
        model_name="Voucher",
        object_id=number,
        before=snapshot,
        user=deleted_by,
    )
    return snapshot
