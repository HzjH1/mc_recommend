import json
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from wxcloudrun.models import MealSlot, UserAccount
from wxcloudrun.recommendation_service import (
    refresh_recommendations_for_user_slot,
    resolve_snapshot_recommendation_dates,
    resolve_week_start_monday,
)


class Command(BaseCommand):
    help = (
        '为指定用户按「菜单快照」重新生成推荐（写入 recommendation_batch / recommendation_result）。'
        '打分使用 recommendation_scoring 规则引擎，不调用 OpenAI/大模型（与 views.recommend_dishes 的 LLM 路径分离）。'
        '若 meican_client_config 或环境变量已配置美餐 client 且用户有 token，会先按 namespace 从美餐拉菜单落库；'
        '加 --no-meican-sync 则只读库中已有快照。'
    )

    def add_arguments(self, parser):
        parser.add_argument('--user-id', type=int, required=True, help='平台用户 ID')
        parser.add_argument('--date', type=str, default='', help='菜单日期 YYYY-MM-DD（不传则可配合 --snapshot-week）')
        parser.add_argument('--namespace', type=str, required=True, help='企业 namespace，与 menu_snapshot 一致')
        parser.add_argument(
            '--meal-slot',
            type=str,
            default='ALL',
            choices=['ALL', 'LUNCH', 'DINNER'],
            help='餐期：ALL 表示午+晚各生成一批',
        )
        parser.add_argument(
            '--freeze',
            action='store_true',
            help='生成后将批次 status 设为 FROZEN（否则为 READY）',
        )
        parser.add_argument(
            '--top-n',
            type=int,
            default=3,
            help='每餐期推荐条数，默认 3',
        )
        parser.add_argument(
            '--no-meican-sync',
            action='store_true',
            help='不从美餐拉取菜单，仅使用数据库已有 menu_snapshot',
        )
        parser.add_argument(
            '--snapshot-week',
            action='store_true',
            help='按 menu_snapshot 的该自然周日期批量生成（兼容调休日周六）',
        )
        parser.add_argument(
            '--week-start',
            type=str,
            default='',
            help='配合 --snapshot-week 使用，指定周一 YYYY-MM-DD；不传则按当前日期推算',
        )
        parser.add_argument(
            '--workdays',
            type=int,
            default=5,
            help='--snapshot-week 且该周无快照时的回退天数，默认 5',
        )

    def handle(self, *args, **options):
        user_id = options['user_id']
        namespace = str(options['namespace']).strip()
        date_raw = (options['date'] or '').strip()
        meal_arg = options['meal_slot']
        freeze = options['freeze']
        top_n = max(1, int(options['top_n']))
        sync_menu = not options['no_meican_sync']
        snapshot_week = bool(options['snapshot_week'])
        week_start_raw = (options['week_start'] or '').strip()
        workdays = max(1, int(options['workdays']))

        if not date_raw and not snapshot_week:
            raise CommandError('需要传 --date，或使用 --snapshot-week')

        date_val = None
        if date_raw:
            try:
                date_val = datetime.strptime(date_raw, '%Y-%m-%d').date()
            except ValueError as exc:
                raise CommandError('date 须为 YYYY-MM-DD') from exc

        if not namespace:
            raise CommandError('namespace 不能为空')

        try:
            user = UserAccount.objects.get(id=user_id)
        except UserAccount.DoesNotExist as exc:
            raise CommandError(f'用户不存在: user_id={user_id}') from exc

        slots = [MealSlot.LUNCH, MealSlot.DINNER] if meal_arg == 'ALL' else [meal_arg]

        if snapshot_week:
            week_start = None
            if week_start_raw:
                try:
                    week_start = datetime.strptime(week_start_raw, '%Y-%m-%d').date()
                except ValueError as exc:
                    raise CommandError('week-start 须为 YYYY-MM-DD') from exc
            elif date_val is not None:
                week_start = date_val

            monday = resolve_week_start_monday(None, week_start)
            target_dates = resolve_snapshot_recommendation_dates(namespace, monday, fallback_workdays=workdays)
        else:
            target_dates = [date_val]

        self.stdout.write(
            'hint: 本命令使用规则打分（recommendation_scoring），usesAiLlm=false；'
            '大模型推荐见 POST recommend_dishes（OPENAI_*）。'
        )

        created_batches = []
        last_ok_hint = None
        for day in target_dates:
            for meal_slot in slots:
                out = refresh_recommendations_for_user_slot(
                    user,
                    namespace,
                    day,
                    meal_slot,
                    freeze=freeze,
                    top_n=top_n,
                    sync_menu_if_missing=sync_menu,
                )
                if out.get('ok'):
                    created_batches.append(
                        f'{day} {meal_slot}:batch_id={out["batch_id"]},version={out["version"]},status={out["status"]}'
                    )
                    last_ok_hint = out.get('hint')
                else:
                    self.stdout.write(self.style.WARNING(f'跳过 {day} {meal_slot}：{out.get("skip", "unknown")}'))
                    if out.get('hint'):
                        self.stdout.write(self.style.WARNING(json.dumps(out['hint'], ensure_ascii=False)))

        if not created_batches:
            raise CommandError('未生成任何推荐批次（请检查菜单快照与菜品是否存在；查看上方 hint）')

        self.stdout.write(self.style.SUCCESS('OK ' + '; '.join(created_batches)))
        if last_ok_hint:
            self.stdout.write(json.dumps(last_ok_hint, ensure_ascii=False))
