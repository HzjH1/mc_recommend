from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('wxcloudrun', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userpreference',
            name='staple',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
    ]
