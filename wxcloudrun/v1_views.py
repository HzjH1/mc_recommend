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
    OrderRecord,
    RecommendationBatch,
    RecommendationBatchStatus,
    RecommendationResult,
    UserAccount,
    UserMeicanAccount,
    UserPreference,
)
from wxcloudrun.menu_sync_service import normalize_days_payload, sync_menu_days
from wxcloudrun.meican_client_config import (
    resolve_forward_base_url,
    resolve_forward_credentials,
    resolve_forward_referer,
    resolve_forward_user_agent,
    resolve_graphql_app,
    resolve_x_mc_device,
)
from wxcloudrun.recommendation_service import run_weekly_recommendation_job

# settings.LOGGING 仅显式配置了 logger "log"，这里统一走该通道，确保落盘到 all-*.log
logger = logging.getLogger('log')


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

    result = {'date': str(date_val), 'LUNCH': [], 'DINNER': []}
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
                'ordered': slot in order_map,
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
    min_submit_interval_seconds = float(getattr(settings, 'AUTO_ORDER_SUBMIT_INTERVAL_SECONDS', 1.2))
    too_fast_max_retries = max(0, int(getattr(settings, 'AUTO_ORDER_TOO_FAST_MAX_RETRIES', 2)))
    too_fast_retry_delay_seconds = float(getattr(settings, 'AUTO_ORDER_TOO_FAST_RETRY_DELAY_SECONDS', 1.5))
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
