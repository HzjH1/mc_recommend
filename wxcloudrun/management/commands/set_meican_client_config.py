from django.core.management.base import BaseCommand, CommandError

from wxcloudrun.models import MeicanClientConfig


class Command(BaseCommand):
    help = (
        '写入 meican_client_config（key=default），字段与 mc1 config/index.js 中美餐 client 对应；'
        '未传的参数保持原值不变。'
    )

    def add_arguments(self, parser):
        parser.add_argument('--forward-id', type=str, default='', help='meicanForwardClientId')
        parser.add_argument('--forward-secret', type=str, default='', help='meicanForwardClientSecret')
        parser.add_argument('--graphql-id', type=str, default='', help='meicanGraphqlClientId（回退用）')
        parser.add_argument('--graphql-secret', type=str, default='', help='meicanGraphqlClientSecret（回退用）')
        parser.add_argument('--forward-base-url', type=str, default='', help='可选，默认 https://www.meican.com/forward')
        parser.add_argument('--graphql-app', type=str, default='', help='x-mc-app，可选')

    def handle(self, *args, **options):
        row = MeicanClientConfig.objects.filter(key='default').first()
        if row is None:
            row = MeicanClientConfig(key='default')

        mapping = (
            ('forward_id', 'forward_client_id'),
            ('forward_secret', 'forward_client_secret'),
            ('graphql_id', 'graphql_client_id'),
            ('graphql_secret', 'graphql_client_secret'),
            ('forward_base_url', 'forward_base_url'),
            ('graphql_app', 'graphql_app'),
        )
        touched = False
        for opt_key, field in mapping:
            val = options.get(opt_key)
            if val is None:
                continue
            s = str(val).strip()
            if not s:
                continue
            setattr(row, field, s)
            touched = True

        if not touched:
            raise CommandError('请至少传入一个非空参数（如 --forward-id / --forward-secret）')

        row.key = 'default'
        row.save()
        self.stdout.write(self.style.SUCCESS('已保存 meican_client_config key=default'))
