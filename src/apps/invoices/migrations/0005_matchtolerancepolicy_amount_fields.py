from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("invoices", "0004_alter_invoiceline_quantity_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="matchtolerancepolicy",
            name="price_tolerance_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Absolute price tolerance per line (e.g. 500.00). Pass if either % or amount is within bounds.",
                max_digits=14,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="matchtolerancepolicy",
            name="qty_tolerance_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Absolute quantity tolerance per line (e.g. 2.00). Pass if either % or amount is within bounds.",
                max_digits=12,
                null=True,
            ),
        ),
    ]
