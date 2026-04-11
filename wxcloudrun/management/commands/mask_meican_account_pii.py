from django.core.management.base import BaseCommand

from wxcloudrun.models import UserMeicanAccount


def _mask_username(username: str) -> str:
    s = str(username or '').strip()
    if not s:
        return ''
    if len(s) <= 2:
        return s[0] + '*'
    if len(s) <= 4:
        return s[0] + ('*' * (len(s) - 2)) + s[-1]
    return s[:2] + ('*' * (len(s) - 4)) + s[-2:]


def _mask_email(email: str) -> str:
    s = str(email or '').strip()
    if not s or '@' not in s:
        return _mask_username(s)
    local, domain = s.split('@', 1)
    return f'{_mask_username(local)}@{domain}'


class Command(BaseCommand):
    help = 'Mask existing meican username/email in DB.'

    def handle(self, *args, **options):
        updated = 0
        for item in UserMeicanAccount.objects.all().only('id', 'meican_username', 'meican_email'):
            masked_name = _mask_username(item.meican_username)
            masked_email = _mask_email(item.meican_email)
            if masked_name == (item.meican_username or '') and masked_email == (item.meican_email or ''):
                continue
            item.meican_username = masked_name
            item.meican_email = masked_email
            item.save(update_fields=['meican_username', 'meican_email', 'updated_at'])
            updated += 1
        self.stdout.write(self.style.SUCCESS(f'masked records: {updated}'))
