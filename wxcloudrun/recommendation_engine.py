"""
基于用户偏好对菜单项打分（与 wxcloudrun.views._score_menu_items 规则一致），用于 V1 入库推荐。
"""
import re
from typing import Any, Dict, List, Optional

from wxcloudrun.models import UserPreference


def _contains_any(text: str, keywords: List[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def user_preference_to_dict(pref: Optional[UserPreference]) -> Dict[str, Any]:
    if not pref:
        return {
            'spicy': None,
            'halal': False,
            'losing_fat': False,
            'prefer_noodle': False,
            'prefer_rice': False,
            'extra': '',
        }
    staple = (pref.staple or '').lower()
    return {
        'spicy': bool(pref.prefers_spicy),
        'halal': bool(pref.is_halal),
        'losing_fat': bool(pref.is_cutting),
        'prefer_noodle': staple == 'noodle',
        'prefer_rice': staple != 'noodle',
        'extra': str(pref.taboo or ''),
    }


def score_menu_items(pref: Dict[str, Any], menu_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    scored = []
    extra = pref.get('extra') or ''
    avoid_onion = bool(re.search(r'不能吃葱|不吃葱', extra))
    like_coriander = '香菜' in extra

    for item in menu_items:
        name = str(item.get('name', '') or item.get('dish_name', ''))
        if not name:
            continue
        score = 0
        reasons = []

        if pref.get('spicy') is False and _contains_any(name, ['辣', '香辣', '辣子', '微辣', '麻辣']):
            score -= 3
            reasons.append('避开辣味偏好')
        elif pref.get('spicy') is True and _contains_any(name, ['辣', '香辣', '辣子', '微辣', '麻辣']):
            score += 1
            reasons.append('匹配吃辣偏好')

        if pref.get('halal') is True and _contains_any(name, ['猪', '排骨', '猪手', '里脊']):
            score -= 4
            reasons.append('清真偏好下规避猪肉类')

        if pref.get('losing_fat') is True:
            if _contains_any(name, ['汤', '青菜', '瘦肉', '牛肉']):
                score += 2
                reasons.append('减脂期优先清淡高蛋白')
            if _contains_any(name, ['红烧', '排骨', '猪手', '里脊', '糖醋']):
                score -= 2
                reasons.append('减脂期降低高油高糖菜品权重')

        if pref.get('prefer_noodle') is True and _contains_any(name, ['粉', '面', '米粉']):
            score += 2
            reasons.append('主食偏好粉面')
        if pref.get('prefer_rice') is True and '饭' in name:
            score += 2
            reasons.append('主食偏好米饭')

        if avoid_onion and '葱' in name:
            score -= 5
            reasons.append('备注不吃葱')
        if like_coriander and '香菜' in name:
            score += 2
            reasons.append('备注喜欢香菜')

        scored.append({
            'dish_id': str(item.get('id') or item.get('dish_id') or ''),
            'name': name,
            'score': score,
            'reason': '；'.join(reasons) if reasons else '基础推荐',
        })

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored


def menuitem_queryset_to_score_inputs(items) -> List[Dict[str, Any]]:
    out = []
    for it in items:
        out.append({
            'id': it.dish_id,
            'dish_id': it.dish_id,
            'name': it.dish_name,
            'dish_name': it.dish_name,
            'restaurant': {'name': it.restaurant_name or ''},
        })
    return out


def rank_menu_items_for_user(pref: Optional[UserPreference], menu_items_qs) -> List[Dict[str, Any]]:
    inputs = menuitem_queryset_to_score_inputs(menu_items_qs)
    p = user_preference_to_dict(pref)
    ranked = score_menu_items(p, inputs)
    return ranked
