from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="exportjob",
            name="file_hash",
            field=models.CharField(
                blank=True,
                help_text="SHA-256 hex digest of result_file",
                max_length=64,
            ),
        ),
    ]
