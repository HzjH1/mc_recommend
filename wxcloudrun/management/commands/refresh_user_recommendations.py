from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from wxcloudrun.models import MealSlot, UserAccount
from wxcloudrun.recommendation_service import refresh_recommendations_for_user_slot


class Command(BaseCommand):
    help = (
        '为指定用户按「菜单快照」重新生成推荐（写入 recommendation_batch / recommendation_result）。'
        '依赖库中已存在 menu_snapshot + menu_item。'
    )

    def add_arguments(self, parser):
        parser.add_argument('--user-id', type=int, required=True, help='平台用户 ID')
        parser.add_argument('--date', type=str, required=True, help='菜单日期 YYYY-MM-DD')
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

    def handle(self, *args, **options):
        user_id = options['user_id']
        namespace = str(options['namespace']).strip()
        date_raw = options['date']
        meal_arg = options['meal_slot']
        freeze = options['freeze']
        top_n = max(1, int(options['top_n']))

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

        created_batches = []
        for meal_slot in slots:
            out = refresh_recommendations_for_user_slot(
                user, namespace, date_val, meal_slot, freeze=freeze, top_n=top_n
            )
            if out.get('ok'):
                created_batches.append(
                    f'{meal_slot}:batch_id={out["batch_id"]},version={out["version"]},status={out["status"]}'
                )
            else:
                self.stdout.write(self.style.WARNING(f'跳过 {meal_slot}：{out.get("skip", "unknown")}'))

        if not created_batches:
            raise CommandError('未生成任何推荐批次（请检查菜单快照与菜品是否存在）')

        self.stdout.write(self.style.SUCCESS('OK ' + '; '.join(created_batches)))
