"""آزمون‌های تاریخ شمسی، مبالغ و نمایش."""
import datetime
from decimal import Decimal

from django.template import Context, Template
from django.test import TestCase, override_settings

from core import jalali, money
from core.models import Currency


class JalaliTests(TestCase):
    def test_parses_standard_format(self):
        self.assertEqual(jalali.parse_jalali("1405/05/17"), datetime.date(2026, 8, 8))

    def test_parses_persian_digits(self):
        self.assertEqual(jalali.parse_jalali("۱۴۰۵/۰۵/۱۷"), datetime.date(2026, 8, 8))

    def test_parses_client_sheet_format(self):
        """فرمت «روز.ماه.سال» که در فایل کارفرما استفاده شده."""
        self.assertEqual(jalali.parse_jalali("17.05.1405"), datetime.date(2026, 8, 8))
        self.assertEqual(jalali.parse_jalali("10.05.1405 "), datetime.date(2026, 8, 1))

    def test_parses_year_first_dotted(self):
        self.assertEqual(jalali.parse_jalali("1405.05.17"), datetime.date(2026, 8, 8))

    def test_round_trip(self):
        for text in ["1405/01/01", "1405/12/29", "1404/06/31"]:
            gregorian = jalali.parse_jalali(text)
            self.assertEqual(jalali.format_jalali(gregorian), text)

    def test_rejects_invalid(self):
        for bad in ["1405/13/01", "1405/05/32", "سلام", "05/17"]:
            with self.assertRaises(ValueError, msg=f"باید رد می‌شد: {bad}"):
                jalali.parse_jalali(bad)

    def test_empty_returns_none(self):
        self.assertIsNone(jalali.parse_jalali(""))
        self.assertIsNone(jalali.parse_jalali(None))


@override_settings(DISPLAY_UNIT="toman")
class MoneyDisplayTests(TestCase):
    def test_rial_stored_toman_displayed(self):
        self.assertEqual(money.to_display(Decimal("5000000")), Decimal("500000"))
        self.assertEqual(money.from_display(Decimal("500000")), Decimal("5000000"))

    def test_round_trip_is_lossless(self):
        original = Decimal("4249600000")
        self.assertEqual(money.from_display(money.to_display(original)), original)

    def test_unit_label(self):
        self.assertEqual(money.base_unit_label(), "تومان")

    def test_formatting(self):
        self.assertEqual(money.format_amount(Decimal("424960000")), "424,960,000")
        self.assertEqual(money.format_amount(Decimal("-8300"), 2), "−8,300.00")


@override_settings(DISPLAY_UNIT="rial")
class RialDisplayTests(TestCase):
    def test_no_conversion_in_rial_mode(self):
        self.assertEqual(money.to_display(Decimal("5000000")), Decimal("5000000"))
        self.assertEqual(money.base_unit_label(), "ریال")


@override_settings(DISPLAY_UNIT="toman")
class TemplateTagTests(TestCase):
    """مبالغ ارز پایه باید همیشه به واحد نمایش تبدیل شوند — حتی وقتی ارز None است."""

    @classmethod
    def setUpTestData(cls):
        cls.rial = Currency.objects.create(code="IRR", name="ریال", decimal_places=0,
                                           is_base=True)
        cls.aed = Currency.objects.create(code="AED", name="درهم", decimal_places=2)

    def render(self, template_string, **context):
        return Template("{% load sarrafi %}" + template_string).render(Context(context))

    def test_base_currency_converted_to_toman(self):
        output = self.render("{% plain_money amount currency %}",
                             amount=Decimal("7793850000"), currency=self.rial)
        self.assertIn("779,385,000", output.translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))

    def test_none_currency_treated_as_base(self):
        """جمع کل ریالی که با currency=None می‌آید هم باید تومان شود."""
        output = self.render("{% plain_money amount None %}", amount=Decimal("7793850000"))
        normalized = output.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        self.assertIn("779,385,000", normalized)
        self.assertNotIn("7,793,850,000", normalized)

    def test_foreign_currency_not_converted(self):
        output = self.render("{% plain_money amount currency %}",
                             amount=Decimal("-19800"), currency=self.aed)
        normalized = output.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
        self.assertIn("19,800.00", normalized)

    def test_debit_is_red_credit_is_green(self):
        debit = self.render("{% money_cell amount currency %}",
                            amount=Decimal("1000"), currency=self.aed)
        credit = self.render("{% money_cell amount currency %}",
                             amount=Decimal("-1000"), currency=self.aed)
        self.assertIn("debit", debit)
        self.assertIn("بدهکار", debit)
        self.assertIn("credit", credit)
        self.assertIn("بستانکار", credit)
