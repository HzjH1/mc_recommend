from datetime import datetime

from django.core.management.base import BaseCommand

from wxcloudrun.recommendation_service import run_weekly_recommendation_job


class Command(BaseCommand):
    help = (
        '每周推荐任务（与「每周日定时」配套）：为已绑定 namespace 且有偏好的用户，'
        '生成从「下周一」或 --week-start 起的 5 个工作日、午/晚推荐。'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--week-start',
            type=str,
            default='',
            help='该周周一日期 YYYY-MM-DD；不传则按运行日推算（周日跑则取下周一）',
        )
        parser.add_argument('--freeze', action='store_true', help='批次标记为 FROZEN')
        parser.add_argument('--top-n', type=int, default=3)
        parser.add_argument('--workdays', type=int, default=5)
        parser.add_argument('--user-id', type=int, default=None, help='仅处理指定用户')

    def handle(self, *args, **options):
        ws = options['week_start'].strip()
        week_start = None
        if ws:
            week_start = datetime.strptime(ws, '%Y-%m-%d').date()

        summary = run_weekly_recommendation_job(
            week_start=week_start,
            freeze=options['freeze'],
            top_n=options['top_n'],
            workdays=options['workdays'],
            user_id=options['user_id'],
        )
        self.stdout.write(self.style.SUCCESS(
            f"weekStartMonday={summary['weekStartMonday']} created={len(summary['created'])} skipped={len(summary['skipped'])}"
        ))
