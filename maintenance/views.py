import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode

from accounts import services as audit
from accounts.decorators import require_perm
from accounts.models import AuditLog
from accounts.permissions import Perm

from . import backups

logger = logging.getLogger(__name__)

RESET_CONFIRM_PHRASE = "پاک کن"


@require_perm(Perm.BACKUP_MANAGE)
def backup_dashboard(request):
    rows = backups.list_backups()
    total_size = sum(item["size"] for item in rows)

    return render(request, "maintenance/backup.html", {
        "backups": rows[:30],
        "backup_count": len(rows),
        "total_size": total_size,
        "data_dir": str(settings.DATA_DIR),
        "backup_dir": str(backups.backup_dir()),
        "is_sqlite": backups.is_sqlite(),
    })


@require_perm(Perm.BACKUP_MANAGE)
def backup_create(request):
    if request.method != "POST":
        return redirect("maintenance:backup")

    try:
        path = backups.create_backup(label="manual")
    except Exception as exc:
        messages.error(request, f"پشتیبان‌گیری انجام نشد: {exc}")
    else:
        audit.log(AuditLog.Action.CREATE, summary=f"ساخت نسخه پشتیبان {path.name}",
                  model_name="Backup")
        messages.success(request, f"نسخه پشتیبان ساخته شد: {path.name}")
    return redirect("maintenance:backup")


@require_perm(Perm.BACKUP_MANAGE)
def backup_download(request):
    """آخرین وضعیت پایگاه‌داده را به صورت یک فایل دانلود می‌دهد.

    کاربر می‌تواند این فایل را روی فلش یا فضای ابری نگه دارد؛ برای بازگردانی
    کافی است دوباره در همین صفحه بارگذاری‌اش کند.
    """
    try:
        path = backups.create_backup(label="download")
    except Exception as exc:
        messages.error(request, f"ساخت فایل پشتیبان ممکن نشد: {exc}")
        return redirect("maintenance:backup")

    audit.log(AuditLog.Action.EXPORT, summary=f"دانلود نسخه پشتیبان {path.name}",
              model_name="Backup")

    response = FileResponse(open(path, "rb"), as_attachment=True,
                            filename=backups.backup_filename_for_download())
    return response


@require_perm(Perm.BACKUP_MANAGE)
def backup_restore(request):
    if request.method != "POST":
        return redirect("maintenance:backup")

    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "نسخه پشتیبان انتخاب نشده است.")
        return redirect("maintenance:backup")

    try:
        backups.restore_backup(name)
    except Exception as exc:
        messages.error(request, f"بازگردانی انجام نشد: {exc}")
        return redirect("maintenance:backup")

    audit.log(AuditLog.Action.UPDATE, summary=f"بازگردانی از نسخه پشتیبان {name}",
              model_name="Backup")
    messages.success(
        request,
        "اطلاعات از نسخه پشتیبان برگردانده شد. برای اطمینان، یک بار برنامه را "
        "ببندید و دوباره باز کنید.",
    )
    return redirect("maintenance:backup")


# --------------------------------------------------------------------------
# پاک کردن اسناد برای شروع دوباره
# --------------------------------------------------------------------------
@require_perm(Perm.DATA_RESET)
def data_reset(request):
    """همه اسناد مالی را پاک می‌کند تا کاربر بتواند از اول تست کند.

    این قابلیت به درخواست صریح کارفرما برای دوره آزمایش اضافه شده است. عمداً
    فقط برای مدیر اصلی فعال است، عبارت تأیید می‌خواهد، و قبل از پاک کردن یک
    نسخه پشتیبان کامل می‌گیرد تا اگر اشتباهی زده شد راه بازگشت باشد.
    """
    from core.models import FxRate, Party
    from ledger.models import Deal, Entry, InventoryPosition, Sequence, Voucher

    if not request.user.is_superuser:
        raise PermissionDenied("این کار فقط از دست مدیر اصلی برمی‌آید.")

    stats = {
        "vouchers": Voucher.objects.count(),
        "entries": Entry.objects.count(),
        "deals": Deal.objects.count(),
        "parties": Party.objects.filter(kind=Party.Kind.CUSTOMER).count(),
        "rates": FxRate.objects.count(),
    }

    if request.method == "POST":
        phrase = (request.POST.get("confirm_phrase") or "").strip()
        if phrase != RESET_CONFIRM_PHRASE:
            messages.error(request, f"برای تأیید باید دقیقاً عبارت «{RESET_CONFIRM_PHRASE}» را بنویسید.")
            return redirect("maintenance:data_reset")

        also_parties = request.POST.get("also_parties") == "1"
        also_rates = request.POST.get("also_rates") == "1"

        try:
            backup_path = backups.create_backup(label="before-reset")
        except Exception as exc:
            messages.error(
                request,
                f"چون نسخه پشتیبان ساخته نشد، پاک‌سازی انجام نشد. خطا: {exc}",
            )
            return redirect("maintenance:data_reset")

        with transaction.atomic():
            Deal.objects.all().delete()
            Entry.objects.all().delete()
            Voucher.objects.all().delete()
            InventoryPosition.objects.all().delete()
            Sequence.objects.filter(key="voucher_number").update(value=0)

            if also_rates:
                FxRate.objects.all().delete()
            if also_parties:
                # حساب‌های سیستمی (موقعیت ارزی، صندوق، افتتاحیه) باید بمانند
                Party.objects.filter(kind=Party.Kind.CUSTOMER, is_system=False).delete()

        audit.log(
            AuditLog.Action.DELETE,
            summary=(
                f"پاک‌سازی کامل اسناد — {stats['vouchers']} سند، "
                f"{stats['deals']} معامله. نسخه پشتیبان: {backup_path.name}"
            ),
            model_name="Voucher",
            before=stats,
        )
        messages.success(
            request,
            f"همه اسناد پاک شد و شماره اسناد از ۱ شروع می‌شود. "
            f"نسخه پشتیبان قبل از پاک‌سازی ذخیره شد: {backup_path.name}",
        )
        return redirect("ledger:dashboard")

    return render(request, "maintenance/data_reset.html", {
        "stats": stats,
        "confirm_phrase": RESET_CONFIRM_PHRASE,
    })


# --------------------------------------------------------------------------
# راهنمای دسترسی از گوشی
# --------------------------------------------------------------------------
def mobile_access(request):
    """آدرس دسترسی از گوشی را نشان می‌دهد و مشکلات رایج را توضیح می‌دهد.

    کارفرما گفته بود آدرسی که دادیم روی گوشی باز نمی‌شود؛ بیشتر وقت‌ها علتش
    فایروال ویندوز است یا اینکه گوشی به وای‌فای دیگری وصل است.
    """
    import socket

    def guess_lan_ip():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.4)
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
        except OSError:
            return None

    host = request.get_host()
    port = host.split(":")[-1] if ":" in host else "80"
    ip = guess_lan_ip()

    return render(request, "maintenance/mobile.html", {
        "lan_ip": ip,
        "port": port,
        "lan_url": f"http://{ip}:{port}" if ip else None,
    })
