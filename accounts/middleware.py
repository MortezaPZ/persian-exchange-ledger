"""نگه‌داشتن درخواست جاری در یک متغیر thread-local.

این کار فقط برای پر کردن خودکار «کاربر» و «آی‌پی» در تاریخچه تغییرات است،
تا لازم نباشد request را به عمق لایه سرویس پاس بدهیم.
"""
import threading

_state = threading.local()


def get_current_request():
    return getattr(_state, "request", None)


def get_current_user():
    request = get_current_request()
    if request is None:
        return None
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return user
    return None


def get_client_ip(request=None):
    request = request or get_current_request()
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class CurrentRequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _state.request = request
        try:
            return self.get_response(request)
        finally:
            _state.request = None
