from django.db import migrations, models
import django.core.validators
import django.db.models.deletion
from decimal import Decimal


class Migration(migrations.Migration):
    dependencies = [("ledger", "0002_alter_voucher_kind")]
    operations = [
        migrations.CreateModel(
            name="InvoiceSettlement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=8, max_digits=28, validators=[django.core.validators.MinValueValidator(Decimal("0.00000001"))], verbose_name="مبلغ تخصیص")),
                ("note", models.CharField(blank=True, max_length=255, verbose_name="توضیحات")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="settlements_created", to="accounts.user", verbose_name="ثبت‌کننده")),
                ("deal", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="settlements", to="ledger.deal", verbose_name="فاکتور")),
                ("payment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="invoice_settlements", to="ledger.voucher", verbose_name="سند مالی")),
            ],
            options={"verbose_name": "تخصیص تسویه", "verbose_name_plural": "تخصیص‌های تسویه"},
        ),
        migrations.CreateModel(
            name="VoucherAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="payment_receipts/%Y/%m/", verbose_name="فایل پیوست")),
                ("title", models.CharField(blank=True, max_length=255, verbose_name="عنوان")),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("uploaded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="voucher_attachments", to="accounts.user", verbose_name="بارگذاری‌کننده")),
                ("voucher", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="ledger.voucher", verbose_name="سند")),
            ],
            options={"verbose_name": "پیوست سند", "verbose_name_plural": "پیوست‌های سند"},
        ),
        migrations.AddConstraint(model_name="invoicesettlement", constraint=models.UniqueConstraint(fields=("deal", "payment"), name="unique_deal_payment_settlement")),
        migrations.AddIndex(model_name="invoicesettlement", index=models.Index(fields=["deal", "payment"], name="ledger_invo_deal_id_6d9f0c_idx")),
    ]
