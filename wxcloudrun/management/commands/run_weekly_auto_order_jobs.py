from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError

from wxcloudrun.models import MealSlot, RecommendationBatch
from wxcloudrun.recommendation_service import resolve_week_start_monday
from wxcloudrun.v1_views import run_auto_order_job_for_date_slot


class Command(BaseCommand):
    help = (
        '按一周维度批量触发自动订餐任务：优先使用 recommendation_batch 的日期，'
        '依次触发 LUNCH / DINNER 的 AutoOrderJob。'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--week-start',
            type=str,
            default='',
            help='该周周一日期 YYYY-MM-DD；不传则按运行日推算（周日跑则取下周一）',
        )
        parser.add_argument('--workdays', type=int, default=5, help='无批次日期时回退天数，默认 5')
        parser.add_argument('--force', action='store_true', help='存在同日同餐期任务时重建任务项')

    def handle(self, *args, **options):
        ws = (options['week_start'] or '').strip()
        week_start = None
        if ws:
            try:
                week_start = datetime.strptime(ws, '%Y-%m-%d').date()
            except ValueError as exc:
                raise CommandError('week-start 须为 YYYY-MM-DD') from exc

        workdays = max(1, int(options['workdays']))
        monday = resolve_week_start_monday(None, week_start)
        week_end = monday + timedelta(days=6)

        dates = list(
            RecommendationBatch.objects.filter(date__gte=monday, date__lte=week_end)
            .order_by('date')
            .values_list('date', flat=True)
            .distinct()
        )
        if not dates:
            dates = [monday + timedelta(days=i) for i in range(workdays)]

        created = []
        skipped = []
        for day in dates:
            for meal_slot in (MealSlot.LUNCH, MealSlot.DINNER):
                out = run_auto_order_job_for_date_slot(
                    day,
                    meal_slot,
                    force=bool(options['force']),
                    trigger_type='SCHEDULED',
                    enforce_window=False,
                )
                if out.get('ok'):
                    created.append({'date': str(day), 'mealSlot': meal_slot, **out['data']})
                else:
                    skipped.append({
                        'date': str(day),
                        'mealSlot': meal_slot,
                        'code': out.get('code'),
                        'message': out.get('message', 'unknown'),
                    })

        self.stdout.write(self.style.SUCCESS(
            f"weekStartMonday={monday} dates={len(dates)} created={len(created)} skipped={len(skipped)}"
        ))
        if skipped:
            for row in skipped[:50]:
                self.stdout.write(self.style.WARNING(str(row)))
