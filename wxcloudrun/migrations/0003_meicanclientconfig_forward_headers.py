from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('wxcloudrun', '0002_meicanclientconfig'),
    ]

    operations = [
        migrations.AddField(
            model_name='meicanclientconfig',
            name='forward_referer',
            field=models.CharField(blank=True, default='', max_length=512),
        ),
        migrations.AddField(
            model_name='meicanclientconfig',
            name='forward_user_agent',
            field=models.CharField(blank=True, default='', max_length=512),
        ),
        migrations.AddField(
            model_name='meicanclientconfig',
            name='x_mc_device',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
