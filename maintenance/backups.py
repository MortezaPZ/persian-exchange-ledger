"""پشتیبان‌گیری و بازگردانی پایگاه‌داده.

چرا این ماژول وجود دارد: کارفرما یک بار برنامه را باز کرد و همه چیز صفر بود.
علتش این بود که پایگاه‌داده کنار خود برنامه ذخیره می‌شد و با جابه‌جا کردن یا
دوباره استخراج کردن پوشه، برنامه یک پایگاه‌داده خالی می‌ساخت.

حالا دو لایه محافظت داریم:
  ۱) داده‌ها در پوشه ثابت کاربر ذخیره می‌شوند، نه کنار برنامه
  ۲) در هر بار اجرا یک نسخه پشتیبان خودکار گرفته می‌شود و چند نسخه آخر می‌ماند

به‌علاوه کاربر می‌تواند هر وقت خواست با یک دکمه نسخه پشتیبان بگیرد.
"""
import datetime
import logging
import shutil
import sqlite3
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

BACKUP_DIR_NAME = "پشتیبان"
AUTO_BACKUP_KEEP = 10
FILE_PREFIX = "backup-"


def data_dir():
    return Path(settings.DATA_DIR)


def backup_dir():
    path = data_dir() / BACKUP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_path():
    """مسیر فایل پایگاه‌داده (فقط برای SQLite معنی دارد)."""
    db = settings.DATABASES["default"]
    if "sqlite" not in db["ENGINE"]:
        return None
    return Path(db["NAME"])


def is_sqlite():
    return database_path() is not None


def _timestamp():
    from core.jalali import to_jalali

    now = datetime.datetime.now()
    jalali = to_jalali(now.date())
    return f"{jalali.year:04d}-{jalali.month:02d}-{jalali.day:02d}_{now:%H%M%S}"


def create_backup(label="manual"):
    """یک نسخه پشتیبان سالم از پایگاه‌داده می‌سازد.

    از دستور backup خود SQLite استفاده می‌کنیم، نه کپی ساده فایل. تفاوتش این
    است که اگر همان لحظه کسی مشغول ثبت سند باشد، کپی ساده ممکن است نیمه‌کاره
    و خراب دربیاید؛ این روش همیشه یک نسخه سالم می‌دهد.
    """
    source = database_path()
    if source is None:
        raise RuntimeError("پشتیبان‌گیری خودکار فقط برای پایگاه‌داده SQLite در دسترس است.")
    if not source.exists():
        raise RuntimeError("فایل پایگاه‌داده پیدا نشد.")

    target = backup_dir() / f"{FILE_PREFIX}{_timestamp()}-{label}.sqlite3"

    source_conn = sqlite3.connect(str(source))
    try:
        target_conn = sqlite3.connect(str(target))
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()

    return target


def list_backups():
    """فهرست نسخه‌های پشتیبان، جدیدترین اول."""
    if not backup_dir().exists():
        return []
    files = sorted(
        backup_dir().glob(f"{FILE_PREFIX}*.sqlite3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [
        {
            "name": f.name,
            "path": f,
            "size": f.stat().st_size,
            "created_at": datetime.datetime.fromtimestamp(f.stat().st_mtime),
        }
        for f in files
    ]


def prune_auto_backups(keep=AUTO_BACKUP_KEEP):
    """فقط چند نسخه خودکار آخر را نگه می‌دارد تا دیسک پر نشود.

    نسخه‌های دستی هرگز پاک نمی‌شوند — کاربر خودش آن‌ها را ساخته.
    """
    autos = [b for b in list_backups() if b["name"].endswith("-startup.sqlite3")]
    for old in autos[keep:]:
        try:
            old["path"].unlink()
        except OSError:
            pass


def backup_on_startup():
    """در هر بار بالا آمدن برنامه یک نسخه پشتیبان می‌گیرد.

    اگر شکست بخورد نباید جلوی بالا آمدن برنامه را بگیرد؛ فقط لاگ می‌شود.
    """
    try:
        path = create_backup(label="startup")
        prune_auto_backups()
        return path
    except Exception as exc:
        logger.warning("پشتیبان‌گیری خودکار انجام نشد: %s", exc)
        return None


def restore_backup(name):
    """یک نسخه پشتیبان را جایگزین پایگاه‌داده فعلی می‌کند.

    قبل از جایگزینی، از وضعیت فعلی هم یک پشتیبان گرفته می‌شود تا اگر کاربر
    اشتباهی نسخه غلط را برگرداند، راه بازگشت داشته باشد.
    """
    target = database_path()
    if target is None:
        raise RuntimeError("بازگردانی فقط برای پایگاه‌داده SQLite در دسترس است.")

    source = backup_dir() / name
    if not source.exists() or source.parent != backup_dir():
        raise RuntimeError("نسخه پشتیبان انتخاب‌شده پیدا نشد.")

    # سلامت نسخه پشتیبان را قبل از جایگزینی می‌سنجیم
    try:
        conn = sqlite3.connect(str(source))
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise RuntimeError(f"نسخه پشتیبان قابل خواندن نیست: {exc}")

    if not result or result[0] != "ok":
        raise RuntimeError("نسخه پشتیبان سالم نیست و بازگردانی انجام نشد.")

    create_backup(label="before-restore")

    from django.db import connections

    connections.close_all()
    shutil.copy2(source, target)
    return target


def backup_filename_for_download():
    return f"پشتیبان-حسابداری-{_timestamp()}.sqlite3"
