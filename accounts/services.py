"""سرویس ثبت تاریخچه تغییرات."""
from .middleware import get_client_ip, get_current_request, get_current_user
from .models import AuditLog


def log(action, *, summary="", model_name="", object_id="", before=None, after=None, user=None):
    """یک رکورد در تاریخچه تغییرات ثبت می‌کند.

    خطای این تابع هرگز نباید کار اصلی کاربر را متوقف کند، ولی چون داخل همان
    تراکنش سند صدا زده می‌شود، اگر ثبت لاگ شکست بخورد کل سند برمی‌گردد —
    و این دقیقاً همان چیزی است که می‌خواهیم: سند بدون ردپا ثبت نشود.
    """
    request = get_current_request()
    actor = user or get_current_user()
    agent = ""
    if request is not None:
        agent = (request.META.get("HTTP_USER_AGENT") or "")[:255]

    return AuditLog.objects.create(
        user=actor,
        username_snapshot=(actor.get_username() if actor else ""),
        action=action,
        model_name=model_name,
        object_id=str(object_id or ""),
        summary=summary[:255],
        ip_address=get_client_ip(request),
        user_agent=agent,
        before=before,
        after=after,
    )
