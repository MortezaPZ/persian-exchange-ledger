"""اجرای کارگر ربات به صورت جداگانه.

در نسخه دسکتاپ نیازی به این دستور نیست — کارگر خودش داخل برنامه اجرا می‌شود.
این دستور برای وقتی است که سامانه روی سرور نصب شده و می‌خواهید ربات را به
عنوان یک سرویس مستقل اجرا کنید:

    python manage.py run_bot
"""
from django.core.management.base import BaseCommand

from bots.worker import TelegramPoller


class Command(BaseCommand):
    help = "اجرای کارگر دریافت پیام ربات تلگرام"

    def handle(self, *args, **options):
        self.stdout.write("کارگر ربات تلگرام در حال اجراست. برای توقف Ctrl+C بزنید.")
        poller = TelegramPoller()
        try:
            poller.run_forever()
        except KeyboardInterrupt:
            self.stdout.write("\nمتوقف شد.")
