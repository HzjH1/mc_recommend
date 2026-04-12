"""
推荐结果落库：按 menu_snapshot / menu_item 为单用户生成 recommendation_batch + recommendation_result。
"""
from datetime import date, timedelta
from typing import Optional, Set

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
from wxcloudrun.meican_menu_snapshot import sync_meican_menu_snapshot_for_user_dates
from wxcloudrun.recommendation_scoring import pref_dict_from_user_preference, rank_top_menu_items


def _recommendation_refresh_hint(**extra):
    """
    refresh_user_recommendations / refresh_recommendations_for_user_slot 排查用：
    本链路为规则打分，不调用 OpenAI；与 views.recommend_dishes 的 LLM 推荐分离。
    """
    h = {
        'usesAiLlm': False,
        'scoring': '规则引擎 wxcloudrun.recommendation_scoring（score_menu_item / rank_top_menu_items）',
        'llmNote': '大模型仅用于 wxcloudrun.views.recommend_dishes（_call_ai_recommendation，依赖 OPENAI_*）；不落库到 recommendation_batch',
    }
    h.update({k: v for k, v in extra.items() if v is not None})
    return h


def refresh_recommendations_for_user_slot(
    user: UserAccount,
    namespace: str,
    day: date,
    meal_slot: str,
    *,
    freeze: bool = False,
    top_n: int = 3,
    sync_menu_if_missing: bool = True,
    meican_menu_sync_attempted_dates: Optional[Set[date]] = None,
):
    """
    为单个用户、单日、单餐期生成一批推荐。
    当缺少该餐期的 menu_snapshot / menu_item 且 sync_menu_if_missing 为 True 时，
    会按用户美餐 session（须与 namespace 一致）调用美餐 Forward 拉单日菜单并 sync_menu_days。
    meican_menu_sync_attempted_dates 若传入，则每个自然日只尝试拉取一次（周任务内午/晚共用）。
    成功返回 dict: {ok, batch_id, version, status, meal_slot, date, hint}
    跳过返回 dict: {ok: False, skip, hint}；hint 说明打分不走大模型，便于与 recommend_dishes 区分。
    """
    ns = (namespace or '').strip()
    if not ns:
        return {
            'ok': False,
            'skip': 'namespace 为空',
            'hint': _recommendation_refresh_hint(detail='传入 --namespace 须与 menu_snapshot / 美餐 account_namespace 一致'),
        }

    pref = UserPreference.objects.filter(user=user).first()
    pref_dict = pref_dict_from_user_preference(pref)

    snap = MenuSnapshot.objects.filter(namespace=ns, date=day, meal_slot=meal_slot).first()
    has_items = bool(snap and MenuItem.objects.filter(snapshot=snap).exists())
    if (not snap or not has_items) and sync_menu_if_missing:
        should_try = meican_menu_sync_attempted_dates is None or day not in meican_menu_sync_attempted_dates
        if should_try:
            sync_meican_menu_snapshot_for_user_dates(user, ns, [day], language='zh-CN')
            if meican_menu_sync_attempted_dates is not None:
                meican_menu_sync_attempted_dates.add(day)
        snap = MenuSnapshot.objects.filter(namespace=ns, date=day, meal_slot=meal_slot).first()
        has_items = bool(snap and MenuItem.objects.filter(snapshot=snap).exists())

    if not snap:
        return {
            'ok': False,
            'skip': f'无 menu_snapshot {day} {meal_slot}',
            'hint': _recommendation_refresh_hint(
                detail='库中无该日该餐期快照',
                nextSteps='可先 meican 同步 / week-sync / import_menu_week_json；加 --no-meican-sync 则不会自动拉美餐',
            ),
        }
    if not has_items:
        return {
            'ok': False,
            'skip': f'snapshot {snap.id} 无 menu_item',
            'hint': _recommendation_refresh_hint(
                detail='快照存在但菜品表为空',
                snapshotId=snap.id,
                nextSteps='重新同步该日菜单或检查 sync_menu_days 是否写入失败',
            ),
        }

    menu_qs = MenuItem.objects.filter(snapshot=snap)
    ranked = rank_top_menu_items(pref_dict, menu_qs, top_n=max(1, int(top_n)))
    if not ranked:
        return {
            'ok': False,
            'skip': '无可用菜品参与打分',
            'hint': _recommendation_refresh_hint(
                detail='候选 menu_item 在规则里全部被过滤（常见：均为 sold_out/unavailable）',
                snapshotId=snap.id,
                candidateCount=menu_qs.count(),
            ),
        }

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
        'hint': _recommendation_refresh_hint(
            detail='已写入 recommendation_batch / recommendation_result',
            snapshotId=snap.id,
            rankedCount=len(ranked),
            candidateCount=menu_qs.count(),
        ),
    }


def resolve_week_start_monday(today: Optional[date], week_start: Optional[date]) -> date:
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
    week_start: Optional[date] = None,
    freeze: bool = False,
    top_n: int = 3,
    workdays: int = 5,
    user_id: Optional[int] = None,
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

        meican_sync_dates: Set[date] = set()
        for i in range(workdays):
            d = monday + timedelta(days=i)
            for meal_slot in (MealSlot.LUNCH, MealSlot.DINNER):
                out = refresh_recommendations_for_user_slot(
                    user,
                    ns,
                    d,
                    meal_slot,
                    freeze=freeze,
                    top_n=top_n,
                    meican_menu_sync_attempted_dates=meican_sync_dates,
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
