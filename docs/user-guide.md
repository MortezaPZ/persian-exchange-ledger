# Currency-exchange ledger

This is the day-to-day guide. No server knowledge is required.

## 1. Sign in

Open the address in a browser on a computer or a phone. Enter your username and password.

Passwords are stored as hashes. An administrator can reset a password and cannot read it. Successful and failed sign-ins are written to the audit log with the time and the IP address. Change your own password from the account menu.

## 2. Dashboard

| Block | What it shows |
| --- | --- |
| Today and this month | Count and totals of buys and sells |
| Realized profit this month | Profit already earned. Visible to the manager and the partner |
| House balances | Each bank, cash box, and currency position |
| Latest rates | The newest rate stored for each currency |
| Largest customer balances | The ten largest |
| Recent documents | The last twelve |

Balances and rates refresh about every 20 seconds.

A red health banner means the books for a currency do not sum to zero. That banner should stay hidden. If it appears, stop and report it.

## 3. Parties

A party is anyone or any account that can owe money or be owed money.

| Kind | Example | Currency |
| --- | --- | --- |
| Customer | A person you trade with | Leave blank. Customers are multi-currency |
| Bank | A bank account you hold | Required |
| Cash box | Cash on hand | Required |
| House currency position | Created by the application | — |

Banks live in the same list as customers because every event has two sides. A deposit changes the customer and the bank. Both balances stay current.

Create a party under Accounts, then Parties.

- Kind: customer
- Name: the person's name
- Account currency: blank for a customer
- Telegram or WhatsApp id: used later by a bot. If it is blank, the bot does not answer that customer

Parties are not deleted. Clear the active flag when you stop trading with someone. History stays.

## 4. Rates

Settings, then exchange rates.

Manual entry is always available. Pick the currency, enter the rate, and save.

An internet feed is optional. Add the source under rate feeds and use "fetch now". Yesterday's rate is not overwritten. Both rows stay, so a report printed later uses the rate that was current on that day.

Base amounts are stored in rials. With `DISPLAY_UNIT=toman`, the screen divides by 10 and multiplies by 10 on input. Changing the display unit does not rewrite stored rows. If a feed quotes toman and storage is rial, set the feed factor to 10.

## 5. Buy and sell

Daily operations, then record a trade. This is the screen used most often.

Example: sell 8300 dirham at 51200 toman to a customer.

| Field | Value |
| --- | --- |
| Side | Sale |
| Date | Today, unless you change it |
| Party | The customer |
| Currency | Dirham |
| Quantity | 8300 |
| Rate per unit | 51200 |
| Total | Filled by the application: 8300 × 51200 |
| Settlement account | The bank, if cash moved now. Otherwise leave it blank |
| Description | Leave blank to use the generated text |

The customer's current balance is shown beside the form.

The total is computed. It is not typed. A sample spreadsheet once stored 10000 × 51200 as 51,200,000 instead of 512,000,000. That class of error cannot be entered here.

A sale gives the customer the currency. The customer then owes the rial amount, and the house owes the currency until the transfer is sent. A purchase is the reverse.

Posting:

1. The browser checks required fields.
2. The server checks permission, that the party is active, and that the rate is usable.
3. The application writes four lines and requires each currency to sum to zero. Any difference rejects the whole document.
4. The document receives a number and is posted.
5. The audit log records who did it.

Date and party stay filled so the next trade does not start from a blank form.

## 6. Receipts and payments

A receipt is money the customer paid you. Example: 50,000,000 toman into a named bank.

- Party: the customer
- Currency: the currency received
- Bank or cash box: where it landed
- Amount: the amount
- Description: for example the transfer reference

The customer's debt falls and that bank's balance rises. A payment is the same form in the other direction.

## 7. Internal transfer

Moves money between your own accounts: cash box to bank, or one bank to another. No customer is on the document.

## 8. Customer statement

Accounts, then customer statement. Pick the customer.

You see the balance in each currency, a toman value at the latest rate, and a line-by-line running balance.

Red means the customer owes you. Green means you owe the customer.

The balance is not stored. The page sums the lines when it opens, so the balance cannot drift from the lines. Filters are date range and currency. Export is print, Excel, and PDF.

## 9. Reversing a document

A posted document is not edited and is not deleted, including by a manager. A report that was already shown to a customer would otherwise stop being trustworthy.

Open the document in the journal and choose reverse. A reason is required. The application posts a new document with the opposite amounts. The original stays, marked void. Its financial effect is cancelled. Both documents remain in the audit trail. Then post the correct document.

## 10. Reports

Every report takes a date range and can go to Excel, PDF, or print.

| Report | Contents |
| --- | --- |
| Daily buys and sells | Totals by day and currency, average rate, and the lines |
| Profit and loss | Realized profit and revaluation |
| Trial balance | Debit and credit per currency, plus the health check |
| Banks | Banks, cash boxes, and currency positions |
| Customers | Every customer, every currency, plus a rial value |

Realized profit is profit from a sale that already happened. Revaluation is the paper change in currency you still hold.

Profit uses a weighted average cost. Buying 100 dirham at 50000 and 100 at 52000 sets the average at 51000. Selling 50 at 53000 realizes (53000 − 51000) × 50. If today's rate is missing, the report says so. It does not guess.

Settlement allocates a document to an invoice and does not create a new accounting line. It settles the invoice's base-currency balance. Delivering dollars, dirham, or tether is a separate fact from settling the rial side. Do not treat a currency transfer as rial without a delivery model and a stated rate.

## 11. Users and roles

Settings, then users, and settings, then roles.

| Role | Access |
| --- | --- |
| Manager | Everything, including users |
| Partner | Everything except user administration |
| Staff | Trades and account movement. No profit, no administration |
| Observer | Read only. Cannot post |

Roles and permissions are separate. A staff member can be given the profit report without being allowed to reverse a document. Hiding a menu is not the check. The server rejects the same request. A partner in another city uses a normal partner account in a browser. There is no geographic lock in the application.

## 12. Import from a spreadsheet

Run a check before anything is written:

```bash
python manage.py import_sheet "path/to/file.xlsx" --mode check
```

The check reports rows whose stored total does not equal quantity times rate. A sample file had:

- row 15: 10000 × 51200 should be 512,000,000 and 51,200,000 was stored
- row 20: 1500 × 50800 should be 76,200,000 and 76,200 was stored

After those rows are decided, post opening balances:

```bash
python manage.py import_sheet "path/to/file.xlsx" --mode opening --user admin
```

The practical import is today's balance per customer. Older rows stay in the spreadsheet, because cleaning historical errors takes longer than starting from a checked opening balance. Opening balances can also be typed under Settings.

## 13. Common questions

The phone uses the same address. The layout collapses and the menu becomes a drawer.

A power cut or a dropped connection does not leave a half document. The database rolls the transaction back.

Automatic posts, including a bot or an import, carry an idempotency key. A manual post shows the previous document number so a double entry is visible.

The audit log is under Settings. It records who, when, from which IP, and what changed.

Add a currency under Settings. One row is enough. No code change.

Enter amounts in the display unit you configured. Storage stays in rials. Commas are optional. The field inserts thousands separators as you type.

Dates are Jalali. A dotted sheet date such as `17.05.1405` is accepted.
