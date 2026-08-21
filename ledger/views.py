from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode

from accounts import services as audit
from accounts.decorators import require_perm
from accounts.models import AuditLog
from accounts.permissions import Perm
from core import rates as rate_service
from core.jalali import month_start_gregorian, persian_digits, today_gregorian
from core.models import Currency, Party
from core.money import to_display

from . import balances, services
from .forms import (CashMovementForm, DealForm, ExpenseIncomeForm, OpeningBalanceForm,
                    PartyTransferForm, TransferForm, VoidForm, VoucherFilterForm)
from .models import Deal, Voucher

ZERO = Decimal("0")


# --------------------------------------------------------------------------
# داشبورد
# --------------------------------------------------------------------------
@login_required
def dashboard(request):
    today = today_gregorian()
    month_start = month_start_gregorian()

    house = balances.house_accounts_summary()
    rate_rows = rate_service.latest_rate_rows()

    today_deals = Deal.objects.filter(date=today).exclude(voucher__status=Voucher.Status.DRAFT)
    month_deals = Deal.objects.filter(date__gte=month_start).exclude(
        voucher__status=Voucher.Status.DRAFT
    )

    def deal_stats(qs):
        buy = qs.filter(side=Deal.Side.BUY).aggregate(total=Sum("total_base"))["total"] or ZERO
        sell = qs.filter(side=Deal.Side.SELL).aggregate(total=Sum("total_base"))["total"] or ZERO
        return {"count": qs.count(), "buy": buy, "sell": sell}

    stats_today = deal_stats(today_deals)
    stats_month = deal_stats(month_deals)

    profit_month = None
    if request.user.has_perm_code(Perm.REPORT_PROFIT):
        profit_month = month_deals.aggregate(total=Sum("realized_pnl"))["total"] or ZERO

    recent = (
        Voucher.objects.select_related("created_by")
        .prefetch_related("entries__party", "entries__currency")
        .order_by("-created_at")[:12]
    )

    # آزمون سلامت: جمع کل هر ارز روی همه حساب‌ها باید صفر باشد
    health = [row for row in balances.currency_totals() if row["total"] != ZERO]

    top_customers = sorted(
        balances.customers_overview(only_nonzero=True),
        key=lambda r: abs(r["total_base"]), reverse=True,
    )[:10]

    return render(request, "ledger/dashboard.html", {
        "house": house,
        "rate_rows": rate_rows,
        "stats_today": stats_today,
        "stats_month": stats_month,
        "profit_month": profit_month,
        "recent": recent,
        "health": health,
        "top_customers": top_customers,
    })


@login_required
def topbar_balances(request):
    """موجودی ارزها برای نوار بالای صفحه.

    این آدرس در تمام صفحات هر ۳۰ ثانیه صدا زده می‌شود تا عددها بدون رفرش
    کردن صفحه به‌روز بمانند؛ پس عمداً سبک نگه داشته شده است.
    """
    from core.money import (base_unit_label, format_amount,
                            format_amount_compact, to_display)

    rows = []
    for row in balances.house_currency_snapshot():
        currency = row["currency"]
        amount = row["amount"]
        if currency.is_base:
            value = format_amount(to_display(amount), 0)
        else:
            value = format_amount_compact(amount, currency.decimal_places)
        rows.append({
            "name": base_unit_label() if currency.is_base else currency.name,
            "value": value,
            "sign": "neg" if amount < 0 else ("pos" if amount > 0 else ""),
        })
    return JsonResponse({"rows": rows})


@login_required
def dashboard_data(request):
    """داده‌های زنده داشبورد به صورت JSON.

    صفحه هر ۲۰ ثانیه این آدرس را صدا می‌زند تا بدون رفرش شدن، موجودی‌ها و
    نرخ‌ها به‌روز شوند.
    """
    from core.money import format_amount, to_display

    house = []
    for row in balances.house_accounts_summary():
        currency = row["currency"]
        amount = row["amount"]
        house.append({
            "party": row["party"].name,
            "currency": currency.name,
            "amount": format_amount(
                to_display(amount) if currency.is_base else amount,
                0 if currency.is_base else currency.decimal_places,
            ),
            "negative": amount < 0,
        })

    rate_rows = []
    for row in rate_service.latest_rate_rows():
        rate = row["rate"]
        rate_rows.append({
            "currency": row["currency"].name,
            "rate": format_amount(to_display(rate.rate_to_base), 0) if rate else None,
            "at": rate.effective_at.strftime("%H:%M") if rate else None,
        })

    return JsonResponse({"house": house, "rates": rate_rows})


def recent_descriptions(kind, limit=15):
    """شرح‌هایی که کاربر قبلاً برای همین نوع سند نوشته است.

    کارفرما گفته بود شرح‌ها اغلب شبیه هم‌اند و فقط عدد و شماره معامله فرق
    می‌کند؛ پس به جای تایپ دوباره، از فهرست انتخاب می‌کند و ویرایشش می‌کند.
    """
    rows = (
        Voucher.objects.filter(kind=kind)
        .exclude(description="")
        .order_by("-created_at")
        .values_list("description", flat=True)[:120]
    )
    seen, result = set(), []
    for text in rows:
        text = (text or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
        if len(result) >= limit:
            break
    return result


# --------------------------------------------------------------------------
# ثبت معامله
# --------------------------------------------------------------------------
@require_perm(Perm.VOUCHER_ADD)
def deal_create(request):
    form = DealForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            voucher = services.post_deal(
                side=data["side"],
                date=data["date"],
                counterparty=data["counterparty"],
                currency=data["currency"],
                quantity=data["quantity"],
                unit_price=data["unit_price"],
                description=data["description"],
                created_by=request.user,
                settle_account=data.get("settle_account"),
                delivery_account=data.get("delivery_account"),
            )
        except services.DuplicateVoucher as exc:
            messages.warning(request, str(exc))
            return redirect("ledger:voucher_detail", pk=exc.voucher.pk)
        except services.LedgerError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"سند شماره {persian_digits(voucher.number)} ثبت شد.")
            # تاریخ و طرف حساب برای معامله بعدی نگه داشته می‌شود
            keep = {"date": request.POST.get("date"),
                    "counterparty": request.POST.get("counterparty"),
                    "side": request.POST.get("side")}
            return redirect(
                f"{request.path}?date={keep['date'] or ''}"
                f"&counterparty={keep['counterparty'] or ''}&side={keep['side'] or ''}"
            )

    elif request.method == "GET" and request.GET:
        initial = {k: v for k, v in request.GET.items() if v}
        if initial:
            form = DealForm(initial=initial)

    return render(request, "ledger/deal_form.html", {
        "form": form,
        "currencies": list(
            Currency.objects.filter(is_active=True, is_base=False).order_by("sort_order", "code")
        ),
        "description_suggestions": recent_descriptions(Voucher.Kind.DEAL),
    })


@require_perm(Perm.VOUCHER_ADD)
def cash_create(request, kind):
    if kind not in {Voucher.Kind.RECEIVE, Voucher.Kind.PAY}:
        messages.error(request, "نوع عملیات نامعتبر است.")
        return redirect("ledger:dashboard")

    form = CashMovementForm(request.POST or None, kind=kind)

    # هنگام «ویرایش سند»، اطلاعات سند حذف‌شده از آدرس خوانده و فرم پیش‌پر می‌شود
    if request.method == "GET" and request.GET:
        initial = {k: v for k, v in request.GET.items() if v}
        if initial:
            form = CashMovementForm(initial=initial, kind=kind)

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            voucher = services.post_cash_movement(
                kind=kind,
                date=data["date"],
                party=data["party"],
                account=data["account"],
                currency=data["currency"],
                amount=data["amount"],
                description=data["description"],
                created_by=request.user,
            )
        except services.LedgerError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"سند شماره {persian_digits(voucher.number)} ثبت شد.")
            return redirect("ledger:cash_create", kind=kind)

    title = "ثبت دریافت" if kind == Voucher.Kind.RECEIVE else "ثبت پرداخت"
    return render(request, "ledger/cash_form.html", {
        "form": form, "title": title, "kind": kind,
        "description_suggestions": recent_descriptions(kind),
    })


@require_perm(Perm.VOUCHER_ADD)
def transfer_create(request):
    form = TransferForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            voucher = services.post_transfer(
                date=data["date"],
                from_account=data["from_account"],
                to_account=data["to_account"],
                currency=data["currency"],
                amount=data["amount"],
                description=data["description"],
                created_by=request.user,
            )
        except services.LedgerError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"سند شماره {persian_digits(voucher.number)} ثبت شد.")
            return redirect("ledger:transfer_create")
    return render(request, "ledger/transfer_form.html", {"form": form})


@require_perm(Perm.VOUCHER_ADD)
def party_transfer_create(request):
    """انتقال حساب بین دو مشتری — وقتی پول مستقیم بین خودشان جابه‌جا می‌شود."""
    form = PartyTransferForm(request.POST or None)

    if request.method == "GET" and request.GET:
        initial = {k: v for k, v in request.GET.items() if v}
        if initial:
            form = PartyTransferForm(initial=initial)

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            voucher = services.post_party_transfer(
                date=data["date"],
                from_party=data["from_party"],
                to_party=data["to_party"],
                currency=data["currency"],
                amount=data["amount"],
                description=data["description"],
                created_by=request.user,
            )
        except services.LedgerError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"سند شماره {persian_digits(voucher.number)} ثبت شد.")
            keep = {
                "date": request.POST.get("date"),
                "from_party": request.POST.get("from_party"),
                "to_party": request.POST.get("to_party"),
                "currency": request.POST.get("currency"),
                "description": request.POST.get("description"),
            }
            query = urlencode({k: v for k, v in keep.items() if v})
            return redirect(f"{request.path}?{query}" if query else request.path)

    return render(request, "ledger/party_transfer_form.html", {
        "form": form,
        "description_suggestions": recent_descriptions(Voucher.Kind.TRANSFER),
    })


@require_perm(Perm.VOUCHER_ADD)
def expense_income_create(request, kind):
    """ثبت هزینه یا درآمد.

    کارفرما پرسیده بود از کجا سند هزینه/درآمد ثبت کند و فقط سند افتتاحیه
    پیدا کرده بود؛ این صفحه دقیقاً همان چیزی است که کم بود.
    """
    if kind not in {Voucher.Kind.EXPENSE, Voucher.Kind.INCOME}:
        messages.error(request, "نوع عملیات نامعتبر است.")
        return redirect("ledger:dashboard")

    form = ExpenseIncomeForm(request.POST or None, kind=kind)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        poster = services.post_expense if kind == Voucher.Kind.EXPENSE else services.post_income
        try:
            voucher = poster(
                date=data["date"], category=data["category"], account=data["account"],
                currency=data["currency"], amount=data["amount"],
                description=data["description"], created_by=request.user,
            )
        except services.LedgerError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"سند شماره {persian_digits(voucher.number)} ثبت شد.")
            return redirect("ledger:expense_income_create", kind=kind)

    title = "ثبت هزینه" if kind == Voucher.Kind.EXPENSE else "ثبت درآمد"
    category_kind = "expense" if kind == Voucher.Kind.EXPENSE else "income"
    return render(request, "ledger/expense_income_form.html", {
        "form": form, "title": title, "kind": kind, "category_kind": category_kind,
    })


@require_perm(Perm.VOUCHER_ADD)
def opening_create(request):
    form = OpeningBalanceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            voucher = services.post_opening_balance(
                date=data["date"],
                party=data["party"],
                currency=data["currency"],
                amount=form.signed_amount(),
                description=data["description"],
                created_by=request.user,
            )
        except services.LedgerError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"مانده افتتاحیه با سند شماره {persian_digits(voucher.number)} ثبت شد.")
            return redirect("ledger:opening_create")
    return render(request, "ledger/opening_form.html", {"form": form})


# --------------------------------------------------------------------------
# اسناد
# --------------------------------------------------------------------------
@require_perm(Perm.VOUCHER_VIEW)
def voucher_list(request):
    form = VoucherFilterForm(request.GET or None)
    qs = (
        Voucher.objects.select_related("created_by")
        .prefetch_related("entries__party", "entries__currency", "deal__currency")
        .order_by("-date", "-number", "-id")
    )

    if form.is_valid():
        data = form.cleaned_data
        if data.get("q"):
            q = data["q"]
            qs = qs.filter(
                Q(description__icontains=q)
                | Q(entries__party__name__icontains=q)
                | Q(number__icontains=q if q.isdigit() else "\x00")
            ).distinct()
        if data.get("kind"):
            qs = qs.filter(kind=data["kind"])
        if data.get("status"):
            qs = qs.filter(status=data["status"])
        if data.get("date_from"):
            qs = qs.filter(date__gte=data["date_from"])
        if data.get("date_to"):
            qs = qs.filter(date__lte=data["date_to"])

    qs = qs.order_by("-date", "-number", "-id")
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(request, "ledger/voucher_list.html", {"page": page, "form": form})


@require_perm(Perm.VOUCHER_VIEW)
def voucher_detail(request, pk):
    voucher = get_object_or_404(
        Voucher.objects.select_related("created_by", "reverses")
        .prefetch_related("entries__party", "entries__currency"),
        pk=pk,
    )
    deal = getattr(voucher, "deal", None)
    reversal = getattr(voucher, "reversed_by", None)
    return render(request, "ledger/voucher_detail.html", {
        "voucher": voucher, "deal": deal, "reversal": reversal,
    })


@require_perm(Perm.VOUCHER_VOID)
def voucher_void(request, pk):
    voucher = get_object_or_404(Voucher, pk=pk)
    form = VoidForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            reversal = services.void_voucher(
                voucher=voucher,
                reason=form.cleaned_data["reason"],
                created_by=request.user,
            )
        except services.LedgerError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"سند {persian_digits(voucher.number)} باطل شد و "
                f"سند برگشتی شماره {persian_digits(reversal.number)} صادر گردید.",
            )
            return redirect("ledger:voucher_detail", pk=voucher.pk)

    return render(request, "ledger/voucher_void.html", {"voucher": voucher, "form": form})


@require_perm(Perm.VOUCHER_EDIT)
def voucher_delete(request, pk):
    """حذف کامل سند — فقط مدیر اصلی.

    این قابلیت به درخواست کارفرما برای دوره آزمایش اضافه شده تا بتواند
    داده‌های اشتباه را پاک کند و دوباره وارد کند.
    """
    if not request.user.is_superuser:
        raise PermissionDenied("حذف سند فقط از دست مدیر اصلی برمی‌آید.")

    voucher = get_object_or_404(
        Voucher.objects.prefetch_related("entries__party", "entries__currency"), pk=pk
    )

    if request.method == "POST":
        reason = (request.POST.get("reason") or "").strip()
        if not reason:
            messages.error(request, "نوشتن علت حذف الزامی است.")
            return redirect("ledger:voucher_delete", pk=pk)

        try:
            services.delete_voucher(voucher=voucher, reason=reason, deleted_by=request.user)
        except services.LedgerError as exc:
            messages.error(request, str(exc))
            return redirect("ledger:voucher_detail", pk=pk)

        messages.success(request, f"سند شماره {persian_digits(voucher.number)} کاملاً حذف شد.")
        return redirect("ledger:voucher_list")

    return render(request, "ledger/voucher_delete.html", {"voucher": voucher})


@require_perm(Perm.VOUCHER_EDIT)
def voucher_edit(request, pk):
    """ویرایش سند: سند قدیمی حذف و همان اطلاعات در فرم ثبت پیش‌پر می‌شود.

    چون سند قطعی طبق قواعد حسابداری قابل تغییر نیست، «ویرایش» در عمل یعنی
    حذف سند اشتباه و ثبت دوباره آن با مقادیر درست. کاربر یک فرم از پیش پر شده
    می‌بیند و فقط همان چیزی را که غلط بوده اصلاح می‌کند.
    """
    if not request.user.is_superuser:
        raise PermissionDenied("ویرایش سند فقط از دست مدیر اصلی برمی‌آید.")

    voucher = get_object_or_404(Voucher, pk=pk)
    deal = getattr(voucher, "deal", None)
    entries = list(voucher.entries.select_related("party", "currency"))

    from core.jalali import format_jalali

    params = {"date": format_jalali(voucher.date), "description": voucher.description}
    target = None

    if voucher.kind == Voucher.Kind.DEAL and deal is not None:
        target = "ledger:deal_create"
        params.update({
            "counterparty": deal.counterparty_id,
            "side": deal.side,
            "currency": deal.currency_id,
            "quantity": f"{deal.quantity:f}".rstrip("0").rstrip("."),
            "unit_price": f"{to_display(deal.unit_price):f}".rstrip("0").rstrip("."),
        })
    elif voucher.kind in {Voucher.Kind.RECEIVE, Voucher.Kind.PAY}:
        account = next((e.party for e in entries if e.party.kind in
                        {Party.Kind.BANK, Party.Kind.CASHBOX}), None)
        customer = next((e.party for e in entries if e.party.kind == Party.Kind.CUSTOMER), None)
        amount = max((abs(e.amount) for e in entries), default=ZERO)
        currency = entries[0].currency if entries else None
        target = "ledger:cash_create"
        params.update({
            "party": customer.pk if customer else "",
            "account": account.pk if account else "",
            "currency": currency.pk if currency else "",
            "amount": f"{to_display(amount) if currency and currency.is_base else amount:f}".rstrip("0").rstrip("."),
        })
    else:
        messages.warning(
            request,
            "برای این نوع سند فرم ویرایش آماده نیست؛ سند حذف شد و می‌توانید "
            "دوباره ثبتش کنید.",
        )

    try:
        services.delete_voucher(
            voucher=voucher, reason="ویرایش سند توسط مدیر اصلی", deleted_by=request.user
        )
    except services.LedgerError as exc:
        messages.error(request, str(exc))
        return redirect("ledger:voucher_detail", pk=pk)

    if target is None:
        return redirect("ledger:voucher_list")

    query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    if target == "ledger:cash_create":
        base = reverse(target, kwargs={"kind": voucher.kind})
    else:
        base = reverse(target)

    messages.info(
        request,
        f"سند شماره {persian_digits(voucher.number)} حذف شد و اطلاعاتش در فرم زیر "
        "پیش‌پر شده است. اصلاح کنید و دوباره ثبت بزنید.",
    )
    return redirect(f"{base}?{query}")


# --------------------------------------------------------------------------
# صورتحساب مشتری
# --------------------------------------------------------------------------
@require_perm(Perm.REPORT_STATEMENT)
def statement(request, pk=None):
    parties = Party.objects.filter(is_active=True).order_by("kind", "name")
    party = get_object_or_404(Party, pk=pk) if pk else None

    context = {"parties": parties, "party": party}
    if party is None:
        return render(request, "ledger/statement.html", context)

    from core.jalali import parse_jalali

    def get_date(name):
        raw = request.GET.get(name)
        if not raw:
            return None
        try:
            return parse_jalali(raw)
        except ValueError:
            messages.warning(request, f"تاریخ «{raw}» نامعتبر است و نادیده گرفته شد.")
            return None

    date_from, date_to = get_date("from"), get_date("to")
    currency_id = request.GET.get("currency")
    currency = Currency.objects.filter(pk=currency_id).first() if currency_id else None

    data = balances.account_statement(
        party, currency=currency, date_from=date_from, date_to=date_to,
        newest_first=True,
    )
    context.update({
        "data": data,
        "balance": balances.party_balance_view(party),
        "currencies": Currency.objects.filter(is_active=True).order_by("sort_order", "code"),
        "selected_currency": currency,
        "date_from": request.GET.get("from", ""),
        "date_to": request.GET.get("to", ""),
    })
    return render(request, "ledger/statement.html", context)


@require_perm(Perm.PARTY_VIEW, Perm.VOUCHER_ADD)
def party_balance_json(request, pk):
    """مانده لحظه‌ای یک طرف حساب — برای نمایش کنار فرم ثبت معامله."""
    from core.money import base_unit_label, format_amount, to_display

    party = get_object_or_404(Party, pk=pk)
    view = balances.party_balance_view(party)
    rows = []
    for row in view["rows"]:
        currency = row["currency"]
        amount = row["amount"]
        rows.append({
            "currency": currency.name,
            "amount": format_amount(
                to_display(amount) if currency.is_base else amount,
                0 if currency.is_base else currency.decimal_places,
            ),
            "state": "بدهکار" if amount > 0 else "بستانکار",
            "negative": amount < 0,
        })
    return JsonResponse({
        "party": party.name,
        "rows": rows,
        "total_base": format_amount(to_display(view["total_base"]), 0),
        "unit": base_unit_label(),
    })
