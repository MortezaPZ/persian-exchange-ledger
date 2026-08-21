"""دکوریتورهای کنترل دسترسی سمت سرور.

نکته امنیتی: مخفی کردن یک منو در قالب، «دسترسی» نیست. هر ویویی که مجوز
می‌خواهد باید با همین دکوریتورها محافظت شود تا اگر کاربری آدرس را مستقیم
تایپ کرد یا درخواست دستی فرستاد، سرور ردش کند.
"""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def require_perm(*codes):
    """دسترسی در صورتی مجاز است که کاربر حداقل یکی از این مجوزها را داشته باشد."""

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not user.is_active:
                raise PermissionDenied("حساب کاربری شما غیرفعال است.")
            if not user.has_any_perm(*codes):
                raise PermissionDenied("شما مجوز دسترسی به این بخش را ندارید.")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator
