import json
import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt

from accounts import services as audit
from accounts.decorators import require_perm
from accounts.models import AuditLog
from accounts.permissions import Perm
from core.models import Party

from .clients import BotError, TelegramClient, send_reply
from .forms import BotConfigForm
from .models import BotConfig, BotMessage
from .services import handle_incoming

logger = logging.getLogger(__name__)


def _ensure_configs():
    """رکورد تنظیمات هر پیام‌رسان را در صورت نبود می‌سازد."""
    for platform, _label in BotConfig.Platform.choices:
        BotConfig.objects.get_or_create(platform=platform)


@require_perm(Perm.BOT_MANAGE)
def bot_dashboard(request):
    _ensure_configs()
    configs = BotConfig.objects.all().order_by("platform")

    linked = Party.objects.filter(kind=Party.Kind.CUSTOMER, is_active=True).exclude(
        Q(telegram_id="") & Q(whatsapp_no="")
    ).count()
    total_customers = Party.objects.filter(kind=Party.Kind.CUSTOMER, is_active=True).count()

    recent = BotMessage.objects.select_related("party")[:15]

    return render(request, "bots/dashboard.html", {
        "configs": configs,
        "linked": linked,
        "total_customers": total_customers,
        "recent": recent,
    })


@require_perm(Perm.BOT_MANAGE)
def bot_settings(request, platform):
    _ensure_configs()
    config = get_object_or_404(BotConfig, platform=platform)
    form = BotConfigForm(request.POST or None, instance=config)

    if request.method == "POST" and form.is_valid():
        was_enabled = config.is_enabled
        config = form.save()
        audit.log(
            AuditLog.Action.UPDATE,
            summary=f"تغییر تنظیمات ربات {config.get_platform_display()}"
                    f" — {'فعال' if config.is_enabled else 'غیرفعال'}",
            model_name="BotConfig",
            object_id=config.pk,
            before={"enabled": was_enabled},
            after={"enabled": config.is_enabled},
        )
        messages.success(request, "تنظیمات ربات ذخیره شد.")
        return redirect("bots:dashboard")

    return render(request, "bots/settings.html", {"form": form, "config": config})


@require_perm(Perm.BOT_MANAGE)
def bot_test(request, platform):
    """اتصال به سرویس را می‌آزماید تا کاربر بفهمد توکن درست است یا نه."""
    if request.method != "POST":
        return redirect("bots:dashboard")

    config = get_object_or_404(BotConfig, platform=platform)

    if not config.token:
        messages.error(request, "ابتدا توکن ربات را وارد کنید.")
        return redirect("bots:dashboard")

    if config.platform == BotConfig.Platform.TELEGRAM:
        try:
            info = TelegramClient(config).get_me()
        except BotError as exc:
            messages.error(request, str(exc))
        else:
            username = info.get("username", "—")
            messages.success(
                request,
                f"اتصال برقرار است. نام ربات شما: @{username} — "
                "حالا مشتری‌ها می‌توانند به این ربات پیام بدهند."
            )
    else:
        messages.info(
            request,
            "آزمون خودکار برای واتس‌اپ در دسترس نیست؛ پس از تنظیم وب‌هوک، "
            "با یک پیام واقعی امتحان کنید."
        )
    return redirect("bots:dashboard")


@require_perm(Perm.BOT_MANAGE)
def message_log(request):
    qs = BotMessage.objects.select_related("party")

    q = (request.GET.get("q") or "").strip()
    platform = (request.GET.get("platform") or "").strip()
    status = (request.GET.get("status") or "").strip()

    if q:
        qs = qs.filter(
            Q(text__icontains=q) | Q(sender_id__icontains=q) | Q(party__name__icontains=q)
        )
    if platform:
        qs = qs.filter(platform=platform)
    if status:
        qs = qs.filter(status=status)

    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "bots/messages.html", {
        "page": page, "q": q, "platform": platform, "status": status,
        "platforms": BotConfig.Platform.choices,
        "statuses": BotMessage.Status.choices,
    })


# --------------------------------------------------------------------------
# وب‌هوک واتس‌اپ
# --------------------------------------------------------------------------
@csrf_exempt
def whatsapp_webhook(request):
    """مسیر دریافت پیام از واتس‌اپ.

    این مسیر عمداً بدون CSRF است چون درخواست از سرویس بیرونی می‌آید، ولی در
    عوض با «کلید امنیتی وب‌هوک» تأیید می‌شود. بدون کلید درست، هیچ پیامی
    پردازش نمی‌شود.
    """
    config = BotConfig.objects.filter(platform=BotConfig.Platform.WHATSAPP).first()
    if config is None or not config.is_enabled:
        return HttpResponseForbidden("ربات واتس‌اپ فعال نیست.")

    # مرحله تأیید اولیه که خود واتس‌اپ انجام می‌دهد
    if request.method == "GET":
        token = request.GET.get("hub.verify_token", "")
        challenge = request.GET.get("hub.challenge", "")
        if config.webhook_secret and token == config.webhook_secret:
            return HttpResponse(challenge, content_type="text/plain")
        return HttpResponseForbidden("کلید تأیید نادرست است.")

    if request.method != "POST":
        return HttpResponseForbidden("روش پشتیبانی نمی‌شود.")

    if config.webhook_secret:
        provided = request.headers.get("X-Webhook-Secret", "")
        if provided and provided != config.webhook_secret:
            return HttpResponseForbidden("کلید امنیتی نادرست است.")

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "بدنه درخواست نامعتبر است."}, status=400)

    for sender, text, message_id in _extract_whatsapp_messages(payload):
        try:
            reply, _record = handle_incoming(
                BotConfig.Platform.WHATSAPP, sender, text, external_id=message_id
            )
            if reply:
                send_reply(config, sender, reply)
        except BotError as exc:
            logger.warning("پاسخ واتس‌اپ فرستاده نشد: %s", exc)
        except Exception:
            logger.exception("پردازش پیام واتس‌اپ شکست خورد")

    # واتس‌اپ انتظار پاسخ ۲۰۰ دارد، وگرنه پیام را دوباره می‌فرستد
    return JsonResponse({"ok": True})


def _extract_whatsapp_messages(payload):
    """پیام‌ها را از ساختار تودرتوی واتس‌اپ بیرون می‌کشد."""
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            for message in value.get("messages", []) or []:
                if message.get("type") != "text":
                    continue
                sender = message.get("from", "")
                text = (message.get("text") or {}).get("body", "")
                yield sender, text, message.get("id", "")
