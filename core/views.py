from django.contrib import messages
from django.db.models import Q
from django.forms import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts import services as audit
from accounts.decorators import require_perm
from accounts.models import AuditLog
from accounts.permissions import Perm

from . import rates
from .forms import (CurrencyForm, ManualRateForm, PartyForm, RateSourceForm,
                    RateSourceMappingForm)
from .models import Currency, FxRate, Party, RateSource, RateSourceMapping


# --------------------------------------------------------------------------
# طرف حساب‌ها
# --------------------------------------------------------------------------
@require_perm(Perm.PARTY_VIEW, Perm.PARTY_MANAGE)
def party_list(request):
    from ledger.balances import bulk_party_balances

    kind = request.GET.get("kind") or ""
    q = (request.GET.get("q") or "").strip()
    show_inactive = request.GET.get("inactive") == "1"

    parties = Party.objects.select_related("currency")
    if kind:
        parties = parties.filter(kind=kind)
    if q:
        parties = parties.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(phone__icontains=q))
    if not show_inactive:
        parties = parties.filter(is_active=True)
    parties = list(parties.order_by("kind", "name"))

    balances = bulk_party_balances(parties=parties)
    currencies = {c.id: c for c in Currency.objects.all()}
    rows = []
    for party in parties:
        cells = [
            {"currency": currencies[cid], "amount": amt}
            for cid, amt in sorted(
                balances.get(party.id, {}).items(),
                key=lambda kv: (currencies[kv[0]].sort_order, currencies[kv[0]].code),
            )
            if amt != 0
        ]
        rows.append({"party": party, "cells": cells})

    return render(request, "core/party_list.html", {
        "rows": rows, "kind": kind, "q": q, "show_inactive": show_inactive,
        "kinds": Party.Kind.choices,
    })


@require_perm(Perm.PARTY_MANAGE)
def party_edit(request, pk=None):
    instance = get_object_or_404(Party, pk=pk) if pk else None
    form = PartyForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        is_new = instance is None
        before = None if is_new else {"name": instance.name, "is_active": instance.is_active,
                                      "phone": instance.phone}
        party = form.save()
        audit.log(
            AuditLog.Action.CREATE if is_new else AuditLog.Action.UPDATE,
            summary=("تعریف طرف حساب " if is_new else "ویرایش طرف حساب ") + party.name,
            model_name="Party", object_id=party.pk,
            before=before,
            after={"name": party.name, "is_active": party.is_active, "phone": party.phone},
        )
        messages.success(request, f"طرف حساب «{party.name}» ذخیره شد.")
        return redirect("core:party_list")
    return render(request, "core/party_form.html", {"form": form, "instance": instance})


@require_perm(Perm.PARTY_VIEW, Perm.PARTY_MANAGE)
def party_detail(request, pk):
    from ledger.balances import party_balance_view

    party = get_object_or_404(Party.objects.select_related("currency"), pk=pk)
    view = party_balance_view(party)
    recent = (
        party.entries.exclude(voucher__status="draft")
        .select_related("voucher", "currency")
        .order_by("-date", "-voucher__number", "-id")[:25]
    )
    return render(request, "core/party_detail.html",
                  {"party": party, "balance": view, "recent": recent})


# --------------------------------------------------------------------------
# ارزها
# --------------------------------------------------------------------------
@require_perm(Perm.CURRENCY_MANAGE)
def currency_list(request):
    currencies = Currency.objects.all().order_by("sort_order", "code")
    return render(request, "core/currency_list.html", {"currencies": currencies})


@require_perm(Perm.CURRENCY_MANAGE)
def currency_edit(request, pk=None):
    instance = get_object_or_404(Currency, pk=pk) if pk else None
    form = CurrencyForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        is_new = instance is None
        currency = form.save()
        audit.log(
            AuditLog.Action.CREATE if is_new else AuditLog.Action.UPDATE,
            summary=("تعریف ارز " if is_new else "ویرایش ارز ") + currency.name,
            model_name="Currency", object_id=currency.pk,
        )
        messages.success(request, f"ارز «{currency.name}» ذخیره شد.")
        return redirect("core:currency_list")
    return render(request, "core/currency_form.html", {"form": form, "instance": instance})


# --------------------------------------------------------------------------
# نرخ ارز
# --------------------------------------------------------------------------
@require_perm(Perm.FXRATE_MANAGE, Perm.VOUCHER_VIEW)
def rate_dashboard(request):
    rows = rates.latest_rate_rows()
    history = (
        FxRate.objects.select_related("currency", "source")
        .order_by("-effective_at", "-id")[:50]
    )
    form = ManualRateForm()
    sources = RateSource.objects.prefetch_related("mappings__currency").order_by("id")
    return render(request, "core/rate_dashboard.html", {
        "rows": rows, "history": history, "form": form, "sources": sources,
    })


@require_perm(Perm.FXRATE_MANAGE)
def rate_manual_add(request):
    if request.method != "POST":
        return redirect("core:rate_dashboard")

    form = ManualRateForm(request.POST)
    if form.is_valid():
        currency = form.cleaned_data["currency"]
        rate_value = form.cleaned_data["rate"]
        FxRate.objects.create(
            currency=currency,
            rate_to_base=rate_value,
            source_label=form.cleaned_data.get("note") or "ورود دستی",
            effective_at=timezone.now(),
            created_by=request.user,
        )
        audit.log(
            AuditLog.Action.CREATE,
            summary=f"ثبت دستی نرخ {currency.name} = {rate_value}",
            model_name="FxRate",
        )
        messages.success(request, f"نرخ {currency.name} ثبت شد.")
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect("core:rate_dashboard")


@require_perm(Perm.FXRATE_MANAGE)
def rate_fetch(request, pk=None):
    if request.method != "POST":
        return redirect("core:rate_dashboard")

    if pk:
        source = get_object_or_404(RateSource, pk=pk)
        try:
            fetched = rates.fetch_from_source(source)
            audit.log(AuditLog.Action.RATE_FETCH,
                      summary=f"دریافت نرخ از {source.title}: {', '.join(fetched)}",
                      model_name="RateSource", object_id=source.pk)
            messages.success(request, f"نرخ {len(fetched)} ارز از «{source.title}» به‌روز شد.")
        except rates.RateFetchError as exc:
            messages.error(request, str(exc))
    else:
        summary = rates.fetch_all_active()
        if summary["ok"]:
            audit.log(AuditLog.Action.RATE_FETCH,
                      summary=f"دریافت نرخ از همه منابع: {', '.join(summary['ok'])}")
            messages.success(request, f"نرخ {len(summary['ok'])} ارز به‌روز شد.")
        for failure in summary["failed"]:
            messages.error(request, failure)
        if not summary["ok"] and not summary["failed"]:
            messages.warning(request, "هیچ منبع اینترنتی فعالی تعریف نشده است.")
    return redirect("core:rate_dashboard")


MappingFormSet = inlineformset_factory(
    RateSource, RateSourceMapping, form=RateSourceMappingForm, extra=2, can_delete=True
)


@require_perm(Perm.FXRATE_MANAGE)
def rate_source_edit(request, pk=None):
    instance = get_object_or_404(RateSource, pk=pk) if pk else None
    form = RateSourceForm(request.POST or None, instance=instance)
    formset = MappingFormSet(request.POST or None, instance=instance)

    if request.method == "POST" and form.is_valid():
        source = form.save()
        formset = MappingFormSet(request.POST, instance=source)
        if formset.is_valid():
            formset.save()
            audit.log(
                AuditLog.Action.CREATE if instance is None else AuditLog.Action.UPDATE,
                summary=f"تنظیم منبع نرخ «{source.title}»",
                model_name="RateSource", object_id=source.pk,
            )
            messages.success(request, "منبع نرخ ذخیره شد.")
            return redirect("core:rate_dashboard")

    return render(request, "core/rate_source_form.html",
                  {"form": form, "formset": formset, "instance": instance})
