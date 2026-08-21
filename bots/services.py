"""منطق پاسخ‌گویی ربات.

اصل مهم امنیتی: ربات فقط اطلاعات می‌دهد. هیچ مشتری‌ای از طریق ربات نمی‌تواند
تراکنشی ثبت کند یا چیزی را تغییر دهد. عمداً این‌طور طراحی شده — داده‌های مالی
حساس‌اند و یک پیام جعلی نباید بتواند حساب کسی را عوض کند.

اصل دوم: فقط شماره‌هایی که در پرونده مشتری ثبت شده‌اند پاسخ می‌گیرند. غریبه
هیچ اطلاعاتی نمی‌گیرد — حتی نمی‌فهمد چنین مشتری‌ای وجود دارد یا نه.
"""
import logging
import re

from django.db.models import Q

from core.jalali import normalize_digits, persian_digits, today_jalali_str
from core.models import Party
from core.money import base_unit_label, format_amount, to_display
from ledger import balances

from .models import BotConfig, BotMessage

logger = logging.getLogger(__name__)

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

BALANCE_WORDS = {
    "مانده", "ماندع", "موجودی", "بالانس", "حساب", "حسابم", "مانده من", "موجودیم",
    "balance", "/balance", "/start", "start", "وضعیت", "وضعیت حساب", "چقدر",
}
STATEMENT_WORDS = {
    "گردش", "گردش حساب", "صورتحساب", "صورت حساب", "آخرین", "اخرین",
    "تراکنش", "تراکنش‌ها", "تراکنشها", "statement", "/statement", "history",
}
HELP_WORDS = {"راهنما", "کمک", "help", "/help", "دستورات", "؟", "?"}

MAX_STATEMENT_ROWS = 8


def _clean(text):
    """متن پیام را برای تشخیص دستور آماده می‌کند."""
    if not text:
        return ""
    text = normalize_digits(text).strip().lower()
    text = text.replace("‌", " ")          # نیم‌فاصله
    text = re.sub(r"[!.،,؛;:]+$", "", text)     # علائم انتهایی
    return re.sub(r"\s+", " ", text).strip()


def resolve_party(platform, sender_id):
    """طرف حساب را از روی شناسه فرستنده پیدا می‌کند.

    برای واتس‌اپ، شماره‌ها ممکن است با فرمت‌های مختلف بیایند (با ۹۸، با صفر،
    بدون هیچ‌کدام)، پس چند حالت را امتحان می‌کنیم.
    """
    sender_id = (sender_id or "").strip()
    if not sender_id:
        return None

    if platform == BotConfig.Platform.TELEGRAM:
        return Party.objects.filter(
            telegram_id__iexact=sender_id, kind=Party.Kind.CUSTOMER, is_active=True
        ).first()

    digits = re.sub(r"\D", "", normalize_digits(sender_id))
    if not digits:
        return None

    variants = {digits}
    if digits.startswith("98"):
        rest = digits[2:]
        variants |= {rest, "0" + rest, "+98" + rest, "98" + rest}
    elif digits.startswith("0"):
        rest = digits[1:]
        variants |= {rest, "98" + rest, "+98" + rest}
    else:
        variants |= {"0" + digits, "98" + digits, "+98" + digits}

    query = Q()
    for variant in variants:
        query |= Q(whatsapp_no=variant)
    # حالت‌هایی که کاربر شماره را با فاصله یا خط تیره ذخیره کرده
    query |= Q(whatsapp_no__endswith=digits[-10:]) if len(digits) >= 10 else Q()

    return Party.objects.filter(
        query, kind=Party.Kind.CUSTOMER, is_active=True
    ).first()


def build_balance_reply(party):
    """متن پاسخ «مانده من»."""
    view = balances.party_balance_view(party)
    unit = base_unit_label()

    lines = [f"سلام {party.name}", "", "مانده شما تا این لحظه:"]

    if not view["rows"]:
        lines.append("حساب شما صاف است؛ هیچ مانده‌ای ندارید.")
    else:
        for row in view["rows"]:
            currency = row["currency"]
            amount = row["amount"]
            if currency.is_base:
                shown = format_amount(to_display(abs(amount)), 0)
                name = unit
            else:
                shown = format_amount(abs(amount), currency.decimal_places)
                name = currency.name
            state = "بدهکار" if amount > 0 else "بستانکار"
            lines.append(f"• {name}: {shown} {state}")

        if view["total_base"] is not None and len(view["rows"]) > 1:
            total = view["total_base"]
            state = "بدهکار" if total > 0 else "بستانکار"
            lines.append("")
            lines.append(f"ارزش کل به {unit}: {format_amount(to_display(abs(total)), 0)} {state}")

        if view["missing_rates"]:
            names = "، ".join(c.name for c in view["missing_rates"])
            lines.append(f"(نرخ روز {names} ثبت نشده و در جمع کل نیامده است)")

    lines.append("")
    lines.append(f"تاریخ: {today_jalali_str()}")
    return persian_digits("\n".join(lines))


def build_statement_reply(party):
    """متن پاسخ «گردش حساب» — چند تراکنش آخر."""
    data = balances.account_statement(party)
    rows = data["rows"][-MAX_STATEMENT_ROWS:]

    if not rows:
        return persian_digits(f"{party.name} عزیز، هنوز تراکنشی برای شما ثبت نشده است.")

    from core.jalali import format_jalali

    lines = [f"{MAX_STATEMENT_ROWS} تراکنش آخر شما:", ""]
    for item in rows:
        entry = item["entry"]
        currency = entry.currency
        if currency.is_base:
            shown = format_amount(to_display(abs(entry.amount)), 0)
            name = base_unit_label()
        else:
            shown = format_amount(abs(entry.amount), currency.decimal_places)
            name = currency.name
        state = "بدهکار" if entry.amount > 0 else "بستانکار"
        lines.append(f"{format_jalali(entry.date)} — {shown} {name} {state}")

    lines.append("")
    lines.append("برای مانده کلی، کلمه «مانده» را بفرستید.")
    return persian_digits("\n".join(lines))


def build_help_reply():
    return (
        "دستورهایی که می‌توانید بفرستید:\n\n"
        "• «مانده» — مانده فعلی حساب شما در همه ارزها\n"
        "• «گردش» — چند تراکنش آخر شما\n"
        "• «راهنما» — همین پیام\n\n"
        "توجه: این ربات فقط اطلاع‌رسانی می‌کند و امکان ثبت یا تغییر تراکنش "
        "از طریق آن وجود ندارد."
    )


def handle_incoming(platform, sender_id, text, external_id=""):
    """یک پیام دریافتی را پردازش می‌کند و متن پاسخ را برمی‌گرداند.

    خروجی: (متن پاسخ یا None، رکورد پیام ذخیره‌شده)
    اگر None برگردد یعنی نباید پاسخی فرستاده شود.
    """
    external_id = str(external_id or "")

    # پیام تکراری (مثلاً به خاطر تلاش دوباره سرویس‌دهنده) دوباره پردازش نمی‌شود
    if external_id:
        already = BotMessage.objects.filter(
            platform=platform, direction=BotMessage.Direction.IN, external_id=external_id
        ).exists()
        if already:
            logger.info("پیام تکراری نادیده گرفته شد: %s", external_id)
            return None, None

    party = resolve_party(platform, sender_id)
    command = _clean(text)

    incoming = BotMessage(
        platform=platform,
        direction=BotMessage.Direction.IN,
        sender_id=str(sender_id or "")[:64],
        party=party,
        text=(text or "")[:4000],
        external_id=external_id[:128],
    )

    if party is None:
        incoming.status = BotMessage.Status.UNKNOWN_SENDER
        incoming.save()
        reply = (
            "شماره شما در سیستم ثبت نشده است.\n"
            "برای فعال شدن این سرویس، با صرافی تماس بگیرید."
        )
        record_outgoing(platform, sender_id, reply, party=None,
                        status=BotMessage.Status.UNKNOWN_SENDER)
        return reply, incoming

    if command in HELP_WORDS:
        reply = build_help_reply()
    elif any(word in command for word in STATEMENT_WORDS):
        reply = build_statement_reply(party)
    elif any(word in command for word in BALANCE_WORDS):
        reply = build_balance_reply(party)
    else:
        incoming.status = BotMessage.Status.NOT_UNDERSTOOD
        incoming.save()
        reply = (
            f"{party.name} عزیز، متوجه پیام شما نشدم.\n\n" + build_help_reply()
        )
        record_outgoing(platform, sender_id, reply, party=party,
                        status=BotMessage.Status.NOT_UNDERSTOOD)
        return reply, incoming

    incoming.status = BotMessage.Status.OK
    incoming.save()
    record_outgoing(platform, sender_id, reply, party=party)
    return reply, incoming


def record_outgoing(platform, sender_id, text, *, party=None,
                    status=BotMessage.Status.OK, error=""):
    return BotMessage.objects.create(
        platform=platform,
        direction=BotMessage.Direction.OUT,
        status=status,
        sender_id=str(sender_id or "")[:64],
        party=party,
        text=(text or "")[:4000],
        error=error[:255],
    )
