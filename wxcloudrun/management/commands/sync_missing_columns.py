from django.core.management.base import BaseCommand
from django.db import connection

from wxcloudrun import models


MODEL_ORDER = [
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
    help = 'Add missing columns to existing wxcloudrun tables safely.'

    def handle(self, *args, **options):
        existing_tables = set(connection.introspection.table_names())
        added_columns = []
        skipped_columns = []

        with connection.cursor() as cursor, connection.schema_editor() as schema_editor:
            for model in MODEL_ORDER:
                table_name = model._meta.db_table
                if table_name not in existing_tables:
                    continue

                table_columns = {
                    col.name.lower()
                    for col in connection.introspection.get_table_description(cursor, table_name)
                }

                for field in model._meta.local_fields:
                    column_name = field.column
                    if column_name.lower() in table_columns:
                        skipped_columns.append(f'{table_name}.{column_name}')
                        continue
                    schema_editor.add_field(model, field)
                    table_columns.add(column_name.lower())
                    added_columns.append(f'{table_name}.{column_name}')

        self.stdout.write(self.style.SUCCESS('Added columns: {}'.format(added_columns or '[]')))
        self.stdout.write('Skipped existing columns: {}'.format(skipped_columns or '[]'))
