"""
推荐结果落库：按 menu_snapshot / menu_item 为单用户生成 recommendation_batch + recommendation_result。
"""
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from wxcloudrun.models import (
    MealSlot,
    MenuItem,
    MenuSnapshot,
    RecommendationBatch,
    RecommendationBatchStatus,
    RecommendationResult,
    UserAccount,
    UserMeicanAccount,
    UserPreference,
)
from wxcloudrun.recommendation_scoring import pref_dict_from_user_preference, rank_top_menu_items


def refresh_recommendations_for_user_slot(
    user: UserAccount,
    namespace: str,
    day: date,
    meal_slot: str,
    *,
    freeze: bool = False,
    top_n: int = 3,
):
    """
    为单个用户、单日、单餐期生成一批推荐。
    成功返回 dict: {ok, batch_id, version, status, meal_slot, date}
    跳过返回 dict: {ok: False, skip: reason}
    """
    ns = (namespace or '').strip()
    if not ns:
        return {'ok': False, 'skip': 'namespace 为空'}

    pref = UserPreference.objects.filter(user=user).first()
    pref_dict = pref_dict_from_user_preference(pref)

    snap = MenuSnapshot.objects.filter(namespace=ns, date=day, meal_slot=meal_slot).first()
    if not snap:
        return {'ok': False, 'skip': f'无 menu_snapshot {day} {meal_slot}'}

    menu_qs = MenuItem.objects.filter(snapshot=snap)
    if not menu_qs.exists():
        return {'ok': False, 'skip': f'snapshot {snap.id} 无 menu_item'}

    ranked = rank_top_menu_items(pref_dict, menu_qs, top_n=max(1, int(top_n)))
    if not ranked:
        return {'ok': False, 'skip': '无可用菜品参与打分'}

    status = RecommendationBatchStatus.FROZEN if freeze else RecommendationBatchStatus.READY

    with transaction.atomic():
        old_ids = list(
            RecommendationBatch.objects.filter(
                namespace=ns,
                date=day,
                meal_slot=meal_slot,
            ).values_list('id', flat=True)
        )
        if old_ids:
            RecommendationResult.objects.filter(user=user, batch_id__in=old_ids).delete()

        max_v = (
            RecommendationBatch.objects.filter(
                namespace=ns,
                date=day,
                meal_slot=meal_slot,
            ).aggregate(mv=Max('version'))['mv']
            or 0
        )
        batch = RecommendationBatch.objects.create(
            date=day,
            meal_slot=meal_slot,
            namespace=ns,
            version=max_v + 1,
            status=status,
        )
        bulk = [
            RecommendationResult(
                batch=batch,
                user=user,
                rank_no=rank_no,
                menu_item=row['menu_item'],
                score=row['score'],
                reason=row['reason'],
            )
            for rank_no, row in enumerate(ranked, start=1)
        ]
        RecommendationResult.objects.bulk_create(bulk)

    return {
        'ok': True,
        'batch_id': batch.id,
        'version': batch.version,
        'status': batch.status,
        'meal_slot': meal_slot,
        'date': str(day),
    }


def resolve_week_start_monday(today: date | None, week_start: date | None) -> date:
    """
    未传 week_start 时：
    - 若今天是周日：视为「每周日任务」，生成「下周一」起的自然周工作日（由调用方取 5 天）。
    - 否则：取「本周一」（date.weekday(): 周一=0, 周日=6）。
    """
    today = today or timezone.now().date()
    if week_start:
        return week_start
    if today.weekday() == 6:
        return today + timedelta(days=1)
    return today - timedelta(days=today.weekday())


def run_weekly_recommendation_job(
    *,
    week_start: date | None = None,
    freeze: bool = False,
    top_n: int = 3,
    workdays: int = 5,
    user_id: int | None = None,
):
    """
    每周推荐任务：对每个已绑定 namespace 且有偏好的用户，
    对 week_start 起的连续 workdays 个工作日、午/晚各生成推荐。
    """
    monday = resolve_week_start_monday(None, week_start)
    accounts = UserMeicanAccount.objects.exclude(account_namespace='').select_related('user')
    if user_id is not None:
        accounts = accounts.filter(user_id=user_id)

    summary = {
        'weekStartMonday': str(monday),
        'workdays': workdays,
        'freeze': freeze,
        'created': [],
        'skipped': [],
    }

    for acc in accounts.iterator():
        user = acc.user
        ns = (acc.account_namespace or '').strip()
        if not ns:
            summary['skipped'].append({'userId': user.id, 'reason': 'namespace 为空'})
            continue
        if not UserPreference.objects.filter(user=user).exists():
            summary['skipped'].append({'userId': user.id, 'reason': '无 user_preference'})
            continue

        for i in range(workdays):
            d = monday + timedelta(days=i)
            for meal_slot in (MealSlot.LUNCH, MealSlot.DINNER):
                out = refresh_recommendations_for_user_slot(
                    user, ns, d, meal_slot, freeze=freeze, top_n=top_n
                )
                if out.get('ok'):
                    summary['created'].append({'userId': user.id, **out})
                else:
                    summary['skipped'].append({
                        'userId': user.id,
                        'date': str(d),
                        'mealSlot': meal_slot,
                        'reason': out.get('skip', 'unknown'),
                    })

    return summary
