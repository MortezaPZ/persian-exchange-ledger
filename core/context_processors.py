from django.conf import settings

from . import jalali, money


def app_context(request):
    """متغیرهایی که در همه قالب‌ها لازم است."""
    user = getattr(request, "user", None)
    perms = set()
    topbar_balances = []

    if user is not None and user.is_authenticated:
        perms = user.permission_codes()
        # موجودی ارزها در نوار بالا. اگر جدول‌ها هنوز ساخته نشده باشند
        # (مثلاً وسط migrate)، نباید کل صفحه بیفتد.
        try:
            from ledger.balances import house_currency_snapshot

            topbar_balances = house_currency_snapshot()
        except Exception:
            topbar_balances = []

    return {
        "APP_NAME": "سامانه حسابداری صرافی",
        "MUST_CHANGE_PASSWORD": bool(
            user is not None
            and user.is_authenticated
            and getattr(user, "must_change_password", False)
        ),
        "BASE_UNIT": money.base_unit_label(),
        "DISPLAY_UNIT": money.display_unit(),
        "TODAY_JALALI": jalali.today_jalali_str(),
        "MY_PERMS": perms,
        "TOPBAR_BALANCES": topbar_balances,
        "DEBUG": settings.DEBUG,
    }
