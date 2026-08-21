"""دریافت و خواندن نرخ ارز.

دو مسیر وجود دارد:
  ۱) ورود دستی توسط کاربر (همیشه در دسترس و بی‌نیاز از اینترنت)
  ۲) دریافت از یک سرویس اینترنتی که خروجی JSON می‌دهد

برای مسیر دوم، آدرس سرویس و «مسیر رسیدن به عدد نرخ» در پایگاه‌داده تعریف
می‌شود؛ بنابراین اتصال به هر سایت نرخ‌دهنده‌ای بدون تغییر کد ممکن است.
"""
import logging
from decimal import Decimal

from django.utils import timezone

from .models import Currency, FxRate, RateSource

logger = logging.getLogger(__name__)


class RateFetchError(Exception):
    """خطا هنگام دریافت نرخ از سرویس بیرونی."""


def dig(payload, path):
    """مقدار را از مسیر نقطه‌ای داخل ساختار JSON بیرون می‌کشد.

    مثال: dig({"aed": {"value": 51200}}, "aed.value") → 51200
    اعداد داخل مسیر به عنوان اندیس لیست تفسیر می‌شوند: "data.0.price"
    """
    current = payload
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def fetch_from_source(source, currencies=None):
    """نرخ‌ها را از یک منبع اینترنتی می‌گیرد و در تاریخچه ثبت می‌کند.

    خروجی: دیکشنری {کد ارز: نرخ} از ارزهایی که با موفقیت گرفته شدند.
    """
    if source.kind != RateSource.Kind.HTTP_JSON:
        raise RateFetchError("این منبع از نوع سرویس اینترنتی نیست.")
    if not source.url:
        raise RateFetchError("آدرس سرویس تعیین نشده است.")

    try:
        import requests
    except ImportError as exc:
        raise RateFetchError("کتابخانه requests نصب نیست.") from exc

    url = source.url.replace("{api_key}", source.api_key or "")

    try:
        response = requests.get(url, timeout=source.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise RateFetchError(f"دریافت نرخ از «{source.title}» ناموفق بود: {exc}") from exc

    mappings = source.mappings.select_related("currency")
    if currencies:
        codes = {c.code for c in currencies}
        mappings = [m for m in mappings if m.currency.code in codes]

    now = timezone.now()
    results = {}
    errors = []

    for mapping in mappings:
        raw = dig(payload, mapping.json_path)
        if raw is None:
            errors.append(f"{mapping.currency.name}: مسیر «{mapping.json_path}» در پاسخ پیدا نشد")
            continue
        try:
            value = Decimal(str(raw).replace(",", "").strip()) * mapping.multiplier
        except Exception:
            errors.append(f"{mapping.currency.name}: مقدار «{raw}» عدد معتبری نیست")
            continue
        if value <= 0:
            errors.append(f"{mapping.currency.name}: نرخ صفر یا منفی نادیده گرفته شد")
            continue

        FxRate.objects.create(
            currency=mapping.currency,
            rate_to_base=value,
            source=source,
            source_label=source.title,
            effective_at=now,
        )
        results[mapping.currency.code] = value

    if not results:
        raise RateFetchError(
            "هیچ نرخی از این منبع خوانده نشد. " + ("؛ ".join(errors) if errors else "")
        )
    if errors:
        logger.warning("دریافت نرخ با هشدار: %s", "؛ ".join(errors))
    return results


def fetch_all_active():
    """همه منابع فعال اینترنتی را یکی‌یکی صدا می‌زند."""
    summary = {"ok": {}, "failed": []}
    for source in RateSource.objects.filter(is_active=True, kind=RateSource.Kind.HTTP_JSON):
        try:
            summary["ok"].update(fetch_from_source(source))
        except RateFetchError as exc:
            summary["failed"].append(f"{source.title}: {exc}")
    return summary


def latest_rate(currency, at=None):
    """آخرین نرخ ثبت‌شده یک ارز تا لحظه مشخص.

    برای ارز پایه همیشه ۱ برمی‌گردد. اگر هیچ نرخی ثبت نشده باشد None.
    """
    if currency.is_base:
        return Decimal("1")
    qs = currency.rates.all()
    if at is not None:
        qs = qs.filter(effective_at__lte=at)
    rate = qs.order_by("-effective_at", "-id").values_list("rate_to_base", flat=True).first()
    return rate


def latest_rate_map(at=None, currencies=None):
    """نگاشت {id ارز: نرخ} برای همه ارزهای فعال، با یک پرس‌وجو به ازای هر ارز."""
    qs = currencies if currencies is not None else Currency.objects.filter(is_active=True)
    return {c.id: latest_rate(c, at=at) for c in qs}


def latest_rate_rows(at=None):
    """آخرین نرخ هر ارز به همراه زمان و منبع — برای نمایش در داشبورد."""
    rows = []
    for currency in Currency.objects.filter(is_active=True).order_by("sort_order", "code"):
        if currency.is_base:
            continue
        qs = currency.rates.all()
        if at is not None:
            qs = qs.filter(effective_at__lte=at)
        rate = qs.order_by("-effective_at", "-id").first()
        rows.append({"currency": currency, "rate": rate})
    return rows
