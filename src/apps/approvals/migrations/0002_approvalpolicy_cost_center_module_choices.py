from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("approvals", "0001_initial"),
        ("organization", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="approvalpolicy",
            name="cost_center",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="approval_policies",
                to="organization.costcenter",
            ),
        ),
        migrations.AlterField(
            model_name="approvalpolicy",
            name="module",
            field=models.CharField(
                choices=[
                    ("PR", "Purchase Requisition"),
                    ("PO", "Purchase Order"),
                    ("SOURCING", "Sourcing Award"),
                    ("INVOICE", "Invoice Match Exception"),
                    ("VENDOR", "Vendor KYC Approval"),
                    ("PO_AMENDMENT", "PO Amendment"),
                    ("CONTRACT", "Contract"),
                    ("BUDGET_EXCEPTION", "Budget Exception"),
                ],
                max_length=50,
            ),
        ),
    ]
