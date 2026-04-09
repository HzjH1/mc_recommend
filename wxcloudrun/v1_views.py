import json
import uuid
from datetime import datetime, time

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
        result[slot] = slot_data

    return _resp(data=result)


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
