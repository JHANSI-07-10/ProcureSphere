"""
Reconcile schema drift on vendors_vendorcredentialdispatch.

Some environments (e.g. the Render database) were migrated while migration 0003 still had a
different shape, so the table can carry extra NOT NULL columns (observed: ``legal_notes``) that
this repository's model and migrations never define. Django cannot insert into such a table:
``null value in column "legal_notes" ... violates not-null constraint``.

This migration is idempotent and non-destructive:
  * it only touches PostgreSQL columns that are NOT part of the model, are NOT NULL and have no
    database default;
  * it only drops the NOT NULL constraint - the column and every stored value are kept;
  * on a database that matches the model (fresh installs, local, CI) it does nothing.
"""

from django.db import migrations

TABLE = "vendors_vendorcredentialdispatch"


def relax_orphan_columns(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql":
        return
    model = apps.get_model("vendors", "VendorCredentialDispatch")
    known = {field.column for field in model._meta.concrete_fields}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
              AND is_nullable = 'NO'
              AND column_default IS NULL
            """,
            [TABLE],
        )
        orphans = [name for (name,) in cursor.fetchall() if name not in known]
        for name in orphans:
            cursor.execute(
                "ALTER TABLE %s ALTER COLUMN %s DROP NOT NULL"
                % (schema_editor.quote_name(TABLE), schema_editor.quote_name(name))
            )


class Migration(migrations.Migration):

    dependencies = [
        ("vendors", "0005_vendordocument_review_status"),
    ]

    operations = [
        migrations.RunPython(relax_orphan_columns, migrations.RunPython.noop),
    ]
