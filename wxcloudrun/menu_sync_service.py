"""
将小程序上报的「周菜单」JSON 写入 menu_snapshot / menu_item。
"""
from datetime import datetime

from django.db import transaction

from wxcloudrun.models import MealSlot, MenuItem, MenuSnapshot


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


def _parse_date(value):
    if not value:
        return None, '缺少date'
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date(), None
    except ValueError:
        return None, 'date格式错误'


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


def _dish_row_to_menu_item_fields(d):
    if not isinstance(d, dict):
        return None
    dish_id = str(d.get('dishId') or d.get('id') or d.get('dish_id') or '').strip()
    dish_name = str(d.get('dishName') or d.get('name') or '').strip()
    if not dish_id or not dish_name:
        return None
    price_cent = d.get('priceInCent')
    if price_cent is None:
        price_cent = d.get('price_cent')
    if price_cent is None:
        price_cent = d.get('originalPriceInCent')
    try:
        price_cent = int(price_cent or 0)
    except (TypeError, ValueError):
        price_cent = 0
    if not price_cent and d.get('priceString'):
        try:
            price_cent = int(float(str(d.get('priceString'))) * 100)
        except (TypeError, ValueError):
            price_cent = 0
    rest = d.get('restaurant') if isinstance(d.get('restaurant'), dict) else {}
    rid = str(d.get('restaurantId') or d.get('restaurant_id') or rest.get('uniqueId') or rest.get('id') or '')[:64]
    rname = str(d.get('restaurantName') or d.get('restaurant_name') or rest.get('name') or '')[:128]
    status = str(d.get('status') or 'available')[:16]
    if rest.get('available') is False:
        status = 'unavailable'
    raw = dict(d)
    return {
        'dish_id': dish_id[:64],
        'dish_name': dish_name[:256],
        'restaurant_id': rid,
        'restaurant_name': rname,
        'price_cent': price_cent,
        'status': status,
        'raw_json': raw,
    }


def normalize_days_payload(body):
    """从请求体解析出 days 列表；无效则 (None, error_message)。"""
    days = body.get('days') or body.get('workdays')
    if isinstance(days, list) and days:
        return days, None
    d0 = body.get('date') or body.get('dateKey')
    slot0 = body.get('mealSlot') or body.get('meal_slot')
    dish_list = body.get('dishes') or body.get('menuItems') or body.get('othersRegularDishList')
    if d0 and slot0 and isinstance(dish_list, list) and dish_list:
        return [{'date': d0, 'slots': {str(slot0).upper(): {'dishes': dish_list}}}], None
    return None, 'days必须是非空数组，或使用 date+mealSlot+dishes 单日简写'


def sync_menu_days(namespace: str, days: list):
    """
    写入菜单快照。返回 dict: slots_synced, errors(list)。
    """
    ns = (namespace or '').strip()
    slots_synced = 0
    errors = []

    for day in days:
        if not isinstance(day, dict):
            continue
        date_raw = day.get('date') or day.get('dateKey')
        date_val, perr = _parse_date(date_raw)
        if perr:
            return {'slots_synced': slots_synced, 'errors': errors + [{'error': perr, 'date': date_raw}], 'fatal': True}
        slots = day.get('slots') or {}
        if not isinstance(slots, dict):
            continue
        for slot_key, slot_body in slots.items():
            meal_slot = _normalize_slot_key(slot_key)
            if not meal_slot or not isinstance(slot_body, dict):
                continue
            dishes = slot_body.get('dishes') or slot_body.get('menuItems') or slot_body.get('othersRegularDishList') or []
            if not isinstance(dishes, list) or not dishes:
                errors.append({'date': str(date_val), 'mealSlot': meal_slot, 'error': '无菜品列表'})
                continue
            tab_uid = str(slot_body.get('tabUniqueId') or '')[:64]
            tgt = str(slot_body.get('targetTime') or '')
            tt = _parse_target_time(tgt) if tgt else None

            with transaction.atomic():
                snapshot, _ = MenuSnapshot.objects.update_or_create(
                    namespace=ns,
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
                    row = _dish_row_to_menu_item_fields(d)
                    if not row:
                        continue
                    raw = row['raw_json']
                    raw.setdefault('tabUniqueId', tab_uid)
                    raw.setdefault('targetTime', tgt)
                    raw.setdefault('corpNamespace', str(raw.get('corpNamespace') or ns))
                    bulk.append(
                        MenuItem(
                            snapshot=snapshot,
                            dish_id=row['dish_id'],
                            dish_name=row['dish_name'],
                            restaurant_id=row['restaurant_id'],
                            restaurant_name=row['restaurant_name'],
                            price_cent=row['price_cent'],
                            status=row['status'],
                            raw_json=raw,
                        )
                    )
                if bulk:
                    MenuItem.objects.bulk_create(bulk)
                    slots_synced += 1
                else:
                    errors.append({'date': str(date_val), 'mealSlot': meal_slot, 'error': '无有效菜品字段'})

    return {'slots_synced': slots_synced, 'errors': errors, 'fatal': False}
