"""
美餐 OAuth refresh_token 换票，与小程序 `services/meican/api.js` 中 `refreshMeicanToken` 对齐：
- POST https://www.meican.com/forward/api/v2.1/oauth/token?client_id&client_secret
- body: application/x-www-form-urlencoded grant_type=refresh_token&refresh_token=...
- 请求头: clientId, clientSecret, Referer（与 forward 一致）
"""
import json
from datetime import timedelta
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from wxcloudrun.models import UserMeicanAccount

MEICAN_OAUTH_TOKEN_URL = 'https://www.meican.com/forward/api/v2.1/oauth/token'


class MeicanOAuthError(Exception):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


def _client_config():
    cid = str(getattr(settings, 'MEICAN_CLIENT_ID', '') or '').strip()
    csec = str(getattr(settings, 'MEICAN_CLIENT_SECRET', '') or '').strip()
    return cid, csec


def get_meican_oauth_client_id():
    """供 /meican/access 回传，便于小程序与本地 forward 的 client_id 比对。"""
    cid, _ = _client_config()
    return cid


def _pick_token_field(payload, *keys):
    cur = payload
    for key in keys:
        if not isinstance(cur, dict):
            return None
        if '.' in key:
            nested = payload
            ok = True
            for part in key.split('.'):
                if not isinstance(nested, dict) or part not in nested:
                    ok = False
                    break
                nested = nested.get(part)
            if ok and nested is not None and nested != '':
                return nested
            cur = payload
            continue
        val = cur.get(key)
        if val is not None and val != '':
            return val
    return None


def _parse_oauth_json(raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def call_meican_refresh_token(refresh_token: str):
    """
    调用美餐 oauth/token 换 access_token（不带 Bearer）。
    返回 dict: access_token, refresh_token, expires_in(可选)
    """
    cid, csec = _client_config()
    if not cid or not csec:
        raise MeicanOAuthError('MEICAN_CLIENT_NOT_CONFIGURED')

    url = f'{MEICAN_OAUTH_TOKEN_URL}?{urlencode({"client_id": cid, "client_secret": csec})}'
    body = urlencode({
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
    })

    req = urllib_request.Request(url, data=body.encode('utf-8'), method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    req.add_header('clientId', cid)
    req.add_header('clientSecret', csec)
    req.add_header('Referer', 'https://www.meican.com/')

    try:
        with urllib_request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
            status = getattr(resp, 'status', 200) or 200
    except urllib_error.HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace') if e.fp else ''
        raise MeicanOAuthError(
            f'MEICAN_OAUTH_HTTP_{e.code}',
            status=e.code,
            body=raw[:2000],
        ) from e
    except urllib_error.URLError as e:
        raise MeicanOAuthError(f'MEICAN_OAUTH_NETWORK: {e.reason}', body=str(e.reason)) from e

    if status >= 400:
        raise MeicanOAuthError(f'MEICAN_OAUTH_HTTP_{status}', status=status, body=raw[:2000])

    data = _parse_oauth_json(raw)
    if not isinstance(data, dict):
        raise MeicanOAuthError('MEICAN_OAUTH_INVALID_JSON', body=raw[:2000])

    access = _pick_token_field(data, 'access_token', 'accessToken')
    if not access:
        raise MeicanOAuthError('MEICAN_OAUTH_NO_ACCESS_TOKEN', body=raw[:2000])

    refresh = (
        _pick_token_field(data, 'refresh_token', 'refreshToken')
        or refresh_token
    )
    expires_in = _pick_token_field(data, 'expires_in', 'expiresIn')
    try:
        expires_in = int(expires_in) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_in = None

    return {
        'access_token': access,
        'refresh_token': refresh or refresh_token,
        'expires_in': expires_in,
    }


def _default_ttl_seconds():
    return int(getattr(settings, 'MEICAN_TOKEN_DEFAULT_TTL_SECONDS', 3600) or 3600)


def _skew_seconds():
    return int(getattr(settings, 'MEICAN_TOKEN_REFRESH_SKEW_SECONDS', 300) or 300)


def _compute_expire_at(expires_in):
    ttl = expires_in if isinstance(expires_in, int) and expires_in > 0 else _default_ttl_seconds()
    return timezone.now() + timedelta(seconds=ttl)


def apply_refresh_result_to_account(account: UserMeicanAccount, result: dict):
    account.access_token = result['access_token']
    account.refresh_token = result['refresh_token']
    account.token_expire_at = _compute_expire_at(result.get('expires_in'))
    account.save(update_fields=['access_token', 'refresh_token', 'token_expire_at', 'updated_at'])


def refresh_user_meican_token_locked(user_id: int) -> UserMeicanAccount:
    """
    在事务内对 user_meican_account 行加锁并执行 refresh，返回更新后的账号行。
    """
    with transaction.atomic():
        account = (
            UserMeicanAccount.objects.select_for_update()
            .select_related('user')
            .filter(user_id=int(user_id))
            .first()
        )
        if not account:
            raise MeicanOAuthError('MEICAN_ACCOUNT_NOT_FOUND')
        if not (account.refresh_token or '').strip():
            raise MeicanOAuthError('MEICAN_REFRESH_TOKEN_MISSING')

        result = call_meican_refresh_token(account.refresh_token.strip())
        apply_refresh_result_to_account(account, result)
        account.refresh_from_db()
        return account


def account_needs_refresh(account: UserMeicanAccount, skew_seconds: int = None) -> bool:
    skew = skew_seconds if skew_seconds is not None else _skew_seconds()
    if not (account.refresh_token or '').strip():
        return False
    if not (account.access_token or '').strip():
        return True
    exp = account.token_expire_at
    if not exp:
        return True
    threshold = timezone.now() + timedelta(seconds=skew)
    return exp <= threshold


def ensure_valid_access_token(user_id: int, skew_seconds: int = None):
    """
    若 access 在 skew 窗口内将过期或缺失，则 refresh；否则仅返回当前行。
    返回 (account, did_refresh: bool)
    """
    skew = skew_seconds if skew_seconds is not None else _skew_seconds()
    with transaction.atomic():
        account = (
            UserMeicanAccount.objects.select_for_update()
            .select_related('user')
            .filter(user_id=int(user_id))
            .first()
        )
        if not account:
            raise MeicanOAuthError('MEICAN_ACCOUNT_NOT_FOUND')
        if not account_needs_refresh(account, skew_seconds=skew):
            return account, False

        if not (account.refresh_token or '').strip():
            raise MeicanOAuthError('MEICAN_REFRESH_TOKEN_MISSING')

        result = call_meican_refresh_token(account.refresh_token.strip())
        apply_refresh_result_to_account(account, result)
        account.refresh_from_db()
        return account, True


def iter_user_ids_due_for_refresh(within_seconds: int = None, limit: int = 50):
    """
    需要主动刷新的用户 id 列表：有 refresh_token，且 token 空/无过期时间/在 within_seconds 内过期。
    """
    window = within_seconds if within_seconds is not None else _skew_seconds()
    threshold = timezone.now() + timedelta(seconds=window)
    lim = max(1, min(int(limit or 50), 500))

    qs = (
        UserMeicanAccount.objects.filter(refresh_token__gt='')
        .filter(
            Q(access_token='')
            | Q(token_expire_at__isnull=True)
            | Q(token_expire_at__lte=threshold)
        )
        .order_by('user_id')[:lim]
    )
    return list(qs.values_list('user_id', flat=True))
