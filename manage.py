#!/usr/bin/env python
"""ابزار خط فرمان جنگو برای کارهای مدیریتی."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "جنگو نصب نیست. ابتدا دستور زیر را اجرا کنید:\n"
            "    pip install -r requirements.txt"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
