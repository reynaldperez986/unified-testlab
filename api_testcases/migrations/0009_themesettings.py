from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api_testcases', '0008_add_form_data'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ThemeSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(default='default', max_length=50, unique=True)),
                ('theme_mode', models.CharField(choices=[('light', 'Light'), ('dark', 'Dark')], default='light', max_length=10)),
                ('primary_color', models.CharField(default='#0f766e', max_length=7)),
                ('accent_color', models.CharField(default='#c0841f', max_length=7)),
                ('background_color', models.CharField(default='#eef3f7', max_length=7)),
                ('surface_color', models.CharField(default='#ffffff', max_length=7)),
                ('sidebar_start_color', models.CharField(default='#0f172a', max_length=7)),
                ('sidebar_end_color', models.CharField(default='#1e293b', max_length=7)),
                ('text_color', models.CharField(default='#0f172a', max_length=7)),
                ('border_color', models.CharField(default='#dbe3ec', max_length=7)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='updated_themes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
    ]

