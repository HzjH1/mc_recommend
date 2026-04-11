import json
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q
from django.http import JsonResponse
from django.utils import timezone

from wxcloudrun.meican_oauth import MeicanOAuthError, ensure_valid_access_token, iter_user_ids_due_for_refresh, refresh_user_meican_token_locked
from wxcloudrun.recommendation_engine import rank_menu_items_for_user
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


def _ensure_user(user_id):
    user, _ = UserAccount.objects.get_or_create(id=int(user_id), defaults={'status': 1})
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


def _monday_of_date(d):
    return d - timedelta(days=d.weekday())


def _parse_target_time(value):
    if not value:
        return None
    s = str(value).strip()[:19]
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _normalize_slot_key(key):
    val = str(key or '').upper().strip()
    aliases = {
        'MORNING': MealSlot.LUNCH,
        'LUNCH': MealSlot.LUNCH,
        'NOON': MealSlot.LUNCH,
        'AFTERNOON': MealSlot.DINNER,
        'DINNER': MealSlot.DINNER,
        'EVENING': MealSlot.DINNER,
    }
    return aliases.get(val)


def _resolve_recommendation_batch(user, date_val, meal_slot, namespace: str):
    ns = namespace or ''
    lead = (
        RecommendationResult.objects.filter(
            user=user,
            batch__date=date_val,
            batch__meal_slot=meal_slot,
            batch__namespace=ns,
            batch__status__in=[RecommendationBatchStatus.FROZEN, RecommendationBatchStatus.READY],
        )
        .select_related('batch')
        .order_by('-batch__version', '-batch__id')
        .first()
    )
    return lead.batch if lead else None


def put_user_preferences(request, user_id):
    if request.method != 'PUT':
        return _resp(code=40500, message='请求方式错误，请使用PUT')

    body, err = _parse_json_body(request)
    if err:
        return err

    staple = str(body.get('staple') or '').lower()
    if staple and staple not in {'rice', 'noodle'}:
        return _resp(code=40002, message='staple仅支持rice/noodle')

    user = _ensure_user(user_id)
    obj, _ = UserPreference.objects.update_or_create(
        user=user,
        defaults={
            'prefers_spicy': 1 if body.get('prefersSpicy') else 0,
            'is_halal': 1 if body.get('isHalal') else 0,
            'is_cutting': 1 if body.get('isCutting') else 0,
            'staple': staple,
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
    obj, _ = AutoOrderConfig.objects.update_or_create(
        user=user,
        defaults={
            'enabled': 1 if body.get('enabled') else 0,
            'meal_slots': ','.join(slots),
            'strategy': str(body.get('strategy') or 'TOP1'),
            'default_corp_address_id': str(body.get('defaultCorpAddressId') or ''),
            'effective_from': effective_from,
            'effective_to': effective_to,
        },
    )
    return _resp(data={'userId': user.id, 'configId': obj.id})


def get_daily_recommendations(request, user_id):
    if request.method != 'GET':
        return _resp(code=40500, message='请求方式错误，请使用GET')

    date_val, parse_err = _parse_date(request.GET.get('date'), default_today=True)
    if parse_err:
        return _resp(code=40007, message=parse_err)

    namespace = request.GET.get('namespace') or ''
    user = _ensure_user(user_id)
    order_map = {
        x.meal_slot: x for x in OrderRecord.objects.filter(user=user, date=date_val)
    }

    result = {'date': str(date_val), 'LUNCH': [], 'DINNER': []}
    for slot in [MealSlot.LUNCH, MealSlot.DINNER]:
        batch = _resolve_recommendation_batch(user, date_val, slot, namespace)
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
        result[slot] = slot_data

    return _resp(data=result)


def get_week_recommendations(request, user_id):
    if request.method != 'GET':
        return _resp(code=40500, message='请求方式错误，请使用GET')

    week_raw = request.GET.get('weekStart')
    if week_raw:
        anchor, werr = _parse_date(week_raw)
        if werr:
            return _resp(code=40025, message=werr)
    else:
        anchor = timezone.now().date()
    monday = _monday_of_date(anchor)

    namespace = request.GET.get('namespace') or ''
    if not namespace:
        account = UserMeicanAccount.objects.filter(user_id=int(user_id)).first()
        if account:
            namespace = account.account_namespace or ''

    user = _ensure_user(user_id)
    days_out = []
    for i in range(5):
        d = monday + timedelta(days=i)
        day_obj = {'date': str(d), 'LUNCH': {'items': []}, 'DINNER': {'items': []}}
        for slot in [MealSlot.LUNCH, MealSlot.DINNER]:
            batch = _resolve_recommendation_batch(user, d, slot, namespace)
            if not batch:
                continue
            recs = (
                RecommendationResult.objects.filter(batch=batch, user=user)
                .select_related('menu_item')
                .order_by('rank_no')[:3]
            )
            items = []
            for rec in recs:
                it = rec.menu_item
                raw = it.raw_json if isinstance(it.raw_json, dict) else {}
                items.append({
                    'rankNo': rec.rank_no,
                    'menuItemId': it.id,
                    'dishId': it.dish_id,
                    'dishName': it.dish_name,
                    'restaurantName': it.restaurant_name,
                    'priceCent': it.price_cent,
                    'score': float(rec.score),
                    'reason': rec.reason,
                    'tabUniqueId': raw.get('tabUniqueId', ''),
                    'targetTime': raw.get('targetTime', ''),
                    'corpNamespace': raw.get('corpNamespace', '') or namespace,
                })
            day_obj[slot] = {'items': items}
        days_out.append(day_obj)

    return _resp(data={'weekStart': str(monday), 'namespace': namespace, 'days': days_out})


def post_user_sync_meican_week(request, user_id):
    if request.method != 'POST':
        return _resp(code=40500, message='请求方式错误，请使用POST')

    body, err = _parse_json_body(request)
    if err:
        return err

    namespace = str(body.get('namespace') or '').strip()
    if not namespace:
        return _resp(code=40022, message='缺少namespace')

    days = body.get('days') or body.get('workdays')
    if not isinstance(days, list) or not days:
        return _resp(code=40023, message='days必须是含菜单的非空数组')

    user = _ensure_user(user_id)
    pref = UserPreference.objects.filter(user=user).first()
    slots_synced = 0

    with transaction.atomic():
        for day in days:
            if not isinstance(day, dict):
                continue
            date_raw = day.get('date') or day.get('dateKey')
            date_val, perr = _parse_date(date_raw)
            if perr:
                return _resp(code=40024, message=f'日期无效:{date_raw}')
            slots = day.get('slots') or {}
            if not isinstance(slots, dict):
                continue
            for slot_key, slot_body in slots.items():
                meal_slot = _normalize_slot_key(slot_key)
                if not meal_slot or not isinstance(slot_body, dict):
                    continue
                dishes = slot_body.get('dishes') or slot_body.get('menuItems') or []
                if not isinstance(dishes, list) or not dishes:
                    continue
                tab_uid = str(slot_body.get('tabUniqueId') or '')
                tgt = str(slot_body.get('targetTime') or '')
                tt = _parse_target_time(tgt) if tgt else None

                snapshot, _ = MenuSnapshot.objects.update_or_create(
                    namespace=namespace,
                    date=date_val,
                    meal_slot=meal_slot,
                    defaults={
                        'tab_unique_id': tab_uid,
                        'target_time': tt,
                        'source': 'meican',
                    },
                )
                MenuItem.objects.filter(snapshot=snapshot).delete()
                bulk = []
                for d in dishes:
                    if not isinstance(d, dict):
                        continue
                    dish_id = str(d.get('dishId') or d.get('id') or d.get('dish_id') or '').strip()
                    dish_name = str(d.get('dishName') or d.get('name') or '').strip()
                    if not dish_id or not dish_name:
                        continue
                    price_cent = d.get('priceCent') if d.get('priceCent') is not None else d.get('price_cent')
                    try:
                        price_cent = int(price_cent or 0)
                    except (TypeError, ValueError):
                        price_cent = 0
                    raw = dict(d)
                    raw.setdefault('tabUniqueId', tab_uid)
                    raw.setdefault('targetTime', tgt)
                    raw.setdefault('corpNamespace', str(d.get('corpNamespace') or namespace))
                    bulk.append(
                        MenuItem(
                            snapshot=snapshot,
                            dish_id=dish_id[:64],
                            dish_name=dish_name[:256],
                            restaurant_id=str(d.get('restaurantId') or d.get('restaurant_id') or '')[:64],
                            restaurant_name=str(d.get('restaurantName') or d.get('restaurant_name') or '')[:128],
                            price_cent=price_cent,
                            status=str(d.get('status') or 'available')[:16],
                            raw_json=raw,
                        )
                    )
                if not bulk:
                    continue
                MenuItem.objects.bulk_create(bulk)
                slots_synced += 1

                old_ids = list(
                    RecommendationBatch.objects.filter(
                        namespace=namespace, date=date_val, meal_slot=meal_slot
                    ).values_list('id', flat=True)
                )
                if old_ids:
                    RecommendationResult.objects.filter(user=user, batch_id__in=old_ids).delete()

                max_v = (
                    RecommendationBatch.objects.filter(
                        namespace=namespace, date=date_val, meal_slot=meal_slot
                    ).aggregate(mv=Max('version'))['mv']
                    or 0
                )
                batch = RecommendationBatch.objects.create(
                    date=date_val,
                    meal_slot=meal_slot,
                    namespace=namespace,
                    version=max_v + 1,
                    status=RecommendationBatchStatus.READY,
                )
                menu_qs = MenuItem.objects.filter(snapshot=snapshot)
                ranked = rank_menu_items_for_user(pref, menu_qs)
                id_by_dish = {m.dish_id: m for m in menu_qs}
                results = []
                rank_no = 0
                for row in ranked[:3]:
                    mi = id_by_dish.get(row['dish_id'])
                    if not mi:
                        continue
                    rank_no += 1
                    results.append(
                        RecommendationResult(
                            batch=batch,
                            user=user,
                            rank_no=rank_no,
                            menu_item=mi,
                            score=Decimal(str(row['score'])),
                            reason=str(row.get('reason') or '')[:256],
                        )
                    )
                if results:
                    RecommendationResult.objects.bulk_create(results)
                else:
                    batch.delete()

    return _resp(data={'userId': user.id, 'slotsSynced': slots_synced, 'namespace': namespace})


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
    with transaction.atomic():
        existed_by_idem = OrderRecord.objects.filter(idempotency_key=idempotency_key).first()
        if existed_by_idem:
            return _resp(data={'orderId': existed_by_idem.id, 'status': existed_by_idem.status, 'idempotent': True})

        if OrderRecord.objects.filter(user=user, date=date_val, meal_slot=meal_slot).exists():
            return _resp(code=40901, message='ORDER_ALREADY_EXISTS')

        order = OrderRecord.objects.create(
            user=user,
            date=date_val,
            meal_slot=meal_slot,
            menu_item=menu_item,
            source='MANUAL',
            status='CREATED',
            idempotency_key=idempotency_key,
            meican_order_unique_id='',
        )
    return _resp(data={'orderId': order.id, 'status': order.status})


def _internal_auth_ok(request):
    configured_token = str(getattr(settings, 'INTERNAL_JOB_TOKEN', '') or '')
    if not configured_token:
        return True
    return request.headers.get('X-Internal-Token', '') == configured_token


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

    trigger_type = 'MANUAL'
    force = bool(body.get('force'))
    if not force and not _within_auto_order_window(date_val, meal_slot):
        return _resp(code=40902, message='超过自动下单截止时间窗口')
    job, created = AutoOrderJob.objects.get_or_create(
        date=date_val,
        meal_slot=meal_slot,
        trigger_type=trigger_type,
        defaults={'status': JobStatus.PENDING},
    )
    if not created and not force:
        return _resp(data={'jobId': job.id, 'status': job.status, 'created': False})

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
    failed = 0
    for cfg in cfg_qs:
        slots = _split_meal_slots(cfg.meal_slots)
        if meal_slot not in slots:
            continue
        total += 1
        user = cfg.user

        if not cfg.default_corp_address_id:
            failed += 1
            AutoOrderJobItem.objects.update_or_create(
                job=job,
                user=user,
                defaults={
                    'status': JobItemStatus.SKIPPED,
                    'retry_count': 0,
                    'fail_code': 'NO_DEFAULT_ADDRESS',
                    'fail_message': '无默认企业地址',
                    'menu_item': None,
                },
            )
            continue

        if OrderRecord.objects.filter(user=user, date=date_val, meal_slot=meal_slot).exists():
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
                },
            )
            continue

        AutoOrderJobItem.objects.update_or_create(
            job=job,
            user=user,
            defaults={
                'menu_item': rec.menu_item,
                'status': JobItemStatus.PENDING,
                'retry_count': 0,
                'fail_code': '',
                'fail_message': '',
            },
        )

    job.total_count = total
    job.failed_count = failed
    job.success_count = 0
    job.status = JobStatus.RUNNING if total > 0 else JobStatus.SUCCESS
    if total == 0:
        job.finished_at = timezone.now()
    job.save(update_fields=['total_count', 'failed_count', 'success_count', 'status', 'finished_at'])
    return _resp(data={'jobId': job.id, 'status': job.status, 'created': created or force})


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


def _meican_oauth_error_response(exc: MeicanOAuthError):
    msg = str(exc)
    if msg == 'MEICAN_ACCOUNT_NOT_FOUND':
        return _resp(code=40403, message=msg)
    if msg in {'MEICAN_REFRESH_TOKEN_MISSING', 'MEICAN_CLIENT_NOT_CONFIGURED'}:
        return _resp(code=40020, message=msg)
    return _resp(code=50201, message=msg, data={'oauthHttpStatus': exc.status})


def put_user_meican_session(request, user_id):
    """
    小程序美餐登录成功后，将 access/refresh 同步到推荐服务，便于定时任务主动换票。
    不在响应中回传 token 明文。
    """
    if request.method != 'PUT':
        return _resp(code=40500, message='请求方式错误，请使用PUT')

    body, err = _parse_json_body(request)
    if err:
        return err

    access = str(body.get('accessToken') or body.get('access_token') or '').strip()
    refresh = str(body.get('refreshToken') or body.get('refresh_token') or '').strip()
    username = str(
        body.get('meicanUsername')
        or body.get('meican_username')
        or body.get('selectedAccountName')
        or body.get('phone')
        or 'meican_user',
    ).strip()
    if not access or not refresh:
        return _resp(code=40021, message='缺少accessToken或refreshToken')

    expires_in = body.get('expiresIn') if body.get('expiresIn') is not None else body.get('expires_in')
    try:
        expires_in = int(expires_in) if expires_in is not None else None
    except (TypeError, ValueError):
        expires_in = None

    email = str(body.get('meicanEmail') or body.get('meican_email') or '').strip()
    namespace = str(body.get('accountNamespace') or body.get('account_namespace') or '').strip()

    ttl = expires_in if isinstance(expires_in, int) and expires_in > 0 else int(
        getattr(settings, 'MEICAN_TOKEN_DEFAULT_TTL_SECONDS', 3600) or 3600
    )
    token_expire_at = timezone.now() + timedelta(seconds=ttl)

    user = _ensure_user(user_id)
    obj, _ = UserMeicanAccount.objects.update_or_create(
        user=user,
        defaults={
            'meican_username': username,
            'meican_email': email,
            'access_token': access,
            'refresh_token': refresh,
            'token_expire_at': token_expire_at,
            'account_namespace': namespace,
            'is_bound': 1,
        },
    )
    return _resp(data={'userId': user.id, 'meicanAccountId': obj.id, 'tokenExpireAt': token_expire_at.isoformat()})


def post_internal_meican_ensure_token(request, user_id):
    if request.method != 'POST':
        return _resp(code=40500, message='请求方式错误，请使用POST')
    if not _internal_auth_ok(request):
        return _resp(code=40101, message='内部鉴权失败')

    body, err = _parse_json_body(request)
    if err:
        body = {}

    force = bool((body or {}).get('force'))
    skew = (body or {}).get('skewSeconds')
    try:
        skew_seconds = int(skew) if skew is not None else None
    except (TypeError, ValueError):
        skew_seconds = None

    try:
        if force:
            account = refresh_user_meican_token_locked(int(user_id))
            refreshed = True
        else:
            account, refreshed = ensure_valid_access_token(int(user_id), skew_seconds=skew_seconds)
    except MeicanOAuthError as exc:
        return _meican_oauth_error_response(exc)

    exp = account.token_expire_at.isoformat() if account.token_expire_at else None
    return _resp(
        data={
            'userId': int(user_id),
            'refreshed': refreshed,
            'tokenExpireAt': exp,
        }
    )


def post_internal_meican_refresh_due_tokens(request):
    """
    定时任务调用：刷新「临近过期」的用户 token（默认取 settings 中的 skew 窗口）。
    """
    if request.method != 'POST':
        return _resp(code=40500, message='请求方式错误，请使用POST')
    if not _internal_auth_ok(request):
        return _resp(code=40101, message='内部鉴权失败')

    body, err = _parse_json_body(request)
    if err:
        body = {}

    within = (body or {}).get('withinSeconds')
    limit = (body or {}).get('limit', 50)
    try:
        within_seconds = int(within) if within is not None else None
    except (TypeError, ValueError):
        within_seconds = None
    try:
        limit_val = int(limit)
    except (TypeError, ValueError):
        limit_val = 50

    user_ids = iter_user_ids_due_for_refresh(within_seconds=within_seconds, limit=limit_val)
    items = []
    for uid in user_ids:
        try:
            account, refreshed = ensure_valid_access_token(uid)
            items.append({
                'userId': uid,
                'ok': True,
                'refreshed': refreshed,
                'tokenExpireAt': account.token_expire_at.isoformat() if account.token_expire_at else None,
            })
        except MeicanOAuthError as exc:
            items.append({'userId': uid, 'ok': False, 'error': str(exc)})

    return _resp(data={'count': len(items), 'items': items})
