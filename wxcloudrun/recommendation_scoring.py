"""
根据 user_preference 对 menu_item 打分，供推荐落库与手动刷新命令复用。
"""
import re
from decimal import Decimal

from wxcloudrun.models import MenuItem, UserPreference


def _contains_any(text, keywords):
    return any(keyword in text for keyword in keywords)


def pref_dict_from_user_preference(pref: UserPreference | None):
    if not pref:
        return {
            'spicy': None,
            'halal': None,
            'losing_fat': None,
            'prefer_noodle': None,
            'prefer_rice': None,
            'extra': '',
            'price_min': None,
            'price_max': None,
        }
    staple = (pref.staple or '').lower()
    prefer_noodle = 'noodle' in staple or 'mian' in staple or 'fen' in staple or '面' in staple
    prefer_rice = 'rice' in staple or '饭' in staple
    if not prefer_noodle and not prefer_rice and staple in {'rice', 'noodle'}:
        prefer_rice = staple == 'rice'
        prefer_noodle = staple == 'noodle'
    return {
        'spicy': bool(pref.prefers_spicy),
        'halal': bool(pref.is_halal),
        'losing_fat': bool(pref.is_cutting),
        'prefer_noodle': prefer_noodle if (prefer_noodle or prefer_rice) else None,
        'prefer_rice': prefer_rice if (prefer_noodle or prefer_rice) else None,
        'extra': str(pref.taboo or ''),
        'price_min': pref.price_min,
        'price_max': pref.price_max,
    }


def score_menu_item(pref: dict, item: MenuItem):
    name = str(item.dish_name or '')
    score = 0.0
    reasons = []
    extra = pref.get('extra') or ''
    avoid_onion = bool(re.search(r'不能吃葱|不吃葱', extra))
    like_coriander = '香菜' in extra

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

    pmn, pmx = pref.get('price_min'), pref.get('price_max')
    if pmn is not None and item.price_cent < float(pmn) * 100:
        score -= 1
        reasons.append('低于预算下限')
    if pmx is not None and item.price_cent > float(pmx) * 100:
        score -= 1
        reasons.append('高于预算上限')

    reason = '；'.join(reasons) if reasons else '基础推荐'
    return score, reason


def rank_top_menu_items(pref: dict, queryset, top_n=3):
    rows = []
    for item in queryset:
        if (item.status or '').lower() in {'sold_out', 'unavailable'}:
            continue
        s, r = score_menu_item(pref, item)
        rows.append((s, item, r))
    rows.sort(key=lambda x: x[0], reverse=True)
    out = []
    for s, item, r in rows[:top_n]:
        out.append({'menu_item': item, 'score': Decimal(str(round(s, 4))), 'reason': r[:256]})
    return out
