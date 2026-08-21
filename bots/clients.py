"""ارتباط با سرویس تلگرام و واتس‌اپ.

چرا تلگرام با روش «دریافت پیوسته» (long polling) کار می‌کند و نه وب‌هوک؟
چون وب‌هوک نیاز به دامنه و گواهی SSL دارد. با این روش، برنامه خودش هر چند
ثانیه از تلگرام می‌پرسد «پیام جدیدی هست؟» — بنابراین روی همین کامپیوتر و
بدون دامنه هم کار می‌کند.

واتس‌اپ رسمی فقط وب‌هوک دارد، پس مسیر وب‌هوکش آماده شده ولی تا وقتی آدرس
عمومی نداشته باشید فعال نمی‌شود. این محدودیت خود واتس‌اپ است، نه برنامه.
"""
import logging

from .models import BotConfig

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
WHATSAPP_API = "https://graph.facebook.com/v21.0"


class BotError(Exception):
    """خطای قابل نمایش هنگام کار با پیام‌رسان."""


def _requests():
    try:
        import requests
    except ImportError as exc:
        raise BotError("کتابخانه requests نصب نیست.") from exc
    return requests


# --------------------------------------------------------------------------
# تلگرام
# --------------------------------------------------------------------------
class TelegramClient:
    def __init__(self, config):
        self.config = config
        self.base = (config.api_base or TELEGRAM_API).rstrip("/")

    def _url(self, method):
        return f"{self.base}/bot{self.config.token}/{method}"

    def _call(self, method, payload=None, timeout=15):
        requests = _requests()
        try:
            response = requests.post(self._url(method), json=payload or {}, timeout=timeout)
            data = response.json()
        except Exception as exc:
            raise BotError(f"ارتباط با تلگرام برقرار نشد: {exc}") from exc

        if not data.get("ok"):
            raise BotError(f"تلگرام خطا داد: {data.get('description', 'نامشخص')}")
        return data.get("result")

    def get_me(self):
        """برای آزمودن درستی توکن."""
        return self._call("getMe", timeout=10)

    def get_updates(self, offset=0, timeout=25):
        """پیام‌های جدید را می‌گیرد.

        timeout به تلگرام می‌گوید تا این تعداد ثانیه منتظر بماند و اگر پیامی
        رسید فوراً برگرداند. این‌طور پاسخ‌ها تقریباً بی‌درنگ‌اند و در عین حال
        درخواست بیهوده هم فرستاده نمی‌شود.
        """
        return self._call(
            "getUpdates",
            {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]},
            timeout=timeout + 10,
        ) or []

    def send_message(self, chat_id, text):
        return self._call("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        })


# --------------------------------------------------------------------------
# واتس‌اپ
# --------------------------------------------------------------------------
class WhatsAppClient:
    def __init__(self, config):
        self.config = config
        self.base = (config.api_base or WHATSAPP_API).rstrip("/")

    def send_message(self, to, text):
        requests = _requests()
        url = f"{self.base}/{self.config.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=20)
        except Exception as exc:
            raise BotError(f"ارتباط با واتس‌اپ برقرار نشد: {exc}") from exc

        if response.status_code >= 400:
            raise BotError(f"واتس‌اپ خطا داد ({response.status_code}): {response.text[:200]}")
        return response.json()


def get_client(config):
    if config.platform == BotConfig.Platform.TELEGRAM:
        return TelegramClient(config)
    if config.platform == BotConfig.Platform.WHATSAPP:
        return WhatsAppClient(config)
    raise BotError("پیام‌رسان پشتیبانی نمی‌شود.")


def send_reply(config, recipient, text):
    """پاسخ را می‌فرستد و خطا را به پیام فارسی تبدیل می‌کند."""
    client = get_client(config)
    if isinstance(client, TelegramClient):
        return client.send_message(recipient, text)
    return client.send_message(recipient, text)
