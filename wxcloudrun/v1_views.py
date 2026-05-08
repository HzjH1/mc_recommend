import json
import logging
import time as pytime
import uuid
from datetime import datetime, time, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone

from wxcloudrun.models import (
    AutoOrderConfig,
    AutoOrderJob,
    AutoOrderJobItem,
    JobItemStatus,
    JobStatus,
    MealSlot,
    MenuItem,
    MenuSnapshot,
    OrderRecord,
    RecommendationBatch,
    RecommendationBatchStatus,
    RecommendationResult,
    UserAccount,
    UserMeicanAccount,
    UserPreference,
)
from wxcloudrun.menu_sync_service import normalize_days_payload, sync_menu_days
from wxcloudrun.meican_menu_snapshot import sync_meican_menu_snapshot_for_user_dates
from wxcloudrun.meican_client_config import (
    resolve_forward_base_url,
    resolve_forward_credentials,
    resolve_graphql_credentials,
    resolve_forward_referer,
    resolve_graphql_referer,
    resolve_forward_user_agent,
    resolve_graphql_app,
    resolve_x_mc_device,
)
from wxcloudrun.recommendation_service import (
    refresh_recommendations_for_user_slot,
    resolve_snapshot_recommendation_dates,
    resolve_week_start_monday,
    run_weekly_recommendation_job,
)

# settings.LOGGING 仅显式配置了 logger "log"，这里统一走该通道，确保落盘到 all-*.log
logger = logging.getLogger('log')

GRAPHQL_BASE_URL = 'https://gateway.meican.com/graphql'
PAYMENT_BASE_URL = 'https://meican-pay-checkout-bff.meican.com'

GRAPHQL_OPERATION_QUERIES = {
    'GetPhoneVerificationCode': """mutation GetPhoneVerificationCode($input: UserCenterSsoV2PhoneVerificationCodeRequest!) {
  getPhoneVerificationCode(input: $input)
}
""",
    'ChooseAccountLogin': """mutation ChooseAccountLogin($input: ChooseAccountLoginInput!) {
  chooseAccountLogin(input: $input) {
    ...TokenOutput
    __typename
  }
}

fragment TokenOutput on TokenOutput {
  token {
    ...UserCenterSsoV2TokenData
    __typename
  }
  __typename
}

fragment UserCenterSsoV2TokenData on UserCenterSsoV2TokenData {
  accessToken
  expiry
  needResetPassword
  refreshToken
  refreshTokenExpiry
  tokenType
  x
  __typename
}
""",
    'LoginByAuthWay': """mutation LoginByAuthWay($input: LoginByAuthWayInput!) {
  loginByAuthWay(input: $input) {
    ...UserCenterSsoV2LoginByAuthWayView
    __typename
  }
}

fragment UserCenterSsoV2LoginByAuthWayView on UserCenterSsoV2LoginByAuthWayView {
  data {
    ...UserCenterSsoV2LoginByAuthWayData
    __typename
  }
  meta {
    ...PbmetaV1Meta
    __typename
  }
  __typename
}

fragment UserCenterSsoV2LoginByAuthWayData on UserCenterSsoV2LoginByAuthWayData {
  clientMemberList {
    ...UserCenterSsoV2ClientMember
    __typename
  }
  signature
  ticket
  userList {
    ...UserCenterSsoV2User
    __typename
  }
  __typename
}

fragment UserCenterSsoV2ClientMember on UserCenterSsoV2ClientMember {
  accountVersion
  clientId
  email
  extra {
    ...UserCenterSsoV2ClientMemberExtra
    __typename
  }
  id
  name
  __typename
}

fragment UserCenterSsoV2ClientMemberExtra on UserCenterSsoV2ClientMemberExtra {
  isAccountFrozen
  leftFrozenTimestamp
  __typename
}

fragment UserCenterSsoV2User on UserCenterSsoV2User {
  accountVersion
  avatar
  email
  extra {
    ...UserCenterSsoV2UserExtra
    __typename
  }
  id
  idpInfo {
    ...UserCenterSsoV2IdpInfo
    __typename
  }
  name
  phone
  realName
  snowflakeId
  type
  wechatUser {
    ...UserCenterSsoV2WechatUser
    __typename
  }
  __typename
}

fragment UserCenterSsoV2UserExtra on UserCenterSsoV2UserExtra {
  isAccountFrozen
  leftFrozenTimestamp
  needResetPassword
  playOauthClientId
  playUniqueId
  userSerialNo
  __typename
}

fragment UserCenterSsoV2IdpInfo on UserCenterSsoV2IdpInfo {
  id
  type
  __typename
}

fragment UserCenterSsoV2WechatUser on UserCenterSsoV2WechatUser {
  active
  appId
  loginToken
  openId
  removed
  wechatType
  __typename
}

fragment PbmetaV1Meta on PbmetaV1Meta {
  code
  msg
  __typename
}
""",
}


def _request_id():
    return 'trace-' + uuid.uuid4().hex


def _resp(code=0, message='ok', data=None, request_id=None):
    return JsonResponse(
        {
            'code': code,
            'message': message,
            'requestId': request_id or _request_id(),
            'data': data if data is not None else {},
        },
        json_dumps_params={'ensure_ascii': False},
    )


def _parse_json_body(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}'), None
    except json.JSONDecodeError:
        return None, _resp(code=40001, message='请求体不是合法JSON', data={})


def _parse_date(value, default_today=False):
    if not value:
        if default_today:
            return timezone.now().date(), None
        return None, '缺少date参数'
    try:
        return datetime.strptime(value, '%Y-%m-%d').date(), None
    except ValueError:
        return None, 'date格式错误，需为YYYY-MM-DD'


def _normalize_meal_slot(value):
    val = str(value or '').upper().strip()
    if val in {MealSlot.LUNCH, MealSlot.DINNER}:
        return val
    return None


def _ensure_user(user_id, phone=None):
    """
    按 URL 中的 user_id 定位/创建 user_account；可选写入手机号便于运营排查。
    """
    phone = (phone or '').strip()[:20]
    defaults = {'status': 1}
    if phone:
        defaults['phone'] = phone
    user, _ = UserAccount.objects.get_or_create(id=int(user_id), defaults=defaults)
    if phone and (not user.phone or user.phone != phone):
        UserAccount.objects.filter(pk=user.pk).update(phone=phone)
        user.phone = phone
    return user


def _split_meal_slots(value):
    parts = [x.strip().upper() for x in str(value or '').split(',') if x.strip()]
    return [x for x in parts if x in {MealSlot.LUNCH, MealSlot.DINNER}]


def _parse_hhmm(hhmm, fallback):
    val = str(hhmm or fallback)
    try:
        h, m = val.split(':')
        return time(hour=int(h), minute=int(m))
    except Exception:
        return fallback


def _pick_first(source, paths, default=''):
    if not isinstance(source, dict):
        return default
    for path in paths:
        cur = source
        ok = True
        for key in path.split('.'):
            if not isinstance(cur, dict):
                ok = False
                break
            cur = cur.get(key)
        if ok and cur is not None and str(cur).strip() != '':
            return cur
    return default


def _ensure_list(value):
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _append_query_params(url: str, params: dict) -> str:
    clean = {k: v for k, v in params.items() if v is not None and str(v).strip() != ''}
    if not clean:
        return url
    return f'{url}{"&" if "?" in url else "?"}{urlencode(clean)}'


def _collect_matching(obj, pred, out, seen):
    if obj is None:
        return
    if isinstance(obj, dict):
        oid = id(obj)
        if oid in seen:
            return
        seen.add(oid)
        if pred(obj):
            out.append(obj)
        for v in obj.values():
            _collect_matching(v, pred, out, seen)
    elif isinstance(obj, list):
        oid = id(obj)
        if oid in seen:
            return
        seen.add(oid)
        for v in obj:
            _collect_matching(v, pred, out, seen)


def _find_objects(value, predicate):
    out = []
    _collect_matching(value, predicate, out, set())
    return out


def _format_target_time(dt_val):
    if dt_val is None:
        return ''
    if isinstance(dt_val, datetime):
        return dt_val.strftime('%Y-%m-%d %H:%M')
    try:
        return dt_val.strftime('%Y-%m-%d %H:%M')
    except Exception:
        return str(dt_val)


def _extract_menu_item_order_context(menu_item):
    raw = menu_item.raw_json if isinstance(menu_item.raw_json, dict) else {}
    tab_uid = str(
        _pick_first(raw, ['tabUniqueId', 'tabUUID', 'tab.uniqueId'], menu_item.snapshot.tab_unique_id or '')
    ).strip()
    target_time = str(
        _pick_first(raw, ['targetTime', 'target_time'], _format_target_time(menu_item.snapshot.target_time))
    ).strip()
    dish_id = str(_pick_first(raw, ['dishId', 'id'], menu_item.dish_id or '')).strip()
    return {
        'tabUniqueId': tab_uid,
        'targetTime': target_time,
        'dishId': dish_id,
    }


def _build_forward_headers(access_token=''):
    cid, csec = resolve_forward_credentials()
    headers = {
        'clientID': cid,
        'clientSecret': csec,
        'x-mc-app': resolve_graphql_app(),
        'x-mc-device': resolve_x_mc_device(),
        'x-mc-page': '/auth/verification?stamp=AC',
        'Referer': resolve_forward_referer(),
        'accept-language': 'zh',
    }
    ua = resolve_forward_user_agent()
    if ua:
        headers['User-Agent'] = ua
    if access_token:
        headers['Authorization'] = f'Bearer {access_token}'
    return headers


def _build_graphql_gateway_headers(profile='graphql_verification'):
    referer = resolve_graphql_referer() or 'https://www.meican.com/'
    app = resolve_graphql_app() or 'meican/web-pc (prod;4.90.1;sys;main)'
    cid, csec = resolve_forward_credentials()
    if profile in {'graphql_signin', 'graphql_verification'}:
        gql_cid, gql_csec = resolve_graphql_credentials()
        cid, csec = gql_cid or cid, gql_csec or csec
    page_path = '/auth/signin/mobile?stamp=AB' if profile == 'graphql_signin' else '/auth/verification?stamp=AC'
    return {
        'accept': '*/*',
        'accept-language': 'zh',
        'clientid': cid,
        'clientsecret': csec,
        'x-mc-app': app,
        'x-mc-device': resolve_x_mc_device(),
        'x-mc-page': page_path,
        'Referer': referer,
    }


def _http_json_request(url: str, method='GET', data=None, headers=None, timeout=45):
    payload = None
    req_headers = dict(headers or {})
    if data is not None:
        if isinstance(data, (dict, list)):
            payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
            req_headers.setdefault('Content-Type', 'application/json')
        elif isinstance(data, bytes):
            payload = data
        else:
            payload = str(data).encode('utf-8')
    req = Request(url, data=payload, method=method, headers=req_headers)
    with urlopen(req, timeout=timeout) as resp:  # nosec B310
        raw = resp.read().decode('utf-8', errors='replace')
    return json.loads(raw) if raw else {}


def _graphql_request(operation_name: str, variables: dict, header_profile='graphql_verification'):
    query = GRAPHQL_OPERATION_QUERIES.get(operation_name, '').strip()
    if not query:
        raise ValueError(f'GRAPHQL_OPERATION_QUERY_MISSING:{operation_name}')
    payload = _http_json_request(
        f'{GRAPHQL_BASE_URL}?op={operation_name}',
        method='POST',
        data={'operationName': operation_name, 'variables': variables, 'query': query},
        headers=_build_graphql_gateway_headers(header_profile),
        timeout=45,
    )
    if isinstance(payload, dict) and payload.get('errors'):
        err0 = _ensure_list(payload.get('errors'))[:1] or [{}]
        err0 = err0[0] if isinstance(err0[0], dict) else {}
        trace = _pick_first(err0, ['extensions.originalError.x-trace-id', 'extensions.x-trace-id'], '')
        base = str(err0.get('message') or f'{operation_name} failed')
        raise ValueError(f'{base} (trace:{trace})' if trace else base)
    if isinstance(payload, dict) and isinstance(payload.get('data'), dict):
        return payload['data']
    return payload


def _payment_request(path, query=None, access_token=''):
    cid, csec = resolve_forward_credentials()
    url = _append_query_params(
        f'{PAYMENT_BASE_URL}{path}',
        {'client_id': cid, 'client_secret': csec, **(query or {})},
    )
    headers = _build_forward_headers(access_token)
    return _http_json_request(url, method='GET', headers=headers, timeout=30)


def _extract_corp_namespace_from_account_info(account_info=None, preferred_account_name=''):
    if not isinstance(account_info, dict):
        return ''
    corps = []
    corps.extend(_ensure_list(_pick_first(account_info, ['corps', 'corpList', 'companies', 'userCorps', 'corpInfos'], [])))
    key_paths = [
        'namespace',
        'corpNamespace',
        'corpUniqueNamespace',
        'uniqueNamespace',
        'slug',
        'code',
        'corpKey',
        'enterpriseKey',
        'corp.namespace',
        'corp.corpNamespace',
        'metadata.namespace',
    ]
    wanted_name = str(preferred_account_name or '').strip()
    if wanted_name:
        for corp in corps:
            if not isinstance(corp, dict):
                continue
            corp_name = str(_pick_first(corp, ['name', 'accountName', 'corpName', 'companyName'], '')).strip()
            if corp_name != wanted_name:
                continue
            ns = str(_pick_first(corp, key_paths, '')).strip()
            if ns:
                return ns
    for corp in corps:
        if not isinstance(corp, dict):
            continue
        ns = str(_pick_first(corp, key_paths, '')).strip()
        if ns:
            return ns
    top_val = str(
        _pick_first(
            account_info,
            ['meicanCorpNamespace', 'corpNamespace', 'currentCorpNamespace', 'defaultNamespace', 'namespace'],
            '',
        )
    ).strip()
    if top_val:
        return top_val
    hits = _find_objects(
        account_info,
        lambda o: isinstance(o, dict)
        and str(o.get('corpNamespace') or o.get('corpNamespaceKey') or '').strip()
        and any(o.get(k) for k in ['corpName', 'name', 'companyName', 'corpId']),
    )
    for hit in hits:
        ns = str(hit.get('corpNamespace') or hit.get('corpNamespaceKey') or '').strip()
        if ns:
            return ns
    return ''


def _extract_lunch_dinner_namespaces_from_dinnerin(payload=None):
    items = _ensure_list(_pick_first(payload if isinstance(payload, dict) else {}, ['data.items', 'items'], []))
    valid = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        labels = [str(x or '').strip() for x in _ensure_list(item.get('labels'))]
        if not name or '到店' in name:
            continue
        if any('特色档口' in label for label in labels):
            continue
        valid.append(item)
    lunch = ''
    dinner = ''
    for item in valid:
        ns = str(item.get('namespace') or '').strip()
        if not ns:
            continue
        labels = [str(x or '').strip() for x in _ensure_list(item.get('labels'))]
        if not lunch and any('午餐' in label for label in labels):
            lunch = ns
        if not dinner and any('晚餐' in label for label in labels):
            dinner = ns
    return {'lunchNamespace': lunch, 'dinnerNamespace': dinner}


def _normalize_meican_login_payload(payload):
    normalized = payload if isinstance(payload, dict) else {}
    data = normalized.get('data')
    has_direct_access = str(_pick_first(normalized, ['accessToken', 'access_token', 'token.accessToken', 'token.access_token'], '')).strip()
    if isinstance(data, dict) and not has_direct_access:
        merged = dict(normalized)
        merged.update(data)
        normalized = merged
    return normalized


def _refresh_user_meican_account_token(acc: UserMeicanAccount) -> bool:
    cid, csec = resolve_forward_credentials()
    if not cid or not csec or not str(acc.refresh_token or '').strip():
        return False
    token_url = _append_query_params(
        f'{resolve_forward_base_url().rstrip("/")}/api/v2.1/oauth/token',
        {'client_id': cid, 'client_secret': csec},
    )
    req = Request(
        token_url,
        data=urlencode({'grant_type': 'refresh_token', 'refresh_token': str(acc.refresh_token or '').strip()}).encode('utf-8'),
        method='POST',
        headers={
            'clientID': cid,
            'clientSecret': csec,
            'Referer': resolve_forward_referer(),
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': resolve_forward_user_agent(),
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:  # nosec B310
            raw = resp.read().decode('utf-8', errors='replace')
        data = json.loads(raw) if raw else {}
    except (HTTPError, URLError, json.JSONDecodeError, ValueError):
        return False
    access = data.get('accessToken') or data.get('access_token')
    refresh = data.get('refreshToken') or data.get('refresh_token')
    if not access:
        return False
    acc.access_token = str(access).strip()
    if refresh:
        acc.refresh_token = str(refresh).strip()
    ttl = data.get('expiresIn') or data.get('expires_in')
    try:
        ttl = int(ttl) if ttl is not None else 3600
    except (TypeError, ValueError):
        ttl = 3600
    if ttl <= 0:
        ttl = 3600
    acc.token_expire_at = timezone.now() + timedelta(seconds=ttl)
    acc.save(update_fields=['access_token', 'refresh_token', 'token_expire_at', 'updated_at'])
    return True


def _forward_json_get(path, query, access_token=''):
    cid, csec = resolve_forward_credentials()
    base = resolve_forward_base_url().rstrip('/')
    q = {'client_id': cid, 'client_secret': csec}
    q.update({k: v for k, v in query.items() if v is not None and str(v).strip() != ''})
    url = f'{base}{path}?{urlencode(q)}'
    req = Request(url, method='GET', headers=_build_forward_headers(access_token))
    with urlopen(req, timeout=30) as resp:  # nosec B310
        raw = resp.read().decode('utf-8', errors='replace')
    return json.loads(raw) if raw else {}


def _forward_form_post(path, form_body: str, access_token=''):
    cid, csec = resolve_forward_credentials()
    base = resolve_forward_base_url().rstrip('/')
    url = f'{base}{path}?{urlencode({"client_id": cid, "client_secret": csec})}'
    headers = _build_forward_headers(access_token)
    headers['Content-Type'] = 'application/x-www-form-urlencoded'
    req = Request(url, data=form_body.encode('utf-8'), method='POST', headers=headers)
    with urlopen(req, timeout=45) as resp:  # nosec B310
        raw = resp.read().decode('utf-8', errors='replace')
    return json.loads(raw) if raw else {}


def _resolve_default_address_ids(namespace: str, access_token: str):
    payload = _forward_json_get(
        '/api/v2.1/corpaddresses/getmulticorpaddress',
        {'namespace': namespace},
        access_token=access_token,
    )
    first = (
        _ensure_list(_pick_first(payload, ['data.addressList', 'addressList', 'result.addressList'], []))[:1]
        or _ensure_list(_pick_first(payload, ['data.list', 'data.addresses', 'addresses', 'result.list'], []))[:1]
        or [{}]
    )[0]
    final_value = first.get('finalValue') if isinstance(first.get('finalValue'), dict) else {}
    user_addr = str(
        _pick_first(
            {**first, 'finalValue': final_value},
            [
                'userAddressUniqueId',
                'userAddressId',
                'userAddress.uniqueId',
                'address.uniqueId',
                'finalValue.userAddressUniqueId',
                'finalValue.uniqueId',
                'id',
            ],
            _pick_first(payload, ['data.defaultUserAddressUniqueId', 'defaultUserAddressUniqueId'], ''),
        )
    ).strip()
    corp_addr = str(
        _pick_first(
            {**first, 'finalValue': final_value},
            [
                'corpAddressUniqueId',
                'corpAddressId',
                'corpAddress.uniqueId',
                'addressUniqueId',
                'finalValue.corpAddressUniqueId',
                'finalValue.uniqueId',
                'uniqueId',
            ],
            _pick_first(payload, ['data.defaultCorpAddressUniqueId', 'defaultCorpAddressUniqueId'], ''),
        )
    ).strip() or user_addr
    if not user_addr or not corp_addr:
        logger.warning(
            'manual_order.address_resolve_failed namespace=%s user_addr=%s corp_addr=%s payload=%s',
            namespace,
            user_addr,
            corp_addr,
            json.dumps(payload, ensure_ascii=False)[:1200],
        )
        raise ValueError('NO_DEFAULT_ADDRESS')
    logger.info(
        'manual_order.address_resolved namespace=%s user_addr=%s corp_addr=%s',
        namespace,
        user_addr,
        corp_addr,
    )
    return user_addr, corp_addr


def _fetch_address_options(namespace: str, access_token: str):
    payload = _forward_json_get(
        '/api/v2.1/corpaddresses/getmulticorpaddress',
        {'namespace': namespace},
        access_token=access_token,
    )
    rows = []
    rows.extend(_ensure_list(_pick_first(payload, ['data.addressList', 'addressList', 'result.addressList'], [])))
    rows.extend(_ensure_list(_pick_first(payload, ['data.list', 'data.addresses', 'addresses', 'result.list'], [])))
    options = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        final_value = row.get('finalValue') if isinstance(row.get('finalValue'), dict) else {}
        user_addr = str(
            _pick_first(
                {**row, 'finalValue': final_value},
                [
                    'userAddressUniqueId',
                    'userAddressId',
                    'userAddress.uniqueId',
                    'address.uniqueId',
                    'finalValue.userAddressUniqueId',
                    'finalValue.uniqueId',
                    'id',
                ],
                '',
            )
        ).strip()
        corp_addr = str(
            _pick_first(
                {**row, 'finalValue': final_value},
                [
                    'corpAddressUniqueId',
                    'corpAddressId',
                    'corpAddress.uniqueId',
                    'addressUniqueId',
                    'finalValue.corpAddressUniqueId',
                    'finalValue.uniqueId',
                    'uniqueId',
                ],
                '',
            )
        ).strip() or user_addr
        if not user_addr or not corp_addr:
            continue
        label = ' / '.join(
            [
                str(_pick_first(row, ['corpName'], '')).strip(),
                str(_pick_first({**row, 'finalValue': final_value}, ['finalValue.pickUpLocation', 'pickUpLocation', 'name', 'addressName'], '')).strip(),
                str(_pick_first(row, ['address', 'detailAddress', 'fullAddress'], '')).strip(),
            ]
        ).strip(' /')
        options.append(
            {
                'userAddressUniqueId': user_addr,
                'corpAddressUniqueId': corp_addr,
                'label': label or corp_addr,
            }
        )
    # 去重
    uniq = []
    seen = set()
    for item in options:
        k = f'{item["userAddressUniqueId"]}::{item["corpAddressUniqueId"]}'
        if k in seen:
            continue
        seen.add(k)
        uniq.append(item)
    return uniq


def _save_default_corp_address(user: UserAccount, corp_address_id: str, meal_slot: str = ''):
    cid = str(corp_address_id or '').strip()
    if not cid:
        return
    meal_slot = str(meal_slot or '').upper().strip()
    cfg = AutoOrderConfig.objects.filter(user=user).first()
    if cfg:
        changed = False
        if cfg.default_corp_address_id != cid:
            cfg.default_corp_address_id = cid
            changed = True
        if meal_slot == MealSlot.LUNCH and cfg.default_corp_address_id_lunch != cid:
            cfg.default_corp_address_id_lunch = cid
            changed = True
        if meal_slot == MealSlot.DINNER and cfg.default_corp_address_id_dinner != cid:
            cfg.default_corp_address_id_dinner = cid
            changed = True
        if changed:
            cfg.save(
                update_fields=[
                    'default_corp_address_id',
                    'default_corp_address_id_lunch',
                    'default_corp_address_id_dinner',
                    'updated_at',
                ]
            )
        return
    AutoOrderConfig.objects.create(
        user=user,
        enabled=0,
        meal_slots='LUNCH,DINNER',
        strategy='TOP1',
        default_corp_address_id=cid,
        default_corp_address_id_lunch=cid if meal_slot == MealSlot.LUNCH else '',
        default_corp_address_id_dinner=cid if meal_slot == MealSlot.DINNER else '',
        effective_from=timezone.now().date(),
        effective_to=None,
    )


def _submit_meican_order_for_manual(
    user: UserAccount,
    menu_item: MenuItem,
    namespace: str,
    selected_user_addr: str = '',
    selected_corp_addr: str = '',
):
    acc = UserMeicanAccount.objects.filter(user=user).first()
    if not acc or not str(acc.access_token or '').strip():
        raise ValueError('MEICAN_SESSION_REQUIRED')
    if acc.token_expire_at and acc.token_expire_at <= timezone.now():
        _refresh_user_meican_account_token(acc)
    ns = str(namespace or acc.account_namespace or '').strip()
    if not ns:
        raise ValueError('MEICAN_NAMESPACE_REQUIRED')
    ctx = _extract_menu_item_order_context(menu_item)
    if not ctx['tabUniqueId'] or not ctx['targetTime'] or not ctx['dishId']:
        raise ValueError('MENU_ITEM_FORWARD_CONTEXT_MISSING')

    user_addr = str(selected_user_addr or '').strip()
    corp_addr = str(selected_corp_addr or '').strip()
    if not user_addr or not corp_addr:
        user_addr, corp_addr = _resolve_default_address_ids(ns, str(acc.access_token or '').strip())
    dish_for_order = int(ctx['dishId']) if str(ctx['dishId']).isdigit() else ctx['dishId']
    form_body = urlencode(
        {
            'tabUniqueId': ctx['tabUniqueId'],
            'targetTime': ctx['targetTime'],
            'userAddressUniqueId': user_addr,
            'corpAddressUniqueId': corp_addr,
            'corpAddressRemark': '',
            'order': json.dumps([{'count': 1, 'dishId': dish_for_order}], ensure_ascii=False),
            'remarks': json.dumps([{'dishId': str(ctx['dishId']), 'remark': ''}], ensure_ascii=False),
        }
    )
    logger.info(
        'manual_order.forward_request user_id=%s namespace=%s menu_item_id=%s dish_id=%s tab_unique_id=%s target_time=%s',
        user.id,
        ns,
        menu_item.id,
        ctx['dishId'],
        ctx['tabUniqueId'],
        ctx['targetTime'],
    )
    try:
        out = _forward_form_post('/api/v2.1/orders/add', form_body, access_token=str(acc.access_token or '').strip())
        logger.info(
            'manual_order.forward_response user_id=%s namespace=%s menu_item_id=%s status=%s message=%s',
            user.id,
            ns,
            menu_item.id,
            str(out.get('status') if isinstance(out, dict) else ''),
            str(out.get('message') if isinstance(out, dict) else ''),
        )
    except HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace') if e.fp else ''
        logger.warning(
            'manual_order.forward_http_error user_id=%s namespace=%s menu_item_id=%s http_status=%s body=%s',
            user.id,
            ns,
            menu_item.id,
            e.code,
            raw[:1000],
        )
        try:
            err_obj = json.loads(raw) if raw else {}
            msg = err_obj.get('message') or err_obj.get('error') or f'HTTP_{e.code}'
        except Exception:
            msg = f'HTTP_{e.code}'
        raise ValueError(f'MEICAN_API_ERROR:{msg}')
    except URLError:
        logger.warning(
            'manual_order.forward_network_error user_id=%s namespace=%s menu_item_id=%s',
            user.id,
            ns,
            menu_item.id,
        )
        raise ValueError('MEICAN_API_ERROR:NETWORK')

    if isinstance(out, dict):
        status = str(out.get('status') or '')
        if status and status.upper() not in {'SUCCESS', 'OK'} and out.get('message'):
            raise ValueError(f'MEICAN_API_ERROR:{out.get("message")}')
        uid = str(
            _pick_first(out, ['uniqueId', 'data.uniqueId', 'order.uniqueId', 'result.uniqueId', 'orderDetail.uniqueId'], '')
        ).strip()
        return uid, user_addr, corp_addr
    return '', user_addr, corp_addr


def _cancel_meican_order_for_user(user: UserAccount, order_unique_id: str):
    uid = str(order_unique_id or '').strip()
    if not uid:
        return
    acc = UserMeicanAccount.objects.filter(user=user).first()
    if not acc or not str(acc.access_token or '').strip():
        raise ValueError('MEICAN_SESSION_REQUIRED')
    if acc.token_expire_at and acc.token_expire_at <= timezone.now():
        _refresh_user_meican_account_token(acc)
    form_body = urlencode(
        {
            'uniqueId': uid,
            'type': 'CORP_ORDER',
            'restoreCart': 'false',
        }
    )
    try:
        out = _forward_form_post('/api/v2.1/orders/delete', form_body, access_token=str(acc.access_token or '').strip())
        logger.info(
            'manual_order.forward_delete_response user_id=%s order_unique_id=%s status=%s message=%s',
            user.id,
            uid,
            str(out.get('status') if isinstance(out, dict) else ''),
            str(out.get('message') if isinstance(out, dict) else ''),
        )
    except HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace') if e.fp else ''
        logger.warning(
            'manual_order.forward_delete_http_error user_id=%s order_unique_id=%s http_status=%s body=%s',
            user.id,
            uid,
            e.code,
            raw[:1000],
        )
        raise ValueError(f'MEICAN_API_ERROR:HTTP_{e.code}')
    except URLError:
        logger.warning(
            'manual_order.forward_delete_network_error user_id=%s order_unique_id=%s',
            user.id,
            uid,
        )
        raise ValueError('MEICAN_API_ERROR:NETWORK')


def _within_auto_order_window(date_val, meal_slot):
    today = timezone.now().date()
    if date_val < today:
        return False
    if date_val > today:
        return True
    now_t = timezone.now().time().replace(second=0, microsecond=0)
    lunch_deadline = _parse_hhmm(getattr(settings, 'AUTO_ORDER_LUNCH_DEADLINE', '10:30'), time(10, 30))
    dinner_deadline = _parse_hhmm(getattr(settings, 'AUTO_ORDER_DINNER_DEADLINE', '16:30'), time(16, 30))
    if meal_slot == MealSlot.LUNCH:
        return now_t <= lunch_deadline
    return now_t <= dinner_deadline


def _trigger_initial_recommendations_for_new_user(user: UserAccount, namespace: str):
    """
    新用户首次登录后，为该用户触发一次推荐生成：
    - 以当前业务周为基准（周日按下周）
    - 优先使用 menu_snapshot 已存在的工作日，缺失时回退 5 个工作日
    - 午/晚餐各跑一次，失败仅打日志，不阻断登录流程
    """
    ns = str(namespace or '').strip()
    if not ns:
        return
    try:
        monday = resolve_week_start_monday(None, None)
        dates = resolve_snapshot_recommendation_dates(ns, monday, fallback_workdays=5)
        for day in dates:
            for slot in (MealSlot.LUNCH, MealSlot.DINNER):
                out = refresh_recommendations_for_user_slot(
                    user,
                    ns,
                    day,
                    slot,
                    freeze=False,
                    top_n=3,
                    sync_menu_if_missing=True,
                )
                if not out.get('ok'):
                    logger.info(
                        'initial_recommendation.skipped user_id=%s namespace=%s day=%s slot=%s reason=%s',
                        user.id,
                        ns,
                        day,
                        slot,
                        out.get('skip', 'unknown'),
                    )
    except Exception as exc:
        logger.warning(
            'initial_recommendation.failed user_id=%s namespace=%s err=%s',
            user.id,
            ns,
            exc,
        )


def _format_price_value(value):
    if value in (None, ''):
        return ''
    try:
        number_value = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number_value > 1000:
        return f'{number_value / 100:.2f}'
    return f'{number_value:.2f}'


def _fetch_account_info_with_token(access_token: str):
    return _forward_json_get('/api/v2.1/accounts/show', {}, access_token=access_token)


def _fetch_real_name_with_token(access_token: str):
    return _forward_json_get('/api/v2.1/client/getrealname', {}, access_token=access_token)


def _fetch_payment_accounts_with_token(access_token: str):
    payload = _payment_request('/api/v3.0/paymentadapter/user/account/list', {'includeCheckout': True}, access_token=access_token)
    accounts = _ensure_list(_pick_first(payload if isinstance(payload, dict) else {}, ['accounts', 'list', 'data'], payload))
    return accounts


def _ensure_session_namespaces_with_token(access_token: str, selected_account_name=''):
    lunch = ''
    dinner = ''
    fallback_ns = ''
    try:
        dinnerin = _forward_json_get('/api/v2.1/corpmembers/dinnerin', {}, access_token=access_token)
        parsed = _extract_lunch_dinner_namespaces_from_dinnerin(dinnerin)
        lunch = str(parsed.get('lunchNamespace') or '').strip()
        dinner = str(parsed.get('dinnerNamespace') or '').strip()
    except Exception:
        pass
    try:
        account_info = _fetch_account_info_with_token(access_token)
        fallback_ns = _extract_corp_namespace_from_account_info(account_info, selected_account_name)
    except Exception:
        fallback_ns = ''
    lunch = lunch or fallback_ns
    dinner = dinner or fallback_ns
    account_namespace = dinner or lunch or fallback_ns
    return {
        'accountNamespace': account_namespace,
        'accountNamespaceLunch': lunch,
        'accountNamespaceDinner': dinner,
    }


def _fetch_meican_user_bundle_with_token(access_token: str, selected_account_name=''):
    try:
        account_info = _fetch_account_info_with_token(access_token)
    except Exception as exc:
        logger.warning('meican.bundle.account_info_failed err=%s', exc)
        account_info = {}
    try:
        real_name = _fetch_real_name_with_token(access_token)
    except Exception as exc:
        logger.warning('meican.bundle.real_name_failed err=%s', exc)
        real_name = {}
    try:
        payment_accounts = _fetch_payment_accounts_with_token(access_token)
    except Exception as exc:
        logger.warning('meican.bundle.payment_accounts_failed err=%s', exc)
        payment_accounts = []
    corps = _ensure_list(_pick_first(account_info, ['corps', 'corpList', 'companies'], []))
    corp_names = [str(_pick_first(corp, ['name', 'corpName', 'companyName'], '')).strip() for corp in corps if isinstance(corp, dict)]
    corp_names = [x for x in corp_names if x]
    meican_corp_namespace = _extract_corp_namespace_from_account_info(account_info, selected_account_name)
    first_payment_account = payment_accounts[0] if payment_accounts else {}
    display_name = str(_pick_first(real_name, ['realName', 'name'], _pick_first(account_info, ['name', 'nickName'], '美'))).strip()
    return {
        'profile': {
            'meicanName': display_name,
            'meicanMemberId': str(_pick_first(account_info, ['memberId', 'id', 'snowflakeId'], '')).strip(),
            'meicanEmployeeNo': str(_pick_first(account_info, ['employeeNo', 'jobNumber'], '')).strip(),
            'email': str(_pick_first(account_info, ['email', 'mail'], '')).strip(),
            'phone': str(_pick_first(account_info, ['phone', 'mobile'], '')).strip(),
            'avatarText': (display_name[:1] or '美'),
            'meicanCorpNamespace': meican_corp_namespace,
            'corpNames': corp_names,
            'userType': str(_pick_first(account_info, ['userType', 'type'], '')).strip(),
            'balance': _format_price_value(_pick_first(first_payment_account, ['balance', 'availableBalance', 'amountInCent'], '')),
            'accountStatus': str(_pick_first(account_info, ['status'], '')).strip(),
        },
        'raw': {
            'accountInfo': account_info,
            'realName': real_name,
            'paymentAccounts': payment_accounts,
        },
    }


def _choose_meican_account_login(login_payload, phone=''):
    normalized = _normalize_meican_login_payload(login_payload)
    direct_access_token = str(
        _pick_first(normalized, ['accessToken', 'access_token', 'token.accessToken', 'token.access_token'], '')
    ).strip()
    if direct_access_token:
        expiry_raw = _pick_first(
            normalized,
            ['expiresIn', 'expires_in', 'token.expiresIn', 'token.expires_in', 'token.expiry', 'expiry'],
            None,
        )
        try:
            expiry_num = int(expiry_raw) if expiry_raw not in (None, '') else 3600
        except (TypeError, ValueError):
            expiry_num = 3600
        session = {
            'phone': phone,
            'accessToken': direct_access_token,
            'refreshToken': str(
                _pick_first(normalized, ['refreshToken', 'refresh_token', 'token.refreshToken', 'token.refresh_token'], '')
            ).strip(),
            'accessTokenExpiresIn': expiry_num if expiry_num > 0 else 3600,
            'selectedAccountName': str(_pick_first(normalized, ['accountName', 'name'], '')).strip(),
            'accountNamespace': str(
                _pick_first(normalized, ['namespace', 'corpNamespace', 'corpUniqueNamespace', 'uniqueNamespace', 'selectedAccount.namespace'], '')
            ).strip(),
            'snowflakeId': str(_pick_first(normalized, ['snowflakeId', 'userSnowflakeId'], '')).strip(),
            'signature': str(_pick_first(normalized, ['signature'], '')).strip(),
            'ticket': str(_pick_first(normalized, ['ticket'], '')).strip(),
        }
        session.update(_ensure_session_namespaces_with_token(session['accessToken'], session.get('selectedAccountName', '')))
        return session

    ticket = str(_pick_first(normalized, ['ticket'], '')).strip()
    accounts = []
    accounts.extend(_ensure_list(normalized.get('accounts')))
    accounts.extend(_ensure_list(normalized.get('accountList')))
    accounts.extend(_ensure_list(normalized.get('selectableAccounts')))
    accounts.extend(_ensure_list(normalized.get('userList')))
    accounts.extend(_ensure_list(normalized.get('clientMemberList')))
    accounts = [x for x in accounts if isinstance(x, dict)]
    if not ticket or not accounts:
        raise ValueError('MEICAN_LOGIN_ACCOUNT_REQUIRED')
    target_account = accounts[0]
    flow_signature = str(_pick_first(target_account, ['signature'], '') or _pick_first(normalized, ['signature'], '')).strip()
    response = _graphql_request(
        'ChooseAccountLogin',
        {
            'input': {
                'ticket': ticket,
                'snowflakeId': str(_pick_first(target_account, ['snowflakeId', 'userSnowflakeId'], '')).strip(),
                'signature': flow_signature,
            }
        },
        header_profile='forward_verification',
    )
    raw_out = response.get('chooseAccountLogin') if isinstance(response, dict) else response
    token_bag = raw_out.get('token') if isinstance(raw_out, dict) and isinstance(raw_out.get('token'), dict) else raw_out
    expiry_raw = _pick_first(token_bag if isinstance(token_bag, dict) else {}, ['expiry', 'expiresIn', 'expires_in'], None)
    try:
        expiry_num = int(expiry_raw) if expiry_raw not in (None, '') else 3600
    except (TypeError, ValueError):
        expiry_num = 3600
    session = {
        'phone': phone,
        'accessToken': str(_pick_first(token_bag if isinstance(token_bag, dict) else {}, ['accessToken', 'access_token'], '')).strip(),
        'refreshToken': str(_pick_first(token_bag if isinstance(token_bag, dict) else {}, ['refreshToken', 'refresh_token'], '')).strip(),
        'accessTokenExpiresIn': expiry_num if expiry_num > 0 else 3600,
        'selectedAccountName': str(_pick_first(target_account, ['name', 'accountName'], '')).strip(),
        'accountNamespace': str(
            _pick_first(target_account, ['namespace', 'corpNamespace', 'corpUniqueNamespace', 'uniqueNamespace', 'corp.namespace'], '')
        ).strip(),
        'snowflakeId': str(_pick_first(target_account, ['snowflakeId', 'userSnowflakeId'], '')).strip(),
        'signature': flow_signature,
        'ticket': ticket,
    }
    session.update(_ensure_session_namespaces_with_token(session['accessToken'], session.get('selectedAccountName', '')))
    return session


def post_meican_send_phone_verification_code(request, *_args, **_kwargs):
    if request.method != 'POST':
        return _resp(code=40500, message='请求方式错误，请使用POST')
    body, err = _parse_json_body(request)
    if err:
        return err
    phone = str(body.get('phone') or '').strip()
    if not phone:
        return _resp(code=40031, message='缺少phone')
    try:
        out = _graphql_request(
            'GetPhoneVerificationCode',
            {'input': {'phone': phone, 'idpType': 'UserCenterIDP'}},
            header_profile='graphql_signin',
        )
        return _resp(data={'phone': phone, 'raw': out})
    except Exception as exc:
        logger.warning('meican.send_code_failed phone=%s err=%s', phone, exc)
        return _resp(code=50211, message=f'MEICAN_SEND_CODE_FAILED:{exc}')


def post_meican_phone_login(request, *_args, **_kwargs):
    if request.method != 'POST':
        return _resp(code=40500, message='请求方式错误，请使用POST')
    body, err = _parse_json_body(request)
    if err:
        return err
    phone = str(body.get('phone') or '').strip()
    verification_code = str(body.get('verificationCode') or body.get('verification_code') or '').strip()
    if not phone:
        return _resp(code=40031, message='缺少phone')
    if not verification_code:
        return _resp(code=40032, message='缺少verificationCode')
    try:
        login_resp = _graphql_request(
            'LoginByAuthWay',
            {
                'input': {
                    'authMethod': 'PhoneVerificationCode',
                    'phone': phone,
                    'verificationCode': verification_code,
                    'createAccountIfNotExist': True,
                    'createAccountScene': 'UserSelfRegistration',
                }
            },
            header_profile='graphql_verification',
        )
        payload = login_resp.get('loginByAuthWay') if isinstance(login_resp, dict) else login_resp
        session_payload = _choose_meican_account_login(payload, phone)
        bundle = _fetch_meican_user_bundle_with_token(session_payload['accessToken'], session_payload.get('selectedAccountName', ''))
        profile = bundle.get('profile') if isinstance(bundle, dict) else {}
        member_id = str(profile.get('meicanMemberId') or session_payload.get('snowflakeId') or '').strip()
        if not member_id.isdigit():
            raise ValueError('MEICAN_MEMBER_ID_INVALID')
        user = _ensure_user(member_id, phone=phone)
        ttl = int(session_payload.get('accessTokenExpiresIn') or 3600)
        token_expire_at = timezone.now() + timedelta(seconds=ttl if ttl > 0 else 3600)
        existing_acc = UserMeicanAccount.objects.filter(user=user).first()
        is_first_bind = not existing_acc or not bool(existing_acc.is_bound)
        UserMeicanAccount.objects.update_or_create(
            user=user,
            defaults={
                'meican_username': str(session_payload.get('selectedAccountName') or phone or 'meican_user').strip(),
                'meican_email': str(profile.get('email') or '').strip(),
                'access_token': str(session_payload.get('accessToken') or '').strip(),
                'refresh_token': str(session_payload.get('refreshToken') or '').strip(),
                'token_expire_at': token_expire_at,
                'account_namespace': str(session_payload.get('accountNamespace') or profile.get('meicanCorpNamespace') or '').strip(),
                'account_namespace_lunch': str(session_payload.get('accountNamespaceLunch') or session_payload.get('accountNamespace') or '').strip(),
                'account_namespace_dinner': str(session_payload.get('accountNamespaceDinner') or session_payload.get('accountNamespace') or '').strip(),
                'is_bound': 1,
            },
        )
        fallback_display_name = str(
            profile.get('meicanName')
            or session_payload.get('selectedAccountName')
            or phone[-4:]
            or '美'
        ).strip()
        fallback_namespace = str(
            session_payload.get('accountNamespace')
            or session_payload.get('accountNamespaceDinner')
            or session_payload.get('accountNamespaceLunch')
            or profile.get('meicanCorpNamespace')
            or ''
        ).strip()
        response_profile = {
            'meicanName': fallback_display_name,
            'meicanMemberId': member_id,
            'meicanEmployeeNo': str(profile.get('meicanEmployeeNo') or '').strip(),
            'email': str(profile.get('email') or '').strip(),
            'phone': phone,
            'avatarText': str(profile.get('avatarText') or (fallback_display_name[:1] or '美')).strip()[:1] or '美',
            'corpNames': profile.get('corpNames') if isinstance(profile.get('corpNames'), list) else [],
            'meicanCorpNamespace': fallback_namespace,
            'userType': str(profile.get('userType') or '').strip(),
            'balance': str(profile.get('balance') or '').strip(),
            'accountStatus': str(profile.get('accountStatus') or '').strip(),
            'meicanExternalMemberId': phone,
        }
        response_session = {
            'phone': phone,
            'accessToken': str(session_payload.get('accessToken') or '').strip(),
            'refreshToken': str(session_payload.get('refreshToken') or '').strip(),
            'ticket': str(session_payload.get('ticket') or '').strip(),
            'snowflakeId': str(session_payload.get('snowflakeId') or member_id).strip(),
            'signature': str(session_payload.get('signature') or '').strip(),
            'selectedAccountName': str(session_payload.get('selectedAccountName') or '').strip(),
            'accountNamespace': fallback_namespace,
            'accountNamespaceLunch': str(session_payload.get('accountNamespaceLunch') or session_payload.get('accountNamespace') or '').strip(),
            'accountNamespaceDinner': str(session_payload.get('accountNamespaceDinner') or session_payload.get('accountNamespace') or '').strip(),
            'accessTokenExpiresIn': ttl if ttl > 0 else 3600,
        }
        if is_first_bind:
            _trigger_initial_recommendations_for_new_user(user, fallback_namespace)
        return _resp(data={'userId': user.id, 'session': response_session, 'profile': response_profile, 'raw': bundle.get('raw') if isinstance(bundle, dict) else {}})
    except Exception as exc:
        logger.warning('meican.phone_login_failed phone=%s err=%s', phone, exc)
        return _resp(code=50212, message=f'MEICAN_PHONE_LOGIN_FAILED:{exc}')


def put_user_preferences(request, user_id):
    if request.method != 'PUT':
        return _resp(code=40500, message='请求方式错误，请使用PUT')

    body, err = _parse_json_body(request)
    if err:
        return err

    staple_raw = str(body.get('staple') or 'rice').lower()
    if any(x in staple_raw for x in ('noodle', 'mian', 'fen', '面')):
        staple_norm = 'noodle'
    else:
        staple_norm = 'rice'

    user = _ensure_user(user_id)
    obj, _ = UserPreference.objects.update_or_create(
        user=user,
        defaults={
            'prefers_spicy': 1 if body.get('prefersSpicy') else 0,
            'is_halal': 1 if body.get('isHalal') else 0,
            'is_cutting': 1 if body.get('isCutting') else 0,
            'staple': staple_norm,
            'taboo': str(body.get('taboo') or ''),
            'price_min': body.get('priceMin'),
            'price_max': body.get('priceMax'),
        },
    )
    return _resp(data={'userId': user.id, 'preferenceId': obj.id})


def get_user_preferences(request, user_id):
    if request.method != 'GET':
        return _resp(code=40500, message='请求方式错误，请使用GET')
    user = _ensure_user(user_id)
    pref = UserPreference.objects.filter(user=user).first()
    if not pref:
        return _resp(
            data={
                'prefersSpicy': False,
                'isHalal': False,
                'isCutting': False,
                'staple': 'rice',
                'taboo': '',
                'priceMin': None,
                'priceMax': None,
            }
        )
    return _resp(
        data={
            'prefersSpicy': bool(pref.prefers_spicy),
            'isHalal': bool(pref.is_halal),
            'isCutting': bool(pref.is_cutting),
            'staple': pref.staple or 'rice',
            'taboo': pref.taboo or '',
            'priceMin': float(pref.price_min) if pref.price_min is not None else None,
            'priceMax': float(pref.price_max) if pref.price_max is not None else None,
        }
    )


def user_preferences(request, user_id):
    if request.method == 'GET':
        return get_user_preferences(request, user_id)
    if request.method == 'PUT':
        return put_user_preferences(request, user_id)
    return _resp(code=40500, message='仅支持GET或PUT')


def put_auto_order_config(request, user_id):
    if request.method != 'PUT':
        return _resp(code=40500, message='请求方式错误，请使用PUT')

    body, err = _parse_json_body(request)
    if err:
        return err

    meal_slots = body.get('mealSlots') or []
    if not isinstance(meal_slots, list):
        return _resp(code=40003, message='mealSlots必须是数组')
    slots = [_normalize_meal_slot(x) for x in meal_slots]
    if any(x is None for x in slots):
        return _resp(code=40004, message='mealSlots仅支持LUNCH/DINNER')

    effective_from, parse_err = _parse_date(body.get('effectiveFrom'))
    if parse_err:
        return _resp(code=40005, message=parse_err)

    effective_to = None
    if body.get('effectiveTo'):
        effective_to, parse_err = _parse_date(body.get('effectiveTo'))
        if parse_err:
            return _resp(code=40006, message=parse_err)

    user = _ensure_user(user_id)
    addr_lunch = str(
        body.get('defaultCorpAddressIdLunch')
        or body.get('default_corp_address_id_lunch')
        or body.get('defaultCorpAddressId')
        or ''
    ).strip()
    addr_dinner = str(
        body.get('defaultCorpAddressIdDinner')
        or body.get('default_corp_address_id_dinner')
        or body.get('defaultCorpAddressId')
        or ''
    ).strip()
    obj, _ = AutoOrderConfig.objects.update_or_create(
        user=user,
        defaults={
            'enabled': 1 if body.get('enabled') else 0,
            'meal_slots': ','.join(slots),
            'strategy': str(body.get('strategy') or 'TOP1'),
            'default_corp_address_id': str(body.get('defaultCorpAddressId') or ''),
            'default_corp_address_id_lunch': addr_lunch,
            'default_corp_address_id_dinner': addr_dinner,
            'effective_from': effective_from,
            'effective_to': effective_to,
        },
    )
    return _resp(data={'userId': user.id, 'configId': obj.id})


def get_auto_order_config(request, user_id):
    if request.method != 'GET':
        return _resp(code=40500, message='请求方式错误，请使用GET')

    user = _ensure_user(user_id)
    obj = AutoOrderConfig.objects.filter(user=user).first()
    if not obj:
        return _resp(
            data={
                'enabled': False,
                'mealSlots': ['LUNCH', 'DINNER'],
                'strategy': 'TOP1',
                'defaultCorpAddressId': '',
                'effectiveFrom': None,
                'effectiveTo': None,
            }
        )
    slots = _split_meal_slots(obj.meal_slots) if (obj.meal_slots or '').strip() else ['LUNCH', 'DINNER']
    return _resp(
        data={
            'enabled': bool(obj.enabled),
            'mealSlots': slots,
            'strategy': obj.strategy or 'TOP1',
            'defaultCorpAddressId': obj.default_corp_address_id or '',
            'defaultCorpAddressIdLunch': (obj.default_corp_address_id_lunch or obj.default_corp_address_id or ''),
            'defaultCorpAddressIdDinner': (obj.default_corp_address_id_dinner or obj.default_corp_address_id or ''),
            'effectiveFrom': obj.effective_from.isoformat() if obj.effective_from else None,
            'effectiveTo': obj.effective_to.isoformat() if obj.effective_to else None,
        }
    )


def user_auto_order_config(request, user_id):
    if request.method == 'GET':
        return get_auto_order_config(request, user_id)
    if request.method == 'PUT':
        return put_auto_order_config(request, user_id)
    return _resp(code=40500, message='仅支持GET或PUT')


def put_user_meican_session(request, user_id):
    """
    小程序美餐登录成功后上报 access/refresh，写入 user_meican_account。
    """
    if request.method != 'PUT':
        return _resp(code=40500, message='请求方式错误，请使用PUT')

    body, err = _parse_json_body(request)
    if err:
        return err

    access = str(body.get('accessToken') or body.get('access_token') or '').strip()
    refresh_in = str(body.get('refreshToken') or body.get('refresh_token') or '').strip()
    if not access:
        return _resp(code=40021, message='缺少accessToken')

    phone = str(body.get('phone') or '').strip()[:20]
    user = _ensure_user(user_id, phone=phone)
    existing = UserMeicanAccount.objects.filter(user=user).first()
    refresh = refresh_in or (existing.refresh_token if existing else '')
    if not refresh:
        return _resp(code=40021, message='缺少refreshToken且无历史refresh可沿用')

    expires_in = body.get('expiresIn') if body.get('expiresIn') is not None else body.get('expires_in')
    try:
        expires_in = int(expires_in) if expires_in is not None else 3600
    except (TypeError, ValueError):
        expires_in = 3600
    ttl = expires_in if isinstance(expires_in, int) and expires_in > 0 else 3600
    token_expire_at = timezone.now() + timedelta(seconds=ttl)

    username = str(
        body.get('meicanUsername')
        or body.get('meican_username')
        or body.get('selectedAccountName')
        or body.get('phone')
        or 'meican_user',
    ).strip()
    email = str(body.get('meicanEmail') or body.get('meican_email') or '').strip()
    namespace_in = str(body.get('accountNamespace') or body.get('account_namespace') or '').strip()
    namespace_lunch_in = str(body.get('accountNamespaceLunch') or body.get('account_namespace_lunch') or '').strip()
    namespace_dinner_in = str(body.get('accountNamespaceDinner') or body.get('account_namespace_dinner') or '').strip()
    # 请求未带 namespace 时保留库内原值，避免被刷成空导致推荐批次对不上
    namespace = namespace_in or (existing.account_namespace if existing else '')
    namespace_lunch = namespace_lunch_in or (existing.account_namespace_lunch if existing else '') or namespace
    namespace_dinner = namespace_dinner_in or (existing.account_namespace_dinner if existing else '') or namespace

    is_first_bind = not existing or not bool(existing.is_bound)
    obj, _ = UserMeicanAccount.objects.update_or_create(
        user=user,
        defaults={
            'meican_username': username,
            'meican_email': email,
            'access_token': access,
            'refresh_token': refresh,
            'token_expire_at': token_expire_at,
            'account_namespace': namespace,
            'account_namespace_lunch': namespace_lunch,
            'account_namespace_dinner': namespace_dinner,
            'is_bound': 1,
        },
    )
    if is_first_bind:
        _trigger_initial_recommendations_for_new_user(user, namespace)
    return _resp(data={'userId': user.id, 'meicanAccountId': obj.id, 'tokenExpireAt': token_expire_at.isoformat()})


def post_user_menu_week_sync(request, user_id):
    """
    菜单快照数据来源：由小程序在美餐拉取菜单后上报本接口，写入 menu_snapshot + menu_item。
    与 refresh_user_recommendations 使用同一套表；namespace 须与 meican-session / 美餐企业空间一致。

    请求体：
    - namespace: 企业 namespace（必填）
    - days: 数组，每项含 date(YYYY-MM-DD)、slots 对象
    - slots 的 key 支持 LUNCH/DINNER（及 MORNING/NOON/EVENING 等别名）
    - 每个 slot 可含 tabUniqueId、targetTime、dishes 或 menuItems（菜品数组）

    菜品项支持字段：dishId/id、dishName/name、priceInCent/price_cent、restaurant{ name, uniqueId } 等。
    """
    if request.method != 'POST':
        return _resp(code=40500, message='请求方式错误，请使用POST')

    body, err = _parse_json_body(request)
    if err:
        return err

    namespace = str(body.get('namespace') or '').strip()
    if not namespace:
        return _resp(code=40022, message='缺少namespace')

    days, derr = normalize_days_payload(body)
    if derr:
        return _resp(code=40023, message=derr)

    user = _ensure_user(user_id)
    out = sync_menu_days(namespace, days)
    if out.get('fatal'):
        return _resp(code=40024, message=out['errors'][-1].get('error', '日期错误'))

    return _resp(
        data={
            'userId': user.id,
            'namespace': namespace,
            'slotsSynced': out['slots_synced'],
            'errors': out['errors'][:50],
            'menuItemsCreated': out.get('menuItemsCreated', 0),
            'menuItemsUpdated': out.get('menuItemsUpdated', 0),
            'menuItemsRemoved': out.get('menuItemsRemoved', 0),
            'hint': (
                '同一 snapshot 下 dish_id 未变的菜品会原地更新（保留 menu_item 主键），一般不再级联删掉 recommendation_result；'
                '本次 payload 中已消失的 dish_id 仍会删除对应 menu_item（其推荐行会随级联删除）。'
                '若菜单结构大变，仍建议重跑 refresh_user_recommendations。'
            ),
        }
    )


def _weekday_label_zh(dt_val):
    labels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    return labels[dt_val.weekday()]


def _normalize_menu_status(raw_status: str) -> str:
    s = str(raw_status or '').lower().strip()
    sold_out_markers = {'sold_out', 'soldout', 'unavailable', 'off', 'closed'}
    return 'sold_out' if s in sold_out_markers else 'available'


def _is_meican_recommended(raw_json):
    if not isinstance(raw_json, dict):
        return False
    direct_true_fields = [
        'isRecommended',
        'recommended',
        'fromRecommendation',
        'fromRecommended',
    ]
    for key in direct_true_fields:
        if raw_json.get(key) is True:
            return True
    non_empty_hint_fields = [
        'recommendReason',
        'recommendationReason',
        'recommendReasonText',
        'reason',
        'rankNo',
        'recommendationScore',
    ]
    for key in non_empty_hint_fields:
        val = raw_json.get(key)
        if val not in (None, '', 0, '0'):
            return True
    source_val = str(raw_json.get('source') or raw_json.get('from') or '').lower().strip()
    return source_val in {'recommendation', 'recommended', 'meican_recommendation'}


def _build_meal_sections_from_snapshots(snapshots):
    sections = []
    for snap in snapshots:
        items = list(MenuItem.objects.filter(snapshot=snap).order_by('id'))
        rest_map = {}
        recommended = []
        fallback_recommended = []
        for item in items:
            status = _normalize_menu_status(item.status)
            row = {
                'id': item.id,
                'name': item.dish_name,
                'price': f'{(item.price_cent or 0) / 100:.2f}' if item.price_cent else '',
                'status': status,
                'statusText': '售罄' if status == 'sold_out' else '可选',
            }
            rkey = f'{item.restaurant_id or ""}|{item.restaurant_name or ""}'
            if rkey not in rest_map:
                rest_map[rkey] = {
                    'id': item.restaurant_id or '',
                    'name': item.restaurant_name or '未知餐厅',
                    'menus': [],
                }
            rest_map[rkey]['menus'].append(row)
            if status == 'available':
                if _is_meican_recommended(item.raw_json) and len(recommended) < 8:
                    recommended.append(row)
                if len(fallback_recommended) < 8:
                    fallback_recommended.append(row)
        if not recommended:
            recommended = fallback_recommended
        key = 'morning' if snap.meal_slot == MealSlot.LUNCH else 'afternoon'
        title = '上午餐期' if key == 'morning' else '下午餐期'
        sections.append(
            {
                'key': key,
                'tabUniqueId': snap.tab_unique_id or '',
                'targetTime': _format_target_time(snap.target_time),
                'title': title,
                'restaurants': list(rest_map.values()),
                'recommendedDishes': recommended,
            }
        )
    sections.sort(key=lambda x: x['key'])
    return sections


def get_user_menu_weekly(request, user_id):
    """
    Web/H5 菜单页接口（对齐 mc1 pages/meican/index 需要的数据结构）：
    返回 weekDates + selectedDate + mealSections（餐厅分组 / 推荐菜 / 售罄状态）。
    """
    if request.method != 'GET':
        return _resp(code=40500, message='请求方式错误，请使用GET')

    user = _ensure_user(user_id)
    acc = UserMeicanAccount.objects.filter(user=user).first()
    namespace = str(request.GET.get('namespace') or (acc.account_namespace if acc else '') or '').strip()
    if not namespace:
        return _resp(code=40022, message='缺少namespace')

    date_val, parse_err = _parse_date(request.GET.get('date'), default_today=True)
    if parse_err:
        return _resp(code=40007, message=parse_err)

    # 以 date 所在周为基准，输出周一~周日中的工作日 chips（与小程序体验对齐）。
    monday = date_val - timedelta(days=(date_val.weekday()))
    week_days = [monday + timedelta(days=i) for i in range(7)]
    work_days = [d for d in week_days if d.weekday() < 5]

    if request.GET.get('sync') in {'1', 'true', 'yes'} and acc:
        try:
            sync_meican_menu_snapshot_for_user_dates(user, namespace, work_days, language='zh-CN')
        except Exception as exc:
            logger.warning('menu_weekly sync failed user_id=%s namespace=%s err=%s', user.id, namespace, exc)

    week_dates = []
    for d in work_days:
        week_dates.append(
            {
                'dateKey': str(d),
                'label': f'{d.month}月{d.day}日',
                'weekLabel': _weekday_label_zh(d),
                'isToday': d == timezone.now().date(),
            }
        )

    selected_date_obj = date_val if date_val in work_days else (work_days[0] if work_days else date_val)
    snapshots = list(
        MenuSnapshot.objects.filter(
            namespace=namespace, date=selected_date_obj, meal_slot__in=[MealSlot.LUNCH, MealSlot.DINNER]
        )
        .order_by('meal_slot', 'id')
    )
    meal_sections = _build_meal_sections_from_snapshots(snapshots)

    return _resp(
        data={
            'namespace': namespace,
            'weekDates': week_dates,
            'selectedDate': str(selected_date_obj),
            'mealSections': meal_sections,
        }
    )


def get_daily_recommendations(request, user_id):
    if request.method != 'GET':
        return _resp(code=40500, message='请求方式错误，请使用GET')

    date_val, parse_err = _parse_date(request.GET.get('date'), default_today=True)
    if parse_err:
        return _resp(code=40007, message=parse_err)

    namespace = request.GET.get('namespace') or ''
    user = _ensure_user(user_id)
    order_map = {
        x.meal_slot: x for x in OrderRecord.objects.filter(user=user, date=date_val, status='CREATED')
    }

    result = {
        'date': str(date_val),
        'LUNCH': [],
        'DINNER': [],
        'orderedMeals': {
            MealSlot.LUNCH: None,
            MealSlot.DINNER: None,
        },
    }
    for slot in [MealSlot.LUNCH, MealSlot.DINNER]:
        od = order_map.get(slot)
        if not od:
            continue
        mi = od.menu_item
        result['orderedMeals'][slot] = {
            'menuItemId': mi.id if mi else None,
            'dishName': (mi.dish_name if mi else ''),
            'restaurantName': (mi.restaurant_name if mi else ''),
            'priceCent': (mi.price_cent if mi else None),
            'orderRecordId': od.id,
            'status': od.status,
        }

    for slot in [MealSlot.LUNCH, MealSlot.DINNER]:
        batch_qs = RecommendationBatch.objects.filter(date=date_val, meal_slot=slot).order_by('-version', '-id')
        if namespace:
            batch_qs = batch_qs.filter(namespace=namespace)
        batch_qs = batch_qs.filter(status__in=[RecommendationBatchStatus.FROZEN, RecommendationBatchStatus.READY])
        batch = batch_qs.first()
        if not batch:
            continue
        recs = (
            RecommendationResult.objects
            .select_related('menu_item')
            .filter(batch=batch, user=user)
            .order_by('rank_no')[:3]
        )
        slot_data = []
        ordered_menu_item_id = (
            result['orderedMeals'][slot]['menuItemId']
            if isinstance(result['orderedMeals'].get(slot), dict)
            else None
        )
        for rec in recs:
            item = rec.menu_item
            slot_data.append({
                'rankNo': rec.rank_no,
                'menuItemId': item.id,
                'dishName': item.dish_name,
                'restaurantName': item.restaurant_name,
                'priceCent': item.price_cent,
                'score': float(rec.score),
                'reason': rec.reason,
                'ordered': bool(ordered_menu_item_id and item.id == ordered_menu_item_id),
            })
        result[str(slot)] = slot_data

    return _resp(data=result)


def get_user_order_addresses(request, user_id):
    if request.method != 'GET':
        return _resp(code=40500, message='请求方式错误，请使用GET')

    user = _ensure_user(user_id)
    acc = UserMeicanAccount.objects.filter(user=user).first()
    if not acc or not str(acc.access_token or '').strip():
        return _resp(code=40102, message='MEICAN_SESSION_REQUIRED')
    if acc.token_expire_at and acc.token_expire_at <= timezone.now():
        _refresh_user_meican_account_token(acc)

    namespace = str(request.GET.get('namespace') or acc.account_namespace or '').strip()
    if not namespace:
        return _resp(code=40022, message='MEICAN_NAMESPACE_REQUIRED')
    try:
        options = _fetch_address_options(namespace, str(acc.access_token or '').strip())
    except Exception as e:
        return _resp(code=50201, message=f'ADDRESS_FETCH_FAILED:{e}')
    meal_slot = _normalize_meal_slot(request.GET.get('mealSlot'))
    cfg = AutoOrderConfig.objects.filter(user=user).first()
    selected = ''
    if cfg:
        if meal_slot == MealSlot.LUNCH:
            selected = str(cfg.default_corp_address_id_lunch or cfg.default_corp_address_id or '').strip()
        elif meal_slot == MealSlot.DINNER:
            selected = str(cfg.default_corp_address_id_dinner or cfg.default_corp_address_id or '').strip()
        else:
            selected = str(cfg.default_corp_address_id or '').strip()
    return _resp(
        data={
            'namespace': namespace,
            'options': options,
            'selectedCorpAddressId': selected,
        }
    )


def post_manual_order(request, user_id):
    if request.method != 'POST':
        return _resp(code=40500, message='请求方式错误，请使用POST')

    body, err = _parse_json_body(request)
    if err:
        return err

    date_val, parse_err = _parse_date(body.get('date'))
    if parse_err:
        return _resp(code=40008, message=parse_err)
    meal_slot = _normalize_meal_slot(body.get('mealSlot'))
    if not meal_slot:
        return _resp(code=40009, message='mealSlot仅支持LUNCH/DINNER')

    menu_item_id = body.get('menuItemId')
    if not menu_item_id:
        return _resp(code=40010, message='缺少menuItemId')
    idempotency_key = str(body.get('idempotencyKey') or '')
    if not idempotency_key:
        return _resp(code=40011, message='缺少idempotencyKey')

    try:
        menu_item = MenuItem.objects.get(id=menu_item_id)
    except MenuItem.DoesNotExist:
        return _resp(code=40401, message='MENU_ITEM_UNAVAILABLE')

    user = _ensure_user(user_id)
    namespace = str(body.get('namespace') or '').strip()
    selected_user_addr = str(body.get('userAddressUniqueId') or '').strip()
    selected_corp_addr = str(body.get('corpAddressUniqueId') or body.get('defaultCorpAddressId') or '').strip()
    replace = bool(body.get('replace') or body.get('replaceOrder'))
    acc = UserMeicanAccount.objects.filter(user=user).first()
    if acc and acc.token_expire_at and acc.token_expire_at <= timezone.now():
        _refresh_user_meican_account_token(acc)
    if not namespace and acc:
        namespace = (
            str(acc.account_namespace_lunch or '').strip()
            if meal_slot == MealSlot.LUNCH
            else str(acc.account_namespace_dinner or '').strip()
        ) or str(acc.account_namespace or '').strip()
    with transaction.atomic():
        existed_by_idem = OrderRecord.objects.filter(idempotency_key=idempotency_key).first()
        if existed_by_idem:
            return _resp(data={'orderId': existed_by_idem.id, 'status': existed_by_idem.status, 'idempotent': True})

        existing_created = OrderRecord.objects.filter(
            user=user, date=date_val, meal_slot=meal_slot, status='CREATED'
        ).first()
        if existing_created:
            if replace:
                existing_created.delete()
            else:
                return _resp(code=40901, message='ORDER_ALREADY_EXISTS')

        try:
            logger.info(
                'manual_order.submit_start user_id=%s date=%s meal_slot=%s menu_item_id=%s namespace=%s replace=%s',
                user.id,
                str(date_val),
                meal_slot,
                menu_item.id,
                namespace,
                replace,
            )
            meican_order_unique_id, used_user_addr, used_corp_addr = _submit_meican_order_for_manual(
                user,
                menu_item,
                namespace,
                selected_user_addr=selected_user_addr,
                selected_corp_addr=selected_corp_addr,
            )
        except ValueError as e:
            msg = str(e)
            logger.warning(
                'manual_order.submit_failed user_id=%s date=%s meal_slot=%s menu_item_id=%s namespace=%s error=%s',
                user.id,
                str(date_val),
                meal_slot,
                menu_item.id,
                namespace,
                msg,
            )
            if msg == 'NO_DEFAULT_ADDRESS':
                return _resp(code=40903, message='NO_DEFAULT_ADDRESS')
            if msg == 'MEICAN_SESSION_REQUIRED':
                return _resp(code=40102, message='MEICAN_SESSION_REQUIRED')
            if msg == 'MEICAN_NAMESPACE_REQUIRED':
                return _resp(code=40022, message='MEICAN_NAMESPACE_REQUIRED')
            if msg == 'MENU_ITEM_FORWARD_CONTEXT_MISSING':
                return _resp(code=40904, message='MENU_ITEM_FORWARD_CONTEXT_MISSING')
            return _resp(code=50201, message=msg)

        _save_default_corp_address(user, used_corp_addr, meal_slot)

        existing_any = (
            OrderRecord.objects.filter(user=user, date=date_val, meal_slot=meal_slot)
            .order_by('-id')
            .first()
        )
        if existing_any:
            # 复用同 user/date/slot 记录，避免触发 uk_user_date_slot 唯一键冲突
            existing_any.menu_item = menu_item
            existing_any.source = 'MANUAL'
            existing_any.status = 'CREATED'
            existing_any.idempotency_key = idempotency_key
            existing_any.meican_order_unique_id = meican_order_unique_id
            existing_any.save(update_fields=['menu_item', 'source', 'status', 'idempotency_key', 'meican_order_unique_id'])
            order = existing_any
        else:
            order = OrderRecord.objects.create(
                user=user,
                date=date_val,
                meal_slot=meal_slot,
                menu_item=menu_item,
                source='MANUAL',
                status='CREATED',
                idempotency_key=idempotency_key,
                meican_order_unique_id=meican_order_unique_id,
            )
        logger.info(
            'manual_order.submit_success user_id=%s order_id=%s date=%s meal_slot=%s menu_item_id=%s meican_order_unique_id=%s',
            user.id,
            order.id,
            str(date_val),
            meal_slot,
            menu_item.id,
            meican_order_unique_id,
        )
    return _resp(
        data={
            'orderId': order.id,
            'status': order.status,
            'meicanOrderUniqueId': meican_order_unique_id,
            'defaultCorpAddressId': used_corp_addr,
            'userAddressUniqueId': used_user_addr,
        }
    )


def post_manual_order_cancel(request, user_id):
    if request.method != 'POST':
        return _resp(code=40500, message='请求方式错误，请使用POST')

    body, err = _parse_json_body(request)
    if err:
        return err

    date_val, parse_err = _parse_date(body.get('date'))
    if parse_err:
        return _resp(code=40008, message=parse_err)
    meal_slot = _normalize_meal_slot(body.get('mealSlot'))
    if not meal_slot:
        return _resp(code=40009, message='mealSlot仅支持LUNCH/DINNER')

    user = _ensure_user(user_id)
    record = (
        OrderRecord.objects.filter(user=user, date=date_val, meal_slot=meal_slot, status='CREATED')
        .order_by('-id')
        .first()
    )
    if not record:
        return _resp(code=40402, message='ORDER_NOT_FOUND')

    order_uid = str(body.get('orderUniqueId') or record.meican_order_unique_id or '').strip()
    try:
        if order_uid:
            _cancel_meican_order_for_user(user, order_uid)
    except ValueError as e:
        return _resp(code=50202, message=str(e))

    record.status = 'CANCELED'
    record.save(update_fields=['status'])
    return _resp(data={'orderId': record.id, 'status': record.status, 'orderUniqueId': order_uid})


def _internal_auth_ok(request):
    configured_token = str(getattr(settings, 'INTERNAL_JOB_TOKEN', '') or '')
    if not configured_token:
        return True
    return request.headers.get('X-Internal-Token', '') == configured_token


def _is_too_fast_error(msg: str) -> bool:
    text = str(msg or '')
    return ('TOO_FAST' in text) or ('下单过快' in text)


def run_auto_order_job_for_date_slot(date_val, meal_slot, *, force=False, trigger_type='MANUAL', enforce_window=True):
    """
    执行自动下单任务：
    - 基于 auto_order_config + 推荐结果挑选菜品
    - 调用美餐 Forward /api/v2.1/orders/add 真正下单
    - 回写 auto_order_job / auto_order_job_item / order_record
    """
    if enforce_window and not force and not _within_auto_order_window(date_val, meal_slot):
        return {'ok': False, 'code': 40902, 'message': '超过自动下单截止时间窗口'}

    job, created = AutoOrderJob.objects.get_or_create(
        date=date_val,
        meal_slot=meal_slot,
        trigger_type=trigger_type,
        defaults={'status': JobStatus.PENDING},
    )
    if not created and not force:
        return {'ok': True, 'data': {'jobId': job.id, 'status': job.status, 'created': False}}

    if not created and force:
        AutoOrderJobItem.objects.filter(job=job).delete()
        job.status = JobStatus.PENDING
        job.total_count = 0
        job.success_count = 0
        job.failed_count = 0
        job.started_at = timezone.now()
        job.finished_at = None
        job.save(update_fields=['status', 'total_count', 'success_count', 'failed_count', 'started_at', 'finished_at'])

    if created:
        job.started_at = timezone.now()
        job.save(update_fields=['started_at'])

    cfg_qs = AutoOrderConfig.objects.select_related('user').filter(enabled=1, effective_from__lte=date_val).filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=date_val)
    )
    total = 0
    success = 0
    failed = 0
    min_submit_interval_seconds = float(getattr(settings, 'AUTO_ORDER_SUBMIT_INTERVAL_SECONDS', 2))
    too_fast_max_retries = max(0, int(getattr(settings, 'AUTO_ORDER_TOO_FAST_MAX_RETRIES', 2.5)))
    too_fast_retry_delay_seconds = float(getattr(settings, 'AUTO_ORDER_TOO_FAST_RETRY_DELAY_SECONDS', 3))
    last_submit_ts = 0.0
    for cfg in cfg_qs:
        slots = _split_meal_slots(cfg.meal_slots)
        if meal_slot not in slots:
            continue
        total += 1
        user = cfg.user

        default_addr = (
            (cfg.default_corp_address_id_lunch if meal_slot == MealSlot.LUNCH else cfg.default_corp_address_id_dinner)
            or cfg.default_corp_address_id
            or ''
        )

        if OrderRecord.objects.filter(user=user, date=date_val, meal_slot=meal_slot, status='CREATED').exists():
            failed += 1
            AutoOrderJobItem.objects.update_or_create(
                job=job,
                user=user,
                defaults={
                    'status': JobItemStatus.SKIPPED,
                    'retry_count': 0,
                    'fail_code': 'ORDER_ALREADY_EXISTS',
                    'fail_message': '该用户该餐期已存在订单',
                    'menu_item': None,
                    'meican_order_unique_id': '',
                },
            )
            continue

        rec = (
            RecommendationResult.objects
            .select_related('menu_item', 'batch')
            .filter(user=user, batch__date=date_val, batch__meal_slot=meal_slot)
            .order_by('rank_no')
            .first()
        )
        if not rec or not rec.menu_item or rec.menu_item.status != 'available':
            failed += 1
            AutoOrderJobItem.objects.update_or_create(
                job=job,
                user=user,
                defaults={
                    'status': JobItemStatus.FAILED,
                    'retry_count': 0,
                    'fail_code': 'MENU_ITEM_UNAVAILABLE',
                    'fail_message': '推荐菜品不可用',
                    'menu_item': rec.menu_item if rec else None,
                    'meican_order_unique_id': '',
                },
            )
            continue

        menu_item = rec.menu_item
        acc = UserMeicanAccount.objects.filter(user=user).first()
        namespace = ''
        if acc:
            if meal_slot == MealSlot.LUNCH:
                namespace = str(acc.account_namespace_lunch or '').strip()
            else:
                namespace = str(acc.account_namespace_dinner or '').strip()
            if not namespace:
                namespace = str(acc.account_namespace or '').strip()
        try:
            meican_order_unique_id = ''
            for attempt in range(too_fast_max_retries + 1):
                now_ts = pytime.time()
                wait_seconds = max(0.0, min_submit_interval_seconds - (now_ts - last_submit_ts))
                if wait_seconds > 0:
                    pytime.sleep(wait_seconds)
                try:
                    meican_order_unique_id, _, _ = _submit_meican_order_for_manual(
                        user,
                        menu_item,
                        namespace,
                        selected_user_addr='',
                        selected_corp_addr=str(default_addr or '').strip(),
                    )
                    last_submit_ts = pytime.time()
                    break
                except ValueError as e:
                    last_submit_ts = pytime.time()
                    msg = str(e)
                    if _is_too_fast_error(msg) and attempt < too_fast_max_retries:
                        retry_wait = too_fast_retry_delay_seconds * (attempt + 1)
                        logger.warning(
                            'auto_order.too_fast_retry user_id=%s date=%s meal_slot=%s attempt=%s wait_seconds=%s',
                            user.id,
                            str(date_val),
                            meal_slot,
                            attempt + 1,
                            retry_wait,
                        )
                        pytime.sleep(retry_wait)
                        continue
                    raise
            idem = f'auto:{job.id}:{user.id}:{date_val}:{meal_slot}:{uuid.uuid4().hex[:8]}'
            reused = (
                OrderRecord.objects.filter(user=user, date=date_val, meal_slot=meal_slot)
                .order_by('-id')
                .first()
            )
            if reused:
                reused.menu_item = menu_item
                reused.source = 'AUTO'
                reused.status = 'CREATED'
                reused.idempotency_key = idem
                reused.meican_order_unique_id = meican_order_unique_id
                reused.save(update_fields=['menu_item', 'source', 'status', 'idempotency_key', 'meican_order_unique_id'])
            else:
                OrderRecord.objects.create(
                    user=user,
                    date=date_val,
                    meal_slot=meal_slot,
                    menu_item=menu_item,
                    source='AUTO',
                    status='CREATED',
                    idempotency_key=idem,
                    meican_order_unique_id=meican_order_unique_id,
                )
            success += 1
            AutoOrderJobItem.objects.update_or_create(
                job=job,
                user=user,
                defaults={
                    'menu_item': menu_item,
                    'status': JobItemStatus.SUCCESS,
                    'retry_count': 0,
                    'fail_code': '',
                    'fail_message': '',
                    'meican_order_unique_id': meican_order_unique_id,
                },
            )
        except ValueError as e:
            failed += 1
            msg = str(e)
            fail_code = msg.split(':', 1)[0] if ':' in msg else msg
            AutoOrderJobItem.objects.update_or_create(
                job=job,
                user=user,
                defaults={
                    'menu_item': menu_item,
                    'status': JobItemStatus.FAILED,
                    'retry_count': 0,
                    'fail_code': fail_code,
                    'fail_message': msg,
                    'meican_order_unique_id': '',
                },
            )

    job.total_count = total
    job.success_count = success
    job.failed_count = failed
    if total == 0:
        job.status = JobStatus.SUCCESS
    elif success == total:
        job.status = JobStatus.SUCCESS
    elif success > 0:
        job.status = JobStatus.PARTIAL
    else:
        job.status = JobStatus.FAILED
    job.finished_at = timezone.now()
    job.save(update_fields=['total_count', 'success_count', 'failed_count', 'status', 'finished_at'])
    return {'ok': True, 'data': {'jobId': job.id, 'status': job.status, 'created': created or force}}


def post_internal_auto_order_run(request):
    if request.method != 'POST':
        return _resp(code=40500, message='请求方式错误，请使用POST')
    if not _internal_auth_ok(request):
        return _resp(code=40101, message='内部鉴权失败')

    body, err = _parse_json_body(request)
    if err:
        return err

    date_val, parse_err = _parse_date(body.get('date'))
    if parse_err:
        return _resp(code=40012, message=parse_err)
    meal_slot = _normalize_meal_slot(body.get('mealSlot'))
    if not meal_slot:
        return _resp(code=40013, message='mealSlot仅支持LUNCH/DINNER')

    force = bool(body.get('force'))
    out = run_auto_order_job_for_date_slot(
        date_val,
        meal_slot,
        force=force,
        trigger_type='MANUAL',
        enforce_window=True,
    )
    if not out.get('ok'):
        return _resp(code=out.get('code', 50000), message=out.get('message', '自动下单任务执行失败'))
    return _resp(data=out['data'])


def get_internal_auto_order_job(request, job_id):
    if request.method != 'GET':
        return _resp(code=40500, message='请求方式错误，请使用GET')
    if not _internal_auth_ok(request):
        return _resp(code=40101, message='内部鉴权失败')

    try:
        job = AutoOrderJob.objects.get(id=job_id)
    except AutoOrderJob.DoesNotExist:
        return _resp(code=40402, message='job不存在')

    failed_items = list(
        AutoOrderJobItem.objects.filter(job=job, status__in=[JobItemStatus.FAILED, JobItemStatus.SKIPPED])
        .values('user_id', 'status', 'retry_count', 'fail_code', 'fail_message')[:200]
    )
    retry_count = AutoOrderJobItem.objects.filter(job=job, retry_count__gt=0).count()
    return _resp(
        data={
            'jobId': job.id,
            'date': str(job.date),
            'mealSlot': job.meal_slot,
            'status': job.status,
            'totalCount': job.total_count,
            'successCount': job.success_count,
            'failedCount': job.failed_count,
            'retryCount': retry_count,
            'failedItems': failed_items,
            'startedAt': job.started_at.isoformat() if job.started_at else None,
            'finishedAt': job.finished_at.isoformat() if job.finished_at else None,
        }
    )


def post_internal_weekly_recommendations_run(request):
    """
    每周日由云托管「定时触发」调用：为已绑定 namespace 且有偏好的用户生成下周工作日推荐。
    请求体可选：weekStart（周一 YYYY-MM-DD）、freeze、topN、workdays、userId、requireSunday（默认 false）。
    生产环境可将 RECOMMENDATION_WEEKLY_REQUIRE_SUNDAY=true，仅允许周日执行。
    """
    if request.method != 'POST':
        return _resp(code=40500, message='请求方式错误，请使用POST')
    if not _internal_auth_ok(request):
        return _resp(code=40101, message='内部鉴权失败')

    body, err = _parse_json_body(request)
    if err:
        body = {}

    if getattr(settings, 'RECOMMENDATION_WEEKLY_REQUIRE_SUNDAY', False):
        if timezone.now().date().weekday() != 6:
            return _resp(code=40903, message='RECOMMENDATION_WEEKLY_REQUIRE_SUNDAY：仅允许周日执行')

    if body.get('requireSunday'):
        if timezone.now().date().weekday() != 6:
            return _resp(code=40904, message='requireSunday：仅允许周日执行')

    week_start = None
    if body.get('weekStart'):
        week_start, werr = _parse_date(body.get('weekStart'))
        if werr:
            return _resp(code=40030, message=werr)

    freeze = bool(body.get('freeze'))
    try:
        top_n = int(body.get('topN', body.get('top_n', 3)))
    except (TypeError, ValueError):
        top_n = 3
    try:
        workdays = int(body.get('workdays', 5))
    except (TypeError, ValueError):
        workdays = 5

    user_id = body.get('userId') if body.get('userId') is not None else body.get('user_id')
    try:
        user_id = int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        return _resp(code=40031, message='userId 无效')

    summary = run_weekly_recommendation_job(
        week_start=week_start,
        freeze=freeze,
        top_n=top_n,
        workdays=workdays,
        user_id=user_id,
    )
    return _resp(
        data={
            'weekStartMonday': summary['weekStartMonday'],
            'createdCount': len(summary['created']),
            'skippedCount': len(summary['skipped']),
            'created': summary['created'][:500],
            'skipped': summary['skipped'][:500],
        }
    )
