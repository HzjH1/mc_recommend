from django.core.management.base import BaseCommand
from django.db import connection

from wxcloudrun import models


MODEL_CREATE_ORDER = [
    models.Counter,
    models.MeicanClientConfig,
    models.UserAccount,
    models.MenuSnapshot,
    models.CorpAddress,
    models.AutoOrderJob,
    models.UserMeicanAccount,
    models.UserPreference,
    models.AutoOrderConfig,
    models.MenuItem,
    models.RecommendationBatch,
    models.RecommendationResult,
    models.AutoOrderJobItem,
    models.OrderRecord,
]


class Command(BaseCommand):
    help = 'Create missing wxcloudrun tables in remote DB safely.'

    def handle(self, *args, **options):
        existing_tables = set(connection.introspection.table_names())
        created = []
        skipped = []

        with connection.schema_editor() as schema_editor:
            for model in MODEL_CREATE_ORDER:
                table_name = model._meta.db_table
                if table_name in existing_tables:
                    skipped.append(table_name)
                    continue
                schema_editor.create_model(model)
                existing_tables.add(table_name)
                created.append(table_name)

        self.stdout.write(self.style.SUCCESS('Created tables: {}'.format(created or '[]')))
        self.stdout.write('Skipped existing tables: {}'.format(skipped or '[]'))
