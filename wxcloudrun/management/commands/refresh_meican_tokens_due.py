"""
与 POST /api/v1/internal/meican/tokens/refresh-due 相同逻辑，供 crontab 直接调 Django：

  cd mc_recommend/mc_recommend && python manage.py refresh_meican_tokens_due --limit=100

或配合云托管定时触发器每 5～10 分钟执行一次。
"""

from django.core.management.base import BaseCommand

from wxcloudrun.meican_oauth import MeicanOAuthError, ensure_valid_access_token, iter_user_ids_due_for_refresh


class Command(BaseCommand):
    help = 'Refresh Meican access tokens that are empty, missing expiry, or expiring within the skew window.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--within-seconds',
            type=int,
            default=None,
            dest='within_seconds',
            help='Same as refresh-due withinSeconds; default uses MEICAN_TOKEN_REFRESH_SKEW_SECONDS.',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=50,
            help='Max users to process (1–500).',
        )

    def handle(self, *args, **options):
        within = options['within_seconds']
        limit = options['limit']
        user_ids = iter_user_ids_due_for_refresh(within_seconds=within, limit=limit)
        ok = 0
        failed = 0
        refreshed = 0
        for uid in user_ids:
            try:
                account, did_refresh = ensure_valid_access_token(uid)
                ok += 1
                if did_refresh:
                    refreshed += 1
                    self.stdout.write(f'user {uid}: refreshed exp={account.token_expire_at}')
            except MeicanOAuthError as exc:
                failed += 1
                self.stderr.write(f'user {uid}: {exc}')
        self.stdout.write(
            self.style.SUCCESS(
                f'meican refresh-due: candidates={len(user_ids)} ok={ok} refreshed={refreshed} failed={failed}'
            )
        )
