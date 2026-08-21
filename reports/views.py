from decimal import Decimal

from django.contrib import messages
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, render

from accounts import services as audit
from accounts.decorators import require_perm
from accounts.models import AuditLog
from accounts.permissions import Perm
from core.jalali import month_start_gregorian, parse_jalali, today_gregorian
from core.models import Currency, Party
from core.money import base_unit_label, to_display
from core.rates import latest_rate_map
from ledger import balances
from ledger.models import Deal, InventoryPosition, Voucher

from .exporters import fmt_date, fmt_number, to_pdf, to_xlsx

ZERO = Decimal("0")


def currency_label(currency):
    """نام ارز برای نمایش در گزارش.

    برای ارز پایه، برچسب واحد نمایش برگردانده می‌شود (تومان)، نه نام خود ارز
    (ریال) — چون مبالغ ستون کناری هم به همان واحد نمایش داده می‌شوند.
    """
    return base_unit_label() if currency.is_base else currency.name


def _range_from_request(request, default_from=None, default_to=None):
    """بازه تاریخ را از پارامترهای آدرس می‌خواند (شمسی)."""
    def get(name, default):
        raw = (request.GET.get(name) or "").strip()
        if not raw:
            return default
        try:
            return parse_jalali(raw)
        except ValueError:
            messages.warning(request, f"تاریخ «{raw}» نامعتبر است و نادیده گرفته شد.")
            return default

    date_from = get("from", default_from if default_from is not None else month_start_gregorian())
    date_to = get("to", default_to if default_to is not None else today_gregorian())
    return date_from, date_to


def _range_label(date_from, date_to):
    return f"از {fmt_date(date_from)} تا {fmt_date(date_to)}"


# --------------------------------------------------------------------------
# گزارش خرید و فروش روزانه
# --------------------------------------------------------------------------
def _daily_data(date_from, date_to):
    deals = (
        Deal.objects.filter(date__gte=date_from, date__lte=date_to)
        .exclude(voucher__status=Voucher.Status.DRAFT)
        .select_related("currency", "counterparty", "voucher")
        .order_by("date", "id")
    )

    per_day = {}
    for deal in deals:
        key = (deal.date, deal.currency_id, deal.side)
        bucket = per_day.setdefault(key, {
            "date": deal.date, "currency": deal.currency, "side": deal.side,
            "quantity": ZERO, "total": ZERO, "count": 0,
        })
        bucket["quantity"] += deal.quantity
        bucket["total"] += deal.total_base
        bucket["count"] += 1

    summary = sorted(
        per_day.values(),
        key=lambda row: (row["date"], row["currency"].sort_order, row["side"]),
    )
    for row in summary:
        row["avg_price"] = (row["total"] / row["quantity"]) if row["quantity"] else ZERO

    totals = {
        "buy": deals.filter(side=Deal.Side.BUY).aggregate(t=Sum("total_base"))["t"] or ZERO,
        "sell": deals.filter(side=Deal.Side.SELL).aggregate(t=Sum("total_base"))["t"] or ZERO,
        "count": deals.count(),
    }
    return deals, summary, totals


@require_perm(Perm.REPORT_DAILY)
def daily(request):
    date_from, date_to = _range_from_request(request)
    deals, summary, totals = _daily_data(date_from, date_to)

    if request.GET.get("export") in {"xlsx", "pdf"}:
        headers = ["تاریخ", "ارز", "نوع", "تعداد", f"مبلغ کل ({base_unit_label()})",
                   "میانگین نرخ", "تعداد معامله"]
        rows = [
            [fmt_date(r["date"]), r["currency"].name,
             "خرید" if r["side"] == Deal.Side.BUY else "فروش",
             fmt_number(r["quantity"], r["currency"].decimal_places),
             fmt_number(to_display(r["total"])),
             fmt_number(to_display(r["avg_price"])),
             fmt_number(r["count"])]
            for r in summary
        ]
        audit.log(AuditLog.Action.EXPORT, summary=f"خروجی گزارش خرید و فروش روزانه ({_range_label(date_from, date_to)})")
        title = "گزارش خرید و فروش روزانه"
        if request.GET.get("export") == "xlsx":
            return to_xlsx(title, headers, rows, subtitle=_range_label(date_from, date_to),
                           filename="daily-trades")
        return to_pdf(title, headers, rows, subtitle=_range_label(date_from, date_to),
                      filename="daily-trades")

    return render(request, "reports/daily.html", {
        "summary": summary, "deals": deals[:200], "totals": totals,
        "date_from": request.GET.get("from", fmt_date(date_from)),
        "date_to": request.GET.get("to", fmt_date(date_to)),
        "range_label": _range_label(date_from, date_to),
    })


# --------------------------------------------------------------------------
# گزارش سود و زیان روزانه خرید و فروش
# --------------------------------------------------------------------------
def _daily_profit_data(date_from, date_to):
    """سود و زیان هر روز از معاملات خرید و فروش.

    سود فقط در لحظه فروش «تحقق» پیدا می‌کند: تفاوت نرخ فروش با میانگین بهای
    خریدهای انجام‌شده تا آن لحظه. خرید به‌خودی‌خود سود یا زیان ندارد، فقط
    میانگین بها را جابه‌جا می‌کند — به همین دلیل ستون سود روبه‌روی روزهایی که
    فقط خرید داشته‌اید صفر است.
    """
    deals = (
        Deal.objects.filter(date__gte=date_from, date__lte=date_to)
        .exclude(voucher__status=Voucher.Status.DRAFT)
        .select_related("currency")
        .order_by("date")
    )

    per_day = {}
    for deal in deals:
        bucket = per_day.setdefault(deal.date, {
            "date": deal.date,
            "buy_total": ZERO, "buy_count": 0,
            "sell_total": ZERO, "sell_count": 0,
            "profit": ZERO,
        })
        if deal.side == Deal.Side.BUY:
            bucket["buy_total"] += deal.total_base
            bucket["buy_count"] += 1
        else:
            bucket["sell_total"] += deal.total_base
            bucket["sell_count"] += 1
            bucket["profit"] += deal.realized_pnl

    rows = sorted(per_day.values(), key=lambda r: r["date"])

    running = ZERO
    for row in rows:
        running += row["profit"]
        row["running_profit"] = running
        # درصد سود نسبت به گردش فروش همان روز
        row["margin"] = (
            (row["profit"] / row["sell_total"] * 100) if row["sell_total"] else None
        )

    totals = {
        "buy": sum((r["buy_total"] for r in rows), ZERO),
        "sell": sum((r["sell_total"] for r in rows), ZERO),
        "profit": sum((r["profit"] for r in rows), ZERO),
        "days": len(rows),
        "profit_days": sum(1 for r in rows if r["profit"] > 0),
        "loss_days": sum(1 for r in rows if r["profit"] < 0),
    }
    totals["margin"] = (
        (totals["profit"] / totals["sell"] * 100) if totals["sell"] else None
    )
    return rows, totals


@require_perm(Perm.REPORT_PROFIT)
def daily_profit(request):
    date_from, date_to = _range_from_request(request)
    rows, totals = _daily_profit_data(date_from, date_to)
    unit = base_unit_label()

    if request.GET.get("export") in {"xlsx", "pdf"}:
        headers = ["تاریخ", f"خرید ({unit})", "تعداد خرید", f"فروش ({unit})",
                   "تعداد فروش", f"سود/زیان روز ({unit})", "درصد سود",
                   f"سود تجمعی ({unit})"]
        data = [
            [fmt_date(r["date"]),
             fmt_number(to_display(r["buy_total"])), fmt_number(r["buy_count"]),
             fmt_number(to_display(r["sell_total"])), fmt_number(r["sell_count"]),
             fmt_number(to_display(r["profit"])),
             (fmt_number(r["margin"], 2) + "٪") if r["margin"] is not None else "—",
             fmt_number(to_display(r["running_profit"]))]
            for r in rows
        ]
        audit.log(AuditLog.Action.EXPORT,
                  summary=f"خروجی گزارش سود و زیان روزانه ({_range_label(date_from, date_to)})")
        title = "گزارش سود و زیان روزانه خرید و فروش"
        if request.GET.get("export") == "xlsx":
            return to_xlsx(title, headers, data, subtitle=_range_label(date_from, date_to),
                           filename="daily-profit")
        return to_pdf(title, headers, data, subtitle=_range_label(date_from, date_to),
                      filename="daily-profit")

    return render(request, "reports/daily_profit.html", {
        "rows": rows, "totals": totals,
        "date_from": request.GET.get("from", fmt_date(date_from)),
        "date_to": request.GET.get("to", fmt_date(date_to)),
        "range_label": _range_label(date_from, date_to),
    })


# --------------------------------------------------------------------------
# گزارش سود و زیان تسعیر
# --------------------------------------------------------------------------
def _profit_data(date_from, date_to):
    """سود تحقق‌یافته (از معاملات) و سود تسعیر (از موجودی باقی‌مانده)."""
    deals = (
        Deal.objects.filter(date__gte=date_from, date__lte=date_to,
                            side=Deal.Side.SELL)
        .exclude(voucher__status=Voucher.Status.DRAFT)
        .select_related("currency")
    )

    realized = {}
    for row in deals.values("currency").annotate(
        pnl=Sum("realized_pnl"), qty=Sum("quantity"),
        turnover=Sum("total_base"), count=Count("id")
    ):
        realized[row["currency"]] = row

    rate_map = latest_rate_map()
    positions = InventoryPosition.objects.select_related("currency").exclude(quantity=0)

    rows = []
    currencies = {c.id: c for c in Currency.objects.all()}

    covered = set(realized) | {p.currency_id for p in positions}
    for currency_id in covered:
        currency = currencies.get(currency_id)
        if currency is None:
            continue
        stat = realized.get(currency_id, {})
        position = next((p for p in positions if p.currency_id == currency_id), None)

        current_rate = rate_map.get(currency_id)
        unrealized = None
        if position is not None and current_rate is not None:
            unrealized = (current_rate - position.avg_unit_cost) * position.quantity

        rows.append({
            "currency": currency,
            "sold_qty": stat.get("qty") or ZERO,
            "turnover": stat.get("turnover") or ZERO,
            "deal_count": stat.get("count") or 0,
            "realized": stat.get("pnl") or ZERO,
            "position_qty": position.quantity if position else ZERO,
            "avg_cost": position.avg_unit_cost if position else ZERO,
            "current_rate": current_rate,
            "unrealized": unrealized,
        })

    rows.sort(key=lambda r: r["currency"].sort_order)
    totals = {
        "realized": sum((r["realized"] for r in rows), ZERO),
        "unrealized": sum((r["unrealized"] for r in rows if r["unrealized"] is not None), ZERO),
    }
    totals["net"] = totals["realized"] + totals["unrealized"]
    missing = [r["currency"] for r in rows if r["position_qty"] != ZERO and r["current_rate"] is None]
    return rows, totals, missing


@require_perm(Perm.REPORT_PROFIT)
def profit(request):
    date_from, date_to = _range_from_request(request)
    rows, totals, missing = _profit_data(date_from, date_to)
    unit = base_unit_label()

    if request.GET.get("export") in {"xlsx", "pdf"}:
        headers = ["ارز", "مقدار فروش", f"گردش فروش ({unit})", "تعداد معامله",
                   f"سود تحقق‌یافته ({unit})", "موجودی فعلی", "میانگین بهای تمام‌شده",
                   "نرخ امروز", f"سود/زیان تسعیر ({unit})"]
        data = [
            [r["currency"].name,
             fmt_number(r["sold_qty"], r["currency"].decimal_places),
             fmt_number(to_display(r["turnover"])),
             fmt_number(r["deal_count"]),
             fmt_number(to_display(r["realized"])),
             fmt_number(r["position_qty"], r["currency"].decimal_places),
             fmt_number(to_display(r["avg_cost"])),
             fmt_number(to_display(r["current_rate"])) if r["current_rate"] else "—",
             fmt_number(to_display(r["unrealized"])) if r["unrealized"] is not None else "—"]
            for r in rows
        ]
        audit.log(AuditLog.Action.EXPORT, summary=f"خروجی گزارش سود و زیان تسعیر ({_range_label(date_from, date_to)})")
        title = "گزارش سود و زیان تسعیر"
        if request.GET.get("export") == "xlsx":
            return to_xlsx(title, headers, data, subtitle=_range_label(date_from, date_to),
                           filename="profit-loss")
        return to_pdf(title, headers, data, subtitle=_range_label(date_from, date_to),
                      filename="profit-loss")

    return render(request, "reports/profit.html", {
        "rows": rows, "totals": totals, "missing": missing,
        "date_from": request.GET.get("from", fmt_date(date_from)),
        "date_to": request.GET.get("to", fmt_date(date_to)),
        "range_label": _range_label(date_from, date_to),
    })


# --------------------------------------------------------------------------
# تراز کل
# --------------------------------------------------------------------------
@require_perm(Perm.REPORT_TRIAL)
def trial(request):
    date_from, date_to = _range_from_request(request)
    rows = balances.debit_credit_totals(date_from=date_from, date_to=date_to)
    health = balances.currency_totals()

    if request.GET.get("export") in {"xlsx", "pdf"}:
        headers = ["ارز", "جمع بدهکار", "جمع بستانکار", "اختلاف"]
        data = [
            [currency_label(r["currency"]),
             fmt_number(to_display(r["debit"]) if r["currency"].is_base else r["debit"],
                        0 if r["currency"].is_base else r["currency"].decimal_places),
             fmt_number(to_display(r["credit"]) if r["currency"].is_base else r["credit"],
                        0 if r["currency"].is_base else r["currency"].decimal_places),
             fmt_number(r["diff"], 2)]
            for r in rows
        ]
        audit.log(AuditLog.Action.EXPORT, summary="خروجی گزارش تراز کل")
        title = "گزارش تراز کل"
        if request.GET.get("export") == "xlsx":
            return to_xlsx(title, headers, data, subtitle=_range_label(date_from, date_to),
                           filename="trial-balance")
        return to_pdf(title, headers, data, subtitle=_range_label(date_from, date_to),
                      filename="trial-balance", landscape_mode=False)

    return render(request, "reports/trial.html", {
        "rows": rows, "health": [h for h in health if h["total"] != ZERO],
        "date_from": request.GET.get("from", fmt_date(date_from)),
        "date_to": request.GET.get("to", fmt_date(date_to)),
        "range_label": _range_label(date_from, date_to),
    })


# --------------------------------------------------------------------------
# وضعیت بانک‌ها و صندوق‌ها
# --------------------------------------------------------------------------
@require_perm(Perm.REPORT_TRIAL)
def banks(request):
    rows = balances.house_accounts_summary()

    if request.GET.get("export") in {"xlsx", "pdf"}:
        headers = ["حساب", "نوع", "ارز", "مانده"]
        data = [
            [r["party"].name, r["party"].kind_label, currency_label(r["currency"]),
             fmt_number(to_display(r["amount"]) if r["currency"].is_base else r["amount"],
                        0 if r["currency"].is_base else r["currency"].decimal_places)]
            for r in rows
        ]
        audit.log(AuditLog.Action.EXPORT, summary="خروجی گزارش وضعیت بانک‌ها")
        title = "وضعیت بانک‌ها، صندوق‌ها و موقعیت ارزی"
        if request.GET.get("export") == "xlsx":
            return to_xlsx(title, headers, data, filename="bank-status")
        return to_pdf(title, headers, data, filename="bank-status", landscape_mode=False)

    return render(request, "reports/banks.html", {"rows": rows})


# --------------------------------------------------------------------------
# تراز مشتریان
# --------------------------------------------------------------------------
@require_perm(Perm.REPORT_STATEMENT)
def customers(request):
    only_nonzero = request.GET.get("all") != "1"
    search = (request.GET.get("q") or "").strip()
    rows = balances.customers_overview(search=search, only_nonzero=only_nonzero)
    grand_total = sum((r["total_base"] for r in rows), ZERO)

    if request.GET.get("export") in {"xlsx", "pdf"}:
        currencies = list(Currency.objects.filter(is_active=True).order_by("sort_order", "code"))
        headers = ["مشتری"] + [currency_label(c) for c in currencies] + [f"ارزش کل ({base_unit_label()})"]
        data = []
        for row in rows:
            by_currency = {cell["currency"].id: cell["amount"] for cell in row["cells"]}
            line = [row["party"].name]
            for currency in currencies:
                amount = by_currency.get(currency.id)
                if amount is None:
                    line.append("—")
                else:
                    line.append(fmt_number(
                        to_display(amount) if currency.is_base else amount,
                        0 if currency.is_base else currency.decimal_places,
                    ))
            line.append(fmt_number(to_display(row["total_base"])))
            data.append(line)
        audit.log(AuditLog.Action.EXPORT, summary="خروجی گزارش تراز مشتریان")
        title = "تراز کل مشتریان"
        if request.GET.get("export") == "xlsx":
            return to_xlsx(title, headers, data, filename="customer-balances")
        return to_pdf(title, headers, data, filename="customer-balances")

    return render(request, "reports/customers.html", {
        "rows": rows, "grand_total": grand_total,
        "only_nonzero": only_nonzero, "q": search,
    })


# --------------------------------------------------------------------------
# خروجی صورتحساب یک مشتری
# --------------------------------------------------------------------------
def _statement_rows(request, party):
    def get(name):
        raw = (request.GET.get(name) or "").strip()
        if not raw:
            return None
        try:
            return parse_jalali(raw)
        except ValueError:
            return None

    date_from, date_to = get("from"), get("to")
    currency_id = request.GET.get("currency")
    currency = Currency.objects.filter(pk=currency_id).first() if currency_id else None

    data = balances.account_statement(
        party, currency=currency, date_from=date_from, date_to=date_to,
        newest_first=True,
    )

    headers = ["تاریخ", "شماره سند", "ارز", "بدهکار", "بستانکار", "مانده", "شرح"]
    rows = []
    for row in data["rows"]:
        entry = row["entry"]
        places = 0 if entry.currency.is_base else entry.currency.decimal_places
        convert = to_display if entry.currency.is_base else (lambda v: v)
        rows.append([
            fmt_date(entry.date),
            fmt_number(entry.voucher.number),
            currency_label(entry.currency),
            fmt_number(convert(entry.debit), places) if entry.amount > 0 else "—",
            fmt_number(convert(entry.credit), places) if entry.amount < 0 else "—",
            fmt_number(convert(row["running"]), places),
            entry.voucher.description or "",
        ])

    label_parts = []
    if date_from:
        label_parts.append(f"از {fmt_date(date_from)}")
    if date_to:
        label_parts.append(f"تا {fmt_date(date_to)}")
    subtitle = f"{party.name} — " + (" ".join(label_parts) if label_parts else "کل دوره")
    return headers, rows, subtitle


# --------------------------------------------------------------------------
# تراز دارایی (خالص ارزش صرافی)
# --------------------------------------------------------------------------
@require_perm(Perm.REPORT_TRIAL)
def net_worth(request):
    snapshot = balances.net_worth_snapshot()

    if request.GET.get("export") in {"xlsx", "pdf"}:
        headers = ["ارز", "نقدی (بانک/صندوق)", "خالص طلب از مشتریان", "جمع",
                   "نرخ روز", f"ارزش به {base_unit_label()}"]
        rows = [
            [r["currency"].name,
             fmt_number(to_display(r["house"]) if r["currency"].is_base else r["house"],
                        0 if r["currency"].is_base else r["currency"].decimal_places),
             fmt_number(to_display(r["customer"]) if r["currency"].is_base else r["customer"],
                        0 if r["currency"].is_base else r["currency"].decimal_places),
             fmt_number(to_display(r["net"]) if r["currency"].is_base else r["net"],
                        0 if r["currency"].is_base else r["currency"].decimal_places),
             fmt_number(to_display(r["rate"])) if r["rate"] else "—",
             fmt_number(to_display(r["base_value"])) if r["base_value"] is not None else "—"]
            for r in snapshot["rows"]
        ]
        audit.log(AuditLog.Action.EXPORT, summary="خروجی گزارش تراز دارایی")
        title = "گزارش تراز دارایی"
        if request.GET.get("export") == "xlsx":
            return to_xlsx(title, headers, rows, filename="net-worth")
        return to_pdf(title, headers, rows, filename="net-worth")

    return render(request, "reports/net_worth.html", {"snapshot": snapshot})


@require_perm(Perm.REPORT_STATEMENT)
def statement_export(request, pk):
    party = get_object_or_404(Party, pk=pk)
    headers, rows, subtitle = _statement_rows(request, party)
    audit.log(AuditLog.Action.EXPORT, summary=f"خروجی اکسل صورتحساب {party.name}",
              model_name="Party", object_id=party.pk)
    return to_xlsx(f"صورتحساب {party.name}", headers, rows, subtitle=subtitle,
                   column_widths=[12, 10, 10, 16, 16, 18, 46],
                   filename=f"statement-{party.pk}")


@require_perm(Perm.REPORT_STATEMENT)
def statement_pdf(request, pk):
    party = get_object_or_404(Party, pk=pk)
    headers, rows, subtitle = _statement_rows(request, party)
    audit.log(AuditLog.Action.EXPORT, summary=f"خروجی PDF صورتحساب {party.name}",
              model_name="Party", object_id=party.pk)
    return to_pdf(f"صورتحساب {party.name}", headers, rows, subtitle=subtitle,
                  filename=f"statement-{party.pk}")
