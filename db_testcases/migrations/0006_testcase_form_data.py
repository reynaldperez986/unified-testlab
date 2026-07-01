from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("db_testcases", "0005_rename_testcases_a_user_id_fb4eae_idx_db_testcase_user_id_f2cc78_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="testcase",
            name="form_data",
            field=models.TextField(
                blank=True,
                default="[]",
                help_text="JSON array of expected key/value rows [{key, value}]",
            ),
        ),
    ]
