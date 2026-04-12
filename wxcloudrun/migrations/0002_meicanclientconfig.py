# Generated manually for MeicanClientConfig

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('wxcloudrun', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MeicanClientConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(default='default', max_length=32, unique=True)),
                ('forward_client_id', models.CharField(blank=True, default='', max_length=128)),
                ('forward_client_secret', models.CharField(blank=True, default='', max_length=256)),
                ('graphql_client_id', models.CharField(blank=True, default='', max_length=128)),
                ('graphql_client_secret', models.CharField(blank=True, default='', max_length=256)),
                ('forward_base_url', models.CharField(blank=True, default='', max_length=128)),
                ('graphql_app', models.CharField(blank=True, default='', max_length=256)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'meican_client_config',
            },
        ),
    ]
