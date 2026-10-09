# Currency Exchange Accounting

A multi-currency double-entry ledger for a currency exchange. Balances are computed from the lines. A posted document is corrected by a reversing document, not by editing the original.

## Overview

Staff record trades, receipts, payments, and internal transfers. Every financial event is a document. The sum of lines in each currency must be zero, or nothing is stored. Access control is enforced on the server.

## Features

- Double-entry documents with a signed amount: positive is debit, negative is credit
- No stored customer balance, except inventory position, which has to be sequential for the weighted average
- Posted documents are not edited or deleted
- Customers, documents, and rates are not hard-deleted
- Manual rates and a JSON rate feed
- Excel and PDF reports
- Google Sheet import for an opening balance, with a check mode that writes nothing
- Server-side roles and permissions

## Technology Stack

- Django 5.2
- PostgreSQL in production, SQLite for a local demo
- Django templates
- No third-party JavaScript

## Architecture

```
config/      settings and URLs
accounts/    users, roles, permissions, change history
core/        currencies, parties, rates, Jalali dates, money
ledger/      documents, lines, trades, posting, balances
reports/     reports and Excel/PDF export
templates/   the interface
docs/        the user guide
```

Base-currency amounts are stored in rials. `DISPLAY_UNIT=toman` divides by 10 for display and multiplies by 10 on input. Changing the display unit does not rewrite stored rows.

A sale of 8300 dirham at 51200 toman is four lines: the exchange's currency position and the customer in dirham, then the customer and the settlement account in rial. Each currency still sums to zero.

`SELECT FOR UPDATE` on the weighted-average calculation is meaningful on PostgreSQL. Use PostgreSQL for a real deployment.

## Installation

```bash
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py setup_sarrafi --admin-user admin --admin-pass "choose-a-strong-password"
python manage.py runserver
```

Open `http://127.0.0.1:8000`.

```env
DB_ENGINE=postgres
DB_NAME=sarrafi
DB_USER=postgres
DB_PASSWORD=...
DB_HOST=127.0.0.1
DB_PORT=5432
DISPLAY_UNIT=toman
```

Use `DB_ENGINE=sqlite` only for a quick demo.

`setup_sarrafi` creates permissions, roles, currencies (rial, dirham, dollar, tether), system accounts, and the admin user. Running it again is safe.

## Usage

Check a spreadsheet without writing:

```bash
python manage.py import_sheet "file.xlsx" --mode check
python manage.py import_sheet "file.xlsx" --mode opening --user admin
```

Rate feeds return JSON. A path such as `aed.value` reads the number. If the feed is in toman and storage is in rial, set the conversion factor to 10. Array paths such as `data.0.price` are supported.

Production:

```env
DEBUG=False
SECRET_KEY=<a long random string>
ALLOWED_HOSTS=example.com
CSRF_TRUSTED_ORIGINS=https://example.com
DB_ENGINE=postgres
```

With `DEBUG=False` the project turns on HTTPS redirect, secure cookies, HSTS, and content-type sniffing protection.

```bash
python manage.py collectstatic
gunicorn config.wsgi:application
```

The screen-by-screen guide is `docs/user-guide.md`.

## Testing

```bash
python manage.py test
```

The suite covers posting rules, balances, weighted average, reversals, duplicate protection, Jalali dates, unit conversion, and access control.

## Limitations

A messaging bot for balance inquiries is not built. The `telegram_id` and `whatsapp_no` fields, and the bot message table, are reserved. A full row-by-row history import (`import_sheet --mode full`) is not finished. Persian PDF shaping needs `arabic-reshaper` and `python-bidi` if you want joined letters. Without them the PDF still generates.

## License

MIT. See [LICENSE](LICENSE).
