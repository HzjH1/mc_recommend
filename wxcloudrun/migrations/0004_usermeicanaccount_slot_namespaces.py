from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('wxcloudrun', '0003_meicanclientconfig_forward_headers'),
    ]

    operations = [
        migrations.AddField(
            model_name='usermeicanaccount',
            name='account_namespace_lunch',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='usermeicanaccount',
            name='account_namespace_dinner',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]

