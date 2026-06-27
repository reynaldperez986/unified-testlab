"""Add category, sub_category, increment/decrement and calculate_on columns to the data table."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("recorder", "0007_data_global_formula"),
    ]

    operations = [
        migrations.RunSQL(
            sql="ALTER TABLE data ADD COLUMN IF NOT EXISTS category VARCHAR(100);",
            reverse_sql="ALTER TABLE data DROP COLUMN IF EXISTS category;",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE data ADD COLUMN IF NOT EXISTS sub_category VARCHAR(100);",
            reverse_sql="ALTER TABLE data DROP COLUMN IF EXISTS sub_category;",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE data ADD COLUMN IF NOT EXISTS increment_value NUMERIC;",
            reverse_sql="ALTER TABLE data DROP COLUMN IF EXISTS increment_value;",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE data ADD COLUMN IF NOT EXISTS increment_frequency VARCHAR(50);",
            reverse_sql="ALTER TABLE data DROP COLUMN IF EXISTS increment_frequency;",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE data ADD COLUMN IF NOT EXISTS decrement_value NUMERIC;",
            reverse_sql="ALTER TABLE data DROP COLUMN IF EXISTS decrement_value;",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE data ADD COLUMN IF NOT EXISTS decrement_frequency VARCHAR(50);",
            reverse_sql="ALTER TABLE data DROP COLUMN IF EXISTS decrement_frequency;",
        ),
        migrations.RunSQL(
            sql="ALTER TABLE data ADD COLUMN IF NOT EXISTS calculate_on VARCHAR(50);",
            reverse_sql="ALTER TABLE data DROP COLUMN IF EXISTS calculate_on;",
        ),
    ]
