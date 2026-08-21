"""محاسبه مانده‌ها.

اصل طراحی: هیچ مانده‌ای هیچ‌جا ذخیره نمی‌شود. هر بار که مانده لازم باشد، از
جمع سطرهای سند در همان لحظه حساب می‌شود. به همین دلیل امکان ندارد مانده با
گردش حساب اختلاف داشته باشد — همان مشکلی که در گوگل‌شیت وجود دارد.

نکته درباره اسناد باطل: وقتی سندی باطل می‌شود، سطرهایش پاک نمی‌شود؛ یک سند
برگشتی با مبالغ قرینه صادر می‌گردد. پس هر دو سند در محاسبه می‌آیند و اثرشان
روی هم صفر می‌شود. فقط پیش‌نویس‌ها کنار گذاشته می‌شوند.
"""
from decimal import Decimal

from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce

from core.models import Currency, Party
from core.rates import latest_rate_map

from .models import Entry, Voucher

ZERO = Decimal("0")


def posted_entries():
    """سطرهایی که واقعاً اثر مالی دارند (پیش‌نویس‌ها حساب نمی‌شوند)."""
    return Entry.objects.exclude(voucher__status=Voucher.Status.DRAFT)


def _apply_date_filter(qs, date_from=None, date_to=None):
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    return qs


def party_balances(party, *, as_of=None, include_zero=False):
    """مانده یک طرف حساب به تفکیک ارز.

    خروجی: فهرستی از دیکشنری‌ها با کلیدهای currency و amount
    amount مثبت = بدهکار، منفی = بستانکار
    """
    qs = _apply_date_filter(posted_entries().filter(party=party), date_to=as_of)
    rows = (
        qs.values("currency")
        .annotate(amount=Coalesce(Sum("amount"), Value(ZERO), output_field=DecimalField(max_digits=28, decimal_places=8)))
        .order_by("currency__sort_order", "currency__code")
    )
    currencies = {c.id: c for c in Currency.objects.all()}
    result = []
    for row in rows:
        if row["amount"] == ZERO and not include_zero:
            continue
        result.append({"currency": currencies[row["currency"]], "amount": row["amount"]})
    return result


def party_balances_map(party, *, as_of=None):
    """نگاشت {id ارز: مانده} برای یک طرف حساب."""
    return {b["currency"].id: b["amount"] for b in party_balances(party, as_of=as_of)}


def bulk_party_balances(parties=None, *, kind=None, as_of=None, only_nonzero=True):
    """مانده همه طرف حساب‌ها را با یک پرس‌وجو حساب می‌کند.

    خروجی: {id طرف حساب: {id ارز: مانده}}
    """
    qs = posted_entries()
    if parties is not None:
        qs = qs.filter(party__in=parties)
    if kind is not None:
        qs = qs.filter(party__kind=kind)
    qs = _apply_date_filter(qs, date_to=as_of)

    rows = qs.values("party", "currency").annotate(amount=Sum("amount"))

    result = {}
    for row in rows:
        if only_nonzero and row["amount"] == ZERO:
            continue
        result.setdefault(row["party"], {})[row["currency"]] = row["amount"]
    return result


def value_in_base(balances_by_currency, currencies_by_id, rate_map):
    """ارزش ریالی مجموعه‌ای از مانده‌های ارزی را با نرخ روز حساب می‌کند.

    اگر نرخ ارزی موجود نباشد آن ارز در جمع نمی‌آید و در فهرست missing گزارش
    می‌شود — چون نشان دادن عدد نادرست بدتر از نشان ندادن است.
    """
    total = ZERO
    missing = []
    for currency_id, amount in balances_by_currency.items():
        currency = currencies_by_id.get(currency_id)
        if currency is None:
            continue
        if currency.is_base:
            total += amount
            continue
        rate = rate_map.get(currency_id)
        if rate is None:
            if amount != ZERO:
                missing.append(currency)
            continue
        total += amount * rate
    return total, missing


def party_balance_view(party, *, as_of=None):
    """مانده یک طرف حساب، آماده نمایش: هر ارز + معادل ریالی + جمع کل."""
    currencies_by_id = {c.id: c for c in Currency.objects.all()}
    rate_map = latest_rate_map(at=as_of)
    balances = party_balances_map(party, as_of=as_of)

    rows = []
    for currency_id, amount in sorted(
        balances.items(), key=lambda kv: (currencies_by_id[kv[0]].sort_order, currencies_by_id[kv[0]].code)
    ):
        currency = currencies_by_id[currency_id]
        rate = Decimal("1") if currency.is_base else rate_map.get(currency_id)
        rows.append({
            "currency": currency,
            "amount": amount,
            "rate": rate,
            "base_value": (amount * rate) if rate is not None else None,
        })

    total, missing = value_in_base(balances, currencies_by_id, rate_map)
    return {"rows": rows, "total_base": total, "missing_rates": missing}


def account_statement(party, *, currency=None, date_from=None, date_to=None,
                      newest_first=False):
    """گردش حساب یک طرف حساب با مانده تجمعی سطر به سطر.

    مانده تجمعی برای هر ارز جداگانه نگه داشته می‌شود، چون جمع کردن درهم و
    ریال در یک ستون بی‌معنی است.

    newest_first: اگر True باشد، سند و تاریخ جدید در بالای فهرست می‌آید
    (مانده تجمعی همان مانده پس از همان سطر است).
    """
    qs = (
        posted_entries()
        .filter(party=party)
        .select_related("voucher", "currency", "voucher__created_by")
        .order_by("date", "voucher__number", "row_no", "id")
    )
    if currency is not None:
        qs = qs.filter(currency=currency)

    # مانده ابتدای دوره: هر چیزی که قبل از تاریخ شروع اتفاق افتاده
    opening = {}
    if date_from:
        before = posted_entries().filter(party=party, date__lt=date_from)
        if currency is not None:
            before = before.filter(currency=currency)
        for row in before.values("currency").annotate(amount=Sum("amount")):
            opening[row["currency"]] = row["amount"]

    qs = _apply_date_filter(qs, date_from=date_from, date_to=date_to)

    running = dict(opening)
    rows = []
    for entry in qs:
        running[entry.currency_id] = running.get(entry.currency_id, ZERO) + entry.amount
        rows.append({"entry": entry, "running": running[entry.currency_id]})

    currencies_by_id = {c.id: c for c in Currency.objects.all()}
    if newest_first:
        rows.reverse()
    return {
        "rows": rows,
        "opening": {currencies_by_id[cid]: amt for cid, amt in opening.items() if cid in currencies_by_id},
        "closing": {currencies_by_id[cid]: amt for cid, amt in running.items() if cid in currencies_by_id},
    }


def house_accounts_summary(*, as_of=None):
    """وضعیت حساب‌های واقعی صرافی: بانک‌ها و صندوق‌ها.

    حساب «موقعیت ارزی» عمداً اینجا نمی‌آید. آن حساب یک حساب واسط داخلی است
    که دو طرف معامله را به هم وصل می‌کند و علامتش برعکس چیزی است که کاربر
    انتظار دارد — دیدنش کنار صندوق‌های واقعی فقط باعث سوءتفاهم می‌شود
    («فروختم ولی موجودی بالا رفت»). موجودی واقعی ارز در نوار بالای صفحه و
    گزارش سود و زیان تسعیر دیده می‌شود.
    """
    kinds = [Party.Kind.BANK, Party.Kind.CASHBOX]
    parties = list(
        Party.objects.filter(kind__in=kinds, is_active=True).select_related("currency")
        .order_by("kind", "currency__sort_order", "name")
    )
    balances = bulk_party_balances(parties=parties, as_of=as_of, only_nonzero=False)
    currencies_by_id = {c.id: c for c in Currency.objects.all()}

    rows = []
    for party in parties:
        by_currency = balances.get(party.id, {})
        for currency_id, amount in by_currency.items():
            rows.append({
                "party": party,
                "currency": currencies_by_id[currency_id],
                "amount": amount,
            })
    return rows


def customers_overview(*, as_of=None, search=None, only_nonzero=False):
    """فهرست مشتری‌ها با مانده هر ارز و معادل ریالی — برای صفحه اصلی."""
    parties = Party.objects.filter(kind=Party.Kind.CUSTOMER)
    if search:
        parties = parties.filter(Q(name__icontains=search) | Q(code__icontains=search)
                                 | Q(phone__icontains=search))
    parties = list(parties.order_by("name"))

    balances = bulk_party_balances(parties=parties, as_of=as_of, only_nonzero=False)
    currencies_by_id = {c.id: c for c in Currency.objects.all()}
    rate_map = latest_rate_map(at=as_of)

    result = []
    for party in parties:
        by_currency = balances.get(party.id, {})
        cells = []
        for currency_id, amount in sorted(
            by_currency.items(),
            key=lambda kv: (currencies_by_id[kv[0]].sort_order, currencies_by_id[kv[0]].code),
        ):
            if amount == ZERO:
                continue
            cells.append({"currency": currencies_by_id[currency_id], "amount": amount})
        if only_nonzero and not cells:
            continue
        total, missing = value_in_base(by_currency, currencies_by_id, rate_map)
        result.append({
            "party": party,
            "cells": cells,
            "total_base": total,
            "missing_rates": missing,
        })
    return result


def house_currency_snapshot():
    """موجودی واقعی صرافی از هر ارز — برای نمایش در نوار بالای صفحه.

    برای همه ارزها (ریال، درهم، دلار، تتر، …) یکسان محاسبه می‌شود: جمع مانده
    همه بانک‌ها و صندوق‌های آن ارز — یعنی دقیقاً چیزی که الان در اختیار
    داریم. اگر چند صندوق برای یک ارز تعریف کرده باشید (مثلاً «صندوق تتر» و
    «صرافی البانک»)، هر دو با هم جمع می‌شوند.

    قبلاً برای ارزهای غیرپایه این عدد را از جدول InventoryPosition (میانگین
    موزون خرید/فروش) می‌خواندیم که یک عدد «معاملاتی» است، نه «نقدی». آن دو
    عدد می‌توانند از هم فاصله بگیرند — مثلاً وقتی ارزی روی حساب مشتری بماند
    و هنوز وارد هیچ صندوقی نشده باشد — و همین گیج‌کننده بود. حالا هر دو نوع
    ارز از یک منبع واحد (مانده واقعی صندوق‌ها) خوانده می‌شوند.

    خروجی: فهرستی از {currency, amount} به ترتیب نمایش ارزها.
    """
    currencies = list(Currency.objects.filter(is_active=True).order_by("sort_order", "code"))
    if not currencies:
        return []

    rows = (
        posted_entries()
        .filter(
            currency_id__in=[c.id for c in currencies],
            party__kind__in=[Party.Kind.BANK, Party.Kind.CASHBOX],
        )
        .values("currency")
        .annotate(total=Sum("amount"))
    )
    totals = {row["currency"]: row["total"] for row in rows}

    return [
        {"currency": currency, "amount": totals.get(currency.id, ZERO)}
        for currency in currencies
    ]


def currency_totals(*, as_of=None):
    """جمع کل هر ارز روی همه حساب‌ها — باید همیشه صفر باشد.

    این آزمون سلامت کل سیستم است: چون هر سند در هر ارز تراز است، جمع کل هر
    ارز روی تمام حساب‌ها هم باید صفر باشد. اگر نبود، یعنی جایی ایراد دارد.
    """
    qs = _apply_date_filter(posted_entries(), date_to=as_of)
    rows = qs.values("currency").annotate(total=Sum("amount"))
    currencies_by_id = {c.id: c for c in Currency.objects.all()}
    return [
        {"currency": currencies_by_id[r["currency"]], "total": r["total"]}
        for r in rows if r["currency"] in currencies_by_id
    ]


def net_worth_snapshot(*, as_of=None):
    """تراز دارایی: جمع کل دارایی خالص صرافی به ریال، الان.

    فرمول ساده است: (نقدی که در بانک‌ها و صندوق‌ها داریم) + (خالص طلب از
    مشتریان، یعنی جمع مانده همه مشتری‌ها). این عدد، برخلاف «جمع کل هر ارز» که
    همیشه صفر است (چون هر سند تراز است)، صفر نیست — چون حساب‌های داخلی
    (موقعیت ارزی، هزینه، درآمد، سرمایه) را کنار می‌گذاریم. آن حساب‌ها فقط
    بازتاب داخلی همین دو عدد هستند، پس اگر آن‌ها را هم جمع می‌کردیم، دو بار
    حساب می‌شد.

    هر ارز غیرپایه با نرخ همین لحظه به ریال تبدیل و جمع می‌شود.
    """
    currencies = list(Currency.objects.filter(is_active=True).order_by("sort_order", "code"))
    ids = [c.id for c in currencies]

    house_rows = (
        posted_entries()
        .filter(currency_id__in=ids, party__kind__in=[Party.Kind.BANK, Party.Kind.CASHBOX])
        .values("currency").annotate(total=Sum("amount"))
    )
    house_totals = {r["currency"]: r["total"] for r in house_rows}

    customer_rows = (
        posted_entries()
        .filter(currency_id__in=ids, party__kind=Party.Kind.CUSTOMER)
        .values("currency").annotate(total=Sum("amount"))
    )
    customer_totals = {r["currency"]: r["total"] for r in customer_rows}

    rate_map = latest_rate_map(at=as_of)
    rows = []
    grand_total = ZERO
    missing = []

    for currency in currencies:
        house = house_totals.get(currency.id, ZERO)
        customer = customer_totals.get(currency.id, ZERO)
        net = house + customer
        rate = Decimal("1") if currency.is_base else rate_map.get(currency.id)
        base_value = net * rate if rate is not None else None

        if base_value is not None:
            grand_total += base_value
        elif net != ZERO:
            missing.append(currency)

        rows.append({
            "currency": currency, "house": house, "customer": customer,
            "net": net, "rate": rate, "base_value": base_value,
        })

    return {"rows": rows, "grand_total": grand_total, "missing_rates": missing}


def debit_credit_totals(*, date_from=None, date_to=None):
    """جمع بدهکار و بستانکار هر ارز در یک بازه — برای گزارش تراز کل."""
    qs = _apply_date_filter(posted_entries(), date_from, date_to)
    rows = qs.values("currency").annotate(
        debit=Coalesce(Sum("amount", filter=Q(amount__gt=0)), Value(ZERO),
                       output_field=DecimalField(max_digits=28, decimal_places=8)),
        credit=Coalesce(Sum("amount", filter=Q(amount__lt=0)), Value(ZERO),
                        output_field=DecimalField(max_digits=28, decimal_places=8)),
    )
    currencies_by_id = {c.id: c for c in Currency.objects.all()}
    return [
        {
            "currency": currencies_by_id[r["currency"]],
            "debit": r["debit"],
            "credit": -r["credit"],
            "diff": r["debit"] + r["credit"],
        }
        for r in rows if r["currency"] in currencies_by_id
    ]
