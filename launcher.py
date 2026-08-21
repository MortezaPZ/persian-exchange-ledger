"""لانچر برنامه دسکتاپ سامانه حسابداری صرافی.

این فایل همان چیزی است که کاربر روی آن دابل‌کلیک می‌کند. کارهایی که می‌کند:

  ۱) پوشه داده قابل نوشتن را تعیین می‌کند (کنار برنامه، یا در پوشه کاربر)
  ۲) اگر اولین اجراست، پایگاه‌داده را می‌سازد و اطلاعات اولیه را وارد می‌کند
  ۳) یک رمز عبور تصادفی برای مدیر می‌سازد و در فایل متنی ذخیره می‌کند
  ۴) وب‌سرور را روی یک پورت آزاد بالا می‌آورد
  ۵) مرورگر پیش‌فرض را روی همان آدرس باز می‌کند

کاربر هیچ دستوری تایپ نمی‌کند و لازم نیست پایتون نصب داشته باشد.
"""
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

APP_TITLE = "سامانه حسابداری صرافی"
DEFAULT_PORT = 8730
CREDENTIALS_FILE = "اطلاعات-ورود.txt"

#: اطلاعات ورود اولیه. ثابت است تا بشود آن را به کاربر گفت؛ برنامه تا وقتی
#: رمز عوض نشود در بالای هر صفحه هشدار نشان می‌دهد.
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASSWORD = "Sarrafi@1405"


# --------------------------------------------------------------------------
# مسیرها
# --------------------------------------------------------------------------
def is_frozen():
    """آیا داخل فایل اجرایی بسته‌بندی‌شده اجرا می‌شویم؟"""
    return getattr(sys, "frozen", False)


def bundle_dir():
    """پوشه‌ای که کدها و قالب‌ها در آن هستند (فقط-خواندنی در حالت بسته‌بندی)."""
    if is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def data_dir():
    """پوشه‌ای که پایگاه‌داده و فایل‌های قابل نوشتن در آن ذخیره می‌شوند.

    عمداً *کنار برنامه* ذخیره نمی‌کنیم. دلیلش یک تجربه واقعی است: اگر کاربر
    برنامه را مستقیم از داخل فایل فشرده اجرا کند، ویندوز آن را در یک پوشه
    موقت باز می‌کند و آن پوشه بعداً پاک می‌شود — یعنی همه اسناد مالی از بین
    می‌رود. اگر هم پوشه برنامه را جابه‌جا یا دوباره استخراج کند، برنامه
    پایگاه‌داده خالی می‌سازد و کاربر فکر می‌کند اطلاعاتش پریده.

    پس داده‌ها همیشه در پوشه ثابت کاربر می‌نشیند و مسیرش در خود برنامه
    (صفحه پشتیبان‌گیری) نشان داده می‌شود.
    """
    if not is_frozen():
        path = Path(__file__).resolve().parent / "desktop-data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    root = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    path = root / "SarrafiAccounting" / "data"
    path.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_data(path)
    return path


def _migrate_legacy_data(new_dir):
    """پایگاه‌داده نسخه قبلی (که کنار برنامه بود) را یک بار منتقل می‌کند.

    اگر کاربر با نسخه قبلی اطلاعاتی وارد کرده باشد، نباید با به‌روزرسانی
    برنامه آن‌ها را از دست بدهد.
    """
    if (new_dir / "db.sqlite3").exists():
        return

    legacy = Path(sys.executable).resolve().parent / "داده‌ها"
    legacy_db = legacy / "db.sqlite3"
    if not legacy_db.exists():
        return

    try:
        import shutil

        for name in ("db.sqlite3", "secret.key", CREDENTIALS_FILE):
            source = legacy / name
            if source.exists():
                shutil.copy2(source, new_dir / name)
        say("  اطلاعات نسخه قبلی پیدا شد و منتقل شد.")
    except OSError as exc:
        say(f"  (انتقال اطلاعات نسخه قبلی ممکن نشد: {exc})")


# --------------------------------------------------------------------------
# ابزارهای کوچک
# --------------------------------------------------------------------------
def find_free_port(preferred=DEFAULT_PORT):
    """اول پورت پیش‌فرض را امتحان می‌کند؛ اگر اشغال بود پورت آزاد بعدی."""
    for port in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def local_ip():
    """آی‌پی این کامپیوتر در شبکه محلی — برای دسترسی از گوشی با همان وای‌فای."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.4)
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def setup_console():
    """کنسول ویندوز را برای نمایش فارسی آماده می‌کند.

    کنسول پیش‌فرض ویندوز با کدپیج قدیمی کار می‌کند و نمی‌تواند حروف فارسی را
    چاپ کند؛ بدون این تنظیم، برنامه همان ابتدا با خطای رمزگذاری می‌افتد.
    """
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def say(text=""):
    """چاپ امن.

    اگر کنسول به هر دلیلی نتواند متن فارسی را نشان بدهد، برنامه نباید بیفتد —
    اطلاعات مهم در مرورگر و در فایل «اطلاعات-ورود» هم هست.
    """
    try:
        print(text, flush=True)
    except (UnicodeEncodeError, OSError):
        try:
            print(str(text).encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass


def banner(port, ip):
    say()
    say("=" * 62)
    say(f"   {APP_TITLE}")
    say("=" * 62)
    say()
    say("  برنامه آماده است. مرورگر خودش باز می‌شود.")
    say()
    say(f"  روی همین کامپیوتر:   http://127.0.0.1:{port}")
    if ip:
        say(f"  از گوشی (همین وای‌فای): http://{ip}:{port}")
        say("     (اگر روی گوشی باز نشد، در برنامه به بخش «دسترسی از گوشی» بروید)")
    say()
    say("  ربات تلگرام هم فعال است (اگر در تنظیمات روشنش کرده باشید).")
    say()
    say("-" * 62)
    say("  این پنجره را نبندید. تا وقتی باز است، برنامه کار می‌کند.")
    say("  برای بستن برنامه: این پنجره را ببندید یا Ctrl+C بزنید.")
    say("=" * 62)
    say()


# --------------------------------------------------------------------------
# راه‌اندازی جنگو
# --------------------------------------------------------------------------
def bootstrap_django(paths):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ["SARRAFI_DATA_DIR"] = str(paths["data"])
    os.environ["SARRAFI_DESKTOP"] = "True"
    os.environ["DB_ENGINE"] = "sqlite"
    os.environ["DEBUG"] = "False"
    os.environ["ALLOWED_HOSTS"] = "*"
    os.environ.setdefault("DISPLAY_UNIT", "rial")

    if not is_frozen():
        sys.path.insert(0, str(bundle_dir()))

    import django

    django.setup()


def prepare_database(paths):
    """جدول‌ها را می‌سازد و در اولین اجرا اطلاعات اولیه را وارد می‌کند."""
    from django.core.management import call_command

    db_file = paths["data"] / "db.sqlite3"
    first_run = not db_file.exists()

    say("  آماده‌سازی پایگاه‌داده…")
    call_command("migrate", verbosity=0, interactive=False)

    try:
        call_command("collectstatic", verbosity=0, interactive=False)
    except Exception:
        # اگر جمع‌آوری فایل‌های ثابت شکست بخورد، برنامه همچنان کار می‌کند
        pass

    if first_run:
        say("  اولین اجرا — ساخت اطلاعات اولیه…")
        password = create_first_admin(paths)
        return password

    # نصب‌های قبلی هم باید قابلیت‌های جدید را بگیرند: مجوزهای تازه، صندوق
    # ارزهایی که هنوز صندوق ندارند، و امثال آن. این دستور بی‌خطر است و چیزی
    # را پاک یا بازنویسی نمی‌کند.
    upgrade_existing_install()

    # از دفعه دوم به بعد، قبل از هر کاری یک نسخه پشتیبان می‌گیریم
    from maintenance.backups import backup_on_startup

    backup = backup_on_startup()
    if backup is not None:
        say(f"  نسخه پشتیبان خودکار گرفته شد: {backup.name}")
    return None


def backup_on_exit():
    """هنگام بسته شدن برنامه یک نسخه پشتیبان می‌گیرد.

    کارفرما خواسته بود علاوه بر پشتیبان هنگام باز شدن، موقع خروج هم نسخه‌ای
    گرفته شود — چون کارهای همان روز بعد از آخرین پشتیبان انجام شده‌اند.

    ممکن است از دو مسیر صدا زده شود (بستن پنجره و پایان عادی)، پس فقط یک بار
    اجرا می‌شود.
    """
    global _exit_backup_done
    if _exit_backup_done:
        return
    _exit_backup_done = True

    try:
        from maintenance.backups import create_backup, prune_auto_backups

        path = create_backup(label="startup")  # همان دسته خودکار، تا هرس شود
        prune_auto_backups()
        say(f"  نسخه پشتیبان خروج گرفته شد: {path.name}")
    except Exception as exc:
        say(f"  (پشتیبان‌گیری هنگام خروج انجام نشد: {exc})")


_exit_backup_done = False


def install_close_handler():
    """پشتیبان‌گیری هنگام بستن پنجره با ضربدر.

    کاربر معمولاً برنامه را با بستن پنجره می‌بندد، نه با Ctrl+C. ویندوز در آن
    حالت پروسه را می‌کشد و کد عادی خروج اجرا نمی‌شود؛ این تابع رویداد بسته
    شدن را می‌گیرد تا فرصت پشتیبان‌گیری باشد (ویندوز چند ثانیه مهلت می‌دهد).
    """
    if os.name != "nt":
        return

    try:
        import ctypes
        from ctypes import wintypes

        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _handler(event):
            # CTRL_CLOSE_EVENT=2، CTRL_LOGOFF=5، CTRL_SHUTDOWN=6
            if event in (2, 5, 6):
                say()
                say("  در حال بستن برنامه…")
                backup_on_exit()
            return False  # اجازه بده ویندوز مسیر عادی بسته شدن را ادامه بدهد

        # ارجاع را نگه می‌داریم وگرنه جمع‌آوری زباله آن را پاک می‌کند
        global _close_handler_ref
        _close_handler_ref = handler_type(_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_close_handler_ref, True)
    except Exception:
        pass


def allow_through_firewall(port):
    """اجازه عبور از فایروال ویندوز برای دسترسی از گوشی.

    کارفرما گفته بود آدرس روی گوشی باز نمی‌شود؛ علت رایجش همین است. اگر
    برنامه دسترسی مدیر نداشته باشد این کار بی‌سروصدا شکست می‌خورد و کاربر
    می‌تواند از صفحه «دسترسی از گوشی» دستور دستی را بردارد.
    """
    if os.name != "nt":
        return False

    import subprocess

    rule = "Sarrafi Accounting"
    try:
        subprocess.run(
            ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule}"],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule", f"name={rule}",
             "dir=in", "action=allow", "protocol=TCP", f"localport={port}"],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except Exception:
        return False


def upgrade_existing_install():
    """قابلیت‌های نسخه جدید را به نصب‌های قبلی هم اضافه می‌کند."""
    import io

    from django.core.management import call_command

    try:
        call_command("setup_sarrafi", skip_admin=True, stdout=io.StringIO())
    except Exception as exc:
        say(f"  (به‌روزرسانی تنظیمات انجام نشد: {exc})")


def create_first_admin(paths):
    """کاربر مدیر را با رمز پیش‌فرض می‌سازد.

    رمز عمداً ثابت و از پیش معلوم است تا بشود آن را به کاربر گفت. در عوض،
    کاربر با علامت must_change_password ساخته می‌شود و تا وقتی رمز را عوض
    نکند، در بالای همه صفحات نوار قرمز هشدار می‌بیند.
    """
    import io

    from django.core.management import call_command

    password = DEFAULT_ADMIN_PASSWORD

    # خروجی دستور راه‌اندازی برای کاربر نهایی معنی ندارد (به runserver اشاره
    # می‌کند که اینجا کاربرد ندارد)، پس در یک بافر جمع می‌شود.
    call_command(
        "setup_sarrafi",
        admin_user=DEFAULT_ADMIN_USER,
        admin_pass=password,
        admin_name="مدیر سیستم",
        stdout=io.StringIO(),
    )

    note = paths["data"] / CREDENTIALS_FILE
    note.write_text(
        "اطلاعات ورود به سامانه حسابداری صرافی\n"
        "=====================================\n\n"
        f"نام کاربری: {DEFAULT_ADMIN_USER}\n"
        f"رمز عبور:  {password}\n\n"
        "این رمز پیش‌فرض است و همه آن را می‌دانند.\n"
        "بعد از اولین ورود، از بالای صفحه روی نام خودتان کلیک کنید و\n"
        "رمز را به رمز دلخواه خودتان تغییر بدهید.\n\n"
        "تا وقتی رمز را عوض نکنید، در بالای هر صفحه یک نوار قرمز هشدار\n"
        "نمایش داده می‌شود.\n\n"
        "بعد از تغییر رمز، این فایل را پاک کنید.\n",
        encoding="utf-8",
    )
    return password


def show_first_run_notice(password, paths):
    say()
    say("*" * 62)
    say("   اولین اجرا — اطلاعات ورود شما")
    say("*" * 62)
    say()
    say(f"   نام کاربری:  {DEFAULT_ADMIN_USER}")
    say(f"   رمز عبور:   {password}")
    say()
    say("   این اطلاعات در فایل زیر هم ذخیره شد:")
    say(f"   {paths['data'] / CREDENTIALS_FILE}")
    say()
    say("   این رمز پیش‌فرض است — بعد از ورود حتماً عوضش کنید.")
    say("*" * 62)


def open_browser_when_ready(url, port):
    """صبر می‌کند تا سرور واقعاً بالا بیاید، بعد مرورگر را باز می‌کند."""
    deadline = time.time() + 25
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.3)
    try:
        webbrowser.open(url)
    except Exception:
        say(f"  مرورگر خودکار باز نشد. این آدرس را دستی باز کنید: {url}")


# --------------------------------------------------------------------------
# اجرا
# --------------------------------------------------------------------------
def main():
    setup_console()
    paths = {"bundle": bundle_dir(), "data": data_dir()}

    say()
    say(f"  {APP_TITLE} — در حال بالا آمدن، چند لحظه صبر کنید…")

    try:
        bootstrap_django(paths)
        first_run_password = prepare_database(paths)
    except Exception as exc:
        say()
        say("  خطا هنگام راه‌اندازی:")
        say(f"  {type(exc).__name__}: {exc}")
        say()
        say("  لطفاً همین پیام را برای پشتیبانی بفرستید.")
        input("\n  برای بستن، کلید Enter را بزنید…")
        return 1

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    allow_through_firewall(port)
    install_close_handler()

    if first_run_password:
        show_first_run_notice(first_run_password, paths)

    # کارگر ربات تلگرام داخل همین برنامه اجرا می‌شود تا کاربر لازم نباشد
    # برنامه دومی باز کند. اگر ربات خاموش باشد، بی‌سروصدا منتظر می‌ماند.
    try:
        from bots.worker import start_background_worker

        start_background_worker()
    except Exception as exc:
        say(f"  (ربات تلگرام شروع نشد: {exc})")

    banner(port, local_ip())

    threading.Thread(
        target=open_browser_when_ready, args=(url, port), daemon=True
    ).start()

    from waitress import serve

    from config.wsgi import application

    try:
        # روی 0.0.0.0 گوش می‌دهد تا از گوشیِ روی همان وای‌فای هم قابل دسترسی باشد
        serve(application, host="0.0.0.0", port=port, threads=8, _quiet=True)
    except KeyboardInterrupt:
        say("\n  در حال بستن برنامه…")
    except Exception as exc:
        say(f"\n  خطای سرور: {exc}")
        backup_on_exit()
        input("\n  برای بستن، کلید Enter را بزنید…")
        return 1

    backup_on_exit()
    say("  برنامه بسته شد.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
