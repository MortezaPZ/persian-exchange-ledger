"""کارگر پس‌زمینه ربات تلگرام.

این کارگر داخل خود برنامه اجرا می‌شود، پس کاربر لازم نیست برنامه دومی را
باز کند: با همان یک کلیک، هم سامانه بالا می‌آید هم ربات.

اگر ربات خاموش باشد یا توکن نداشته باشد، کارگر بی‌سروصدا می‌خوابد و هر چند
ثانیه دوباره تنظیمات را چک می‌کند — تا اگر کاربر وسط کار ربات را روشن کرد،
بدون نیاز به بستن برنامه شروع به کار کند.
"""
import logging
import threading
import time

from .clients import BotError, TelegramClient
from .models import BotConfig, BotMessage
from .services import handle_incoming, record_outgoing

logger = logging.getLogger(__name__)

IDLE_SLEEP = 8          # وقتی ربات خاموش است
ERROR_SLEEP = 15        # بعد از خطای شبکه
POLL_TIMEOUT = 25       # چند ثانیه منتظر پیام جدید بمانیم


class TelegramPoller:
    """حلقه دریافت پیام از تلگرام."""

    def __init__(self, stop_event=None):
        self.stop_event = stop_event or threading.Event()

    # ------------------------------------------------------------------
    def run_forever(self):
        logger.info("کارگر ربات تلگرام شروع شد.")
        while not self.stop_event.is_set():
            try:
                worked = self.tick()
            except BotError as exc:
                logger.warning("ربات تلگرام: %s", exc)
                self.stop_event.wait(ERROR_SLEEP)
                continue
            except Exception:
                logger.exception("خطای پیش‌بینی‌نشده در کارگر ربات")
                self.stop_event.wait(ERROR_SLEEP)
                continue

            if not worked:
                self.stop_event.wait(IDLE_SLEEP)
        logger.info("کارگر ربات تلگرام متوقف شد.")

    # ------------------------------------------------------------------
    def tick(self):
        """یک دور دریافت و پاسخ. خروجی: آیا ربات فعال بود؟"""
        from django.db import close_old_connections

        close_old_connections()

        config = BotConfig.objects.filter(
            platform=BotConfig.Platform.TELEGRAM
        ).first()

        if config is None or not config.is_ready:
            return False

        client = TelegramClient(config)
        updates = client.get_updates(
            offset=config.last_update_id + 1, timeout=POLL_TIMEOUT
        )

        highest = config.last_update_id
        for update in updates:
            update_id = update.get("update_id", 0)
            highest = max(highest, update_id)
            try:
                self.process_update(config, client, update)
            except Exception:
                logger.exception("پردازش پیام تلگرام شکست خورد")

        if highest != config.last_update_id:
            # فقط همین یک فیلد به‌روز می‌شود تا اگر کاربر هم‌زمان تنظیمات را
            # عوض کرده باشد، تغییرش پاک نشود.
            BotConfig.objects.filter(pk=config.pk).update(last_update_id=highest)

        return True

    # ------------------------------------------------------------------
    def process_update(self, config, client, update):
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = message.get("text") or ""

        if chat_id is None:
            return

        # تلگرام شناسه عددی چت را می‌دهد؛ همین در پرونده مشتری ذخیره می‌شود.
        # نام کاربری (username) هم پذیرفته می‌شود تا وارد کردنش برای کاربر
        # راحت‌تر باشد.
        sender = str(chat_id)
        username = (chat.get("username") or "").strip()

        reply, _record = handle_incoming(
            BotConfig.Platform.TELEGRAM, sender, text,
            external_id=str(update.get("update_id", "")),
        )

        # اگر با شناسه عددی پیدا نشد، با نام کاربری هم امتحان می‌کنیم
        if reply is not None and username and "ثبت نشده" in reply:
            retry, _ = handle_incoming(
                BotConfig.Platform.TELEGRAM, username, text,
                external_id=f"u{update.get('update_id', '')}",
            )
            if retry and "ثبت نشده" not in retry:
                reply = retry

        if reply is None:
            return

        try:
            client.send_message(chat_id, reply)
        except BotError as exc:
            logger.warning("ارسال پاسخ تلگرام ناموفق بود: %s", exc)
            record_outgoing(
                BotConfig.Platform.TELEGRAM, sender, reply,
                status=BotMessage.Status.FAILED, error=str(exc),
            )


_worker_thread = None
_stop_event = None


def start_background_worker():
    """کارگر را در یک نخ پس‌زمینه اجرا می‌کند (برای برنامه دسکتاپ)."""
    global _worker_thread, _stop_event

    if _worker_thread is not None and _worker_thread.is_alive():
        return _worker_thread

    _stop_event = threading.Event()
    poller = TelegramPoller(stop_event=_stop_event)
    _worker_thread = threading.Thread(
        target=poller.run_forever, name="telegram-bot", daemon=True
    )
    _worker_thread.start()
    return _worker_thread


def stop_background_worker():
    if _stop_event is not None:
        _stop_event.set()
