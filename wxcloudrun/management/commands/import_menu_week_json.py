import json

from django.core.management.base import BaseCommand, CommandError

from wxcloudrun.menu_sync_service import normalize_days_payload, sync_menu_days


class Command(BaseCommand):
    help = '从 JSON 文件导入菜单快照（与 POST .../menu/week-sync 请求体格式相同）。'

    def add_arguments(self, parser):
        parser.add_argument('--file', type=str, required=True, help='JSON 文件路径')
        parser.add_argument('--namespace', type=str, default='', help='覆盖文件中的 namespace')

    def handle(self, *args, **options):
        path = options['file']
        try:
            with open(path, 'r', encoding='utf-8') as f:
                body = json.load(f)
        except OSError as exc:
            raise CommandError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise CommandError('JSON 解析失败') from exc

        namespace = (options.get('namespace') or '').strip() or str(body.get('namespace') or '').strip()
        if not namespace:
            raise CommandError('缺少 namespace（文件内或 --namespace）')

        days, derr = normalize_days_payload(body)
        if derr:
            raise CommandError(derr)

        out = sync_menu_days(namespace, days)
        if out.get('fatal'):
            raise CommandError(str(out.get('errors')))
        self.stdout.write(self.style.SUCCESS(
            'slotsSynced=%s errors=%s' % (out['slots_synced'], len(out['errors']))
        ))
        for e in out['errors'][:20]:
            self.stdout.write(self.style.WARNING(str(e)))
