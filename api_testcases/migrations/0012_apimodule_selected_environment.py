from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api_testcases', '0011_alter_environment_auth_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='apimodule',
            name='selected_environment',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name='selected_for_api_modules',
                to='api_testcases.environment',
            ),
        ),
    ]
