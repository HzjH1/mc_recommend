from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('wxcloudrun', '0004_usermeicanaccount_slot_namespaces'),
    ]

    operations = [
        migrations.AddField(
            model_name='autoorderconfig',
            name='default_corp_address_id_lunch',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='autoorderconfig',
            name='default_corp_address_id_dinner',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]

