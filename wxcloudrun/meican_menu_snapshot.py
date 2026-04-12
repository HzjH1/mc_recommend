# -*- coding: utf-8 -*-
"""
与 mc1 小程序 `services/meican/api.js` 一致：Forward calendarItems → restaurants/list +
recommendations/dishes，组装 days 后调用 sync_menu_days 落库。
依赖环境变量（与小程序 config 同源）：MEICAN_FORWARD_CLIENT_ID / MEICAN_FORWARD_CLIENT_SECRET。
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone

from wxcloudrun.menu_sync_service import sync_menu_days
from wxcloudrun.models import UserAccount, UserMeicanAccount

_MEICAN_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)
_DEVICE_ID = str(uuid.uuid4())


def _forward_base() -> str:
    return (getattr(settings, 'MEICAN_FORWARD_BASE_URL', None) or 'https://www.meican.com/forward').rstrip('/')


def _forward_credentials() -> Tuple[str, str]:
    cid = (getattr(settings, 'MEICAN_FORWARD_CLIENT_ID', None) or '').strip()
    csec = (getattr(settings, 'MEICAN_FORWARD_CLIENT_SECRET', None) or '').strip()
    return cid, csec


def _forward_headers() -> Dict[str, str]:
    cid, csec = _forward_credentials()
    app = (getattr(settings, 'MEICAN_GRAPHQL_APP', None) or 'meican/web-pc (prod;4.90.1;sys;main)').strip()
    return {
        'clientID': cid,
        'clientSecret': csec,
        'x-mc-app': app,
        'x-mc-device': _DEVICE_ID,
        'x-mc-page': '/auth/verification?stamp=AC',
        'Referer': 'https://www.meican.com/auth/verification',
        'accept-language': 'zh',
        'User-Agent': _MEICAN_UA,
    }


def _url(path: str, query: Dict[str, Any]) -> str:
    cid, csec = _forward_credentials()
    q = {'client_id': cid, 'client_secret': csec}
    for k, v in query.items():
        if v is None:
            continue
        s = str(v).strip()
        if s == '':
            continue
        q[k] = v
    return f'{_forward_base()}{path}?{urlencode(q)}'


def _json_request(
    acc: UserMeicanAccount,
    method: str,
    path: str,
    query: Dict[str, Any],
    *,
    allow_refresh: bool = True,
) -> Any:
    url = _url(path, query)
    headers = {
        **_forward_headers(),
        'Authorization': f'Bearer {(acc.access_token or "").strip()}',
    }
    req = Request(url, method=method.upper(), headers=headers)
    try:
        with urlopen(req, timeout=45) as resp:  # nosec B310 — 固定美餐域名
            raw = resp.read().decode('utf-8', errors='replace')
    except HTTPError as e:
        raw = e.read().decode('utf-8', errors='replace') if e.fp else ''
        if e.code == 401 and allow_refresh and (acc.refresh_token or '').strip():
            if _refresh_meican_token(acc):
                acc.refresh_from_db(fields=['access_token', 'refresh_token', 'token_expire_at'])
                return _json_request(acc, method, path, query, allow_refresh=False)
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {'_http_status': e.code, '_raw': raw[:800]}
    except URLError:
        return {}
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {'_parse_error': True, '_raw': raw[:800]}


def _refresh_meican_token(acc: UserMeicanAccount) -> bool:
    cid, csec = _forward_credentials()
    if not cid or not csec:
        return False
    token_url = f'{_forward_base()}/api/v2.1/oauth/token?{urlencode({"client_id": cid, "client_secret": csec})}'
    body = urlencode({'grant_type': 'refresh_token', 'refresh_token': (acc.refresh_token or '').strip()})
    req = Request(
        token_url,
        data=body.encode('utf-8'),
        method='POST',
        headers={
            'clientID': cid,
            'clientSecret': csec,
            'Referer': 'https://www.meican.com/',
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': _MEICAN_UA,
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:
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


def _ensure_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _pick_first(source: Any, paths: List[str], default: Any = None) -> Any:
    if not isinstance(source, dict):
        return default
    for p in paths:
        cur: Any = source
        ok = True
        for part in p.split('.'):
            if not isinstance(cur, dict):
                ok = False
                break
            cur = cur.get(part)
        if ok and cur is not None and f'{cur}'.strip() != '':
            return cur
    return default


def _collect_matching(obj: Any, pred, out: List[Any], seen: Set[int]) -> None:
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


def _infer_meal_bucket(item: Dict[str, Any]) -> str:
    src = (
        f'{_pick_first(item, ["tabName", "name", "title", "mealType", "categoryName"], "")}'
        f'{_pick_first(item, ["targetTime", "startTime"], "")}'
    ).lower()
    if '晚' in src or 'dinner' in src or 'night' in src:
        return 'afternoon'
    return 'morning'


def _normalize_forward_target_time(item: Dict[str, Any], date_key: str, bucket: str) -> str:
    raw = _pick_first(item, ['targetTime', 'startTime'], None)
    if raw is not None and f'{raw}'.strip() != '':
        try:
            n = float(raw)
            if n == n and n > 1e11:
                return datetime.fromtimestamp(n / 1000.0).strftime('%Y-%m-%d %H:%M')
        except (TypeError, ValueError):
            pass
        s = str(raw).strip()
        if s:
            return s
    base = str(date_key).strip() if date_key else datetime.now().strftime('%Y-%m-%d')
    return f'{base} 17:00' if bucket == 'afternoon' else f'{base} 10:00'


def _extract_calendar_entries(payload: Any) -> List[Dict[str, Any]]:
    def pred(item: Dict[str, Any]) -> bool:
        if not isinstance(item, dict):
            return False
        has_target = item.get('targetTime') is not None and f'{item.get("targetTime")}'.strip() != ''
        if not has_target:
            return False
        tab_id = _pick_first(item, ['tabUniqueId', 'tabUUID', 'uniqueId', 'userTab.uniqueId'], '')
        return bool(tab_id)

    candidates: List[Any] = []
    _collect_matching(payload, pred, candidates, set())
    out = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        dk = _pick_first(item, ['operativeDate', 'date', 'targetDate'], datetime.now().strftime('%Y-%m-%d'))
        bucket = _infer_meal_bucket(item)
        tab_uid = _pick_first(item, ['tabUniqueId', 'tabUUID', 'uniqueId', 'userTab.uniqueId'], '')
        target_time = _normalize_forward_target_time(item, str(dk), bucket)
        out.append(
            {
                'dateKey': str(dk)[:10],
                'tabUniqueId': str(tab_uid),
                'tabName': _pick_first(item, ['tabName', 'name', 'title'], ''),
                'targetTime': target_time,
                'bucket': bucket,
            }
        )
    return out


def _format_price(value: Any) -> str:
    if value is None or value == '':
        return ''
    try:
        number_value = float(value)
    except (TypeError, ValueError):
        return f'{value}'
    if number_value != number_value:
        return ''
    if number_value > 1000:
        return f'{number_value / 100:.2f}'
    return f'{number_value:.2f}'


def _parse_price_cent(value: Any) -> int:
    if value is None or value == '':
        return 0
    try:
        number_value = float(value)
    except (TypeError, ValueError):
        return 0
    if number_value != number_value:
        return 0
    return int(round(number_value)) if number_value > 1000 else int(round(number_value * 100))


def _extract_restaurant_menus(restaurant: Dict[str, Any]) -> List[Dict[str, Any]]:
    dish_groups = [
        restaurant.get('dishes'),
        restaurant.get('dishList'),
        restaurant.get('menus'),
        restaurant.get('menuList'),
        restaurant.get('products'),
    ]
    dishes: List[Any] = []
    for g in dish_groups:
        dishes.extend(_ensure_list(g))
    rows = []
    for dish in dishes:
        if not isinstance(dish, dict):
            continue
        name = _pick_first(dish, ['name', 'dishName', 'title'], '')
        if not name:
            continue
        rows.append(
            {
                'id': _pick_first(dish, ['id', 'dishId', 'uniqueId', 'revisionId'], ''),
                'name': name,
                'price': _format_price(_pick_first(dish, ['price', 'priceInCent', 'priceCent'], '')),
                'status': _pick_first(dish, ['status', 'sellStatus'], 'available'),
            }
        )
    return rows


def _extract_restaurants(payload: Any) -> List[Dict[str, Any]]:
    def pred(item: Dict[str, Any]) -> bool:
        if not isinstance(item, dict):
            return False
        rid = item.get('restaurantId') or item.get('id') or item.get('uniqueId')
        name = item.get('name') or item.get('restaurantName')
        return bool(rid and name)

    found: List[Any] = []
    _collect_matching(payload, pred, found, set())
    restaurants = []
    for restaurant in found:
        if not isinstance(restaurant, dict):
            continue
        restaurants.append(
            {
                'id': _pick_first(restaurant, ['restaurantId', 'id', 'uniqueId'], ''),
                'name': _pick_first(restaurant, ['restaurantName', 'name', 'title'], ''),
                'distance': _pick_first(restaurant, ['distance', 'distanceInMeter'], ''),
                'status': _pick_first(restaurant, ['status', 'sellStatus'], 'available'),
                'menus': _extract_restaurant_menus(restaurant),
            }
        )
    return restaurants


def _extract_recommended_dishes(payload: Any) -> List[Dict[str, Any]]:
    def pred(item: Dict[str, Any]) -> bool:
        if not isinstance(item, dict):
            return False
        did = item.get('dishId') or item.get('id') or item.get('revisionId')
        name = item.get('name') or item.get('dishName')
        return bool(did and name)

    found: List[Any] = []
    _collect_matching(payload, pred, found, set())
    dishes = []
    for dish in found:
        if not isinstance(dish, dict):
            continue
        dishes.append(
            {
                'id': _pick_first(dish, ['dishId', 'id', 'revisionId'], ''),
                'name': _pick_first(dish, ['dishName', 'name', 'title'], ''),
                'price': _format_price(_pick_first(dish, ['price', 'priceInCent', 'priceCent'], '')),
                'status': _pick_first(dish, ['status', 'sellStatus'], 'available'),
            }
        )
    return dishes


def _build_sync_dishes_from_section(section: Dict[str, Any]) -> List[Dict[str, Any]]:
    restaurant_menus: List[Dict[str, Any]] = []
    for restaurant in _ensure_list(section.get('restaurants')):
        if not isinstance(restaurant, dict):
            continue
        r_status = restaurant.get('status') or 'available'
        rid = str(restaurant.get('id') or '').strip()
        rname = str(restaurant.get('name') or '').strip()
        for menu in _ensure_list(restaurant.get('menus')):
            if not isinstance(menu, dict):
                continue
            dish_id = str(menu.get('id') or '').strip()
            dish_name = menu.get('name')
            if not dish_id or not dish_name:
                continue
            restaurant_menus.append(
                {
                    'dishId': dish_id,
                    'dishName': dish_name,
                    'priceInCent': _parse_price_cent(menu.get('price')),
                    'status': menu.get('status') or r_status or 'available',
                    'restaurantId': rid,
                    'restaurantName': rname,
                }
            )
    if restaurant_menus:
        return restaurant_menus
    out = []
    for dish in _ensure_list(section.get('recommendedDishes')):
        if not isinstance(dish, dict):
            continue
        dish_id = str(dish.get('id') or '').strip()
        dish_name = dish.get('name')
        if not dish_id or not dish_name:
            continue
        out.append(
            {
                'dishId': dish_id,
                'dishName': dish_name,
                'priceInCent': _parse_price_cent(dish.get('price')),
                'status': dish.get('status') or 'available',
                'restaurantId': '',
                'restaurantName': '',
            }
        )
    return out


def _fetch_restaurants_by_tab(acc: UserMeicanAccount, tab_unique_id: str, target_time: str) -> List[Dict[str, Any]]:
    tt = str(target_time or '').strip()
    if not tab_unique_id or not tt:
        return []
    payload = _json_request(
        acc,
        'GET',
        '/api/v2.1/restaurants/list',
        {'tabUniqueId': tab_unique_id, 'targetTime': tt},
    )
    return _extract_restaurants(payload)


def _fetch_recommended_dishes_by_tab(acc: UserMeicanAccount, tab_unique_id: str, target_time: str) -> List[Dict[str, Any]]:
    tt = str(target_time or '').strip()
    if not tab_unique_id or not tt:
        return []
    payload = _json_request(
        acc,
        'GET',
        '/api/v2.1/recommendations/dishes',
        {'tabUniqueId': tab_unique_id, 'targetTime': tt},
    )
    return _extract_recommended_dishes(payload)


def _fetch_date_restaurant_menus(acc: UserMeicanAccount, date_key: str, language: str = 'zh-CN') -> Dict[str, Any]:
    cal = _json_request(
        acc,
        'GET',
        '/api/v2.1/calendarItems/list',
        {'withOrderDetail': 'false', 'beginDate': date_key, 'endDate': date_key},
    )
    entries = _extract_calendar_entries(cal)
    unique: List[Dict[str, Any]] = []
    tab_map: Set[str] = set()
    for entry in entries:
        key = f'{entry.get("tabUniqueId")}-{entry.get("targetTime")}'
        if key not in tab_map:
            tab_map.add(key)
            unique.append(entry)

    sections = []
    for entry in unique:
        tab_uid = str(entry.get('tabUniqueId') or '')
        tgt = str(entry.get('targetTime') or '')
        restaurants = _fetch_restaurants_by_tab(acc, tab_uid, tgt)
        recommended = _fetch_recommended_dishes_by_tab(acc, tab_uid, tgt)
        bucket = entry.get('bucket') or 'morning'
        title = str(entry.get('tabName') or '').strip()
        if not title:
            title = 'Later meal' if bucket == 'afternoon' and language == 'en-US' else (
                'Earlier meal' if bucket == 'morning' and language == 'en-US' else (
                    '下午餐期' if bucket == 'afternoon' else '上午餐期'
                )
            )
        sections.append(
            {
                'key': bucket,
                'tabUniqueId': tab_uid,
                'targetTime': tgt,
                'title': title,
                'restaurants': restaurants,
                'recommendedDishes': recommended,
            }
        )
    return {'dateKey': date_key, 'mealSections': sections}


def _meal_sections_to_day_payload(date_key: str, meal_sections: List[Dict[str, Any]]) -> Dict[str, Any]:
    slots: Dict[str, Any] = {}
    for section in meal_sections:
        if not isinstance(section, dict):
            continue
        dishes = _build_sync_dishes_from_section(section)
        if not dishes:
            continue
        slot_key = 'DINNER' if section.get('key') == 'afternoon' else 'LUNCH'
        slots[slot_key] = {
            'tabUniqueId': section.get('tabUniqueId') or '',
            'targetTime': section.get('targetTime') or '',
            'dishes': dishes,
        }
    return {'date': date_key, 'slots': slots}


def meican_forward_configured() -> bool:
    cid, csec = _forward_credentials()
    return bool(cid and csec)


def sync_meican_menu_snapshot_for_user_dates(
    user: UserAccount,
    namespace: str,
    dates: List[date],
    *,
    language: str = 'zh-CN',
) -> Dict[str, Any]:
    """
    用该用户已绑定的美餐 access_token 拉取日历与档口菜单，写入 namespace 对应的 menu_snapshot。
    dates: 去重后的自然日列表。
    """
    ns = (namespace or '').strip()
    if not ns:
        return {'ok': False, 'reason': 'namespace 为空'}
    if not meican_forward_configured():
        return {'ok': False, 'reason': '未配置 MEICAN_FORWARD_CLIENT_ID/MEICAN_FORWARD_CLIENT_SECRET'}
    try:
        acc = UserMeicanAccount.objects.get(user=user)
    except UserMeicanAccount.DoesNotExist:
        return {'ok': False, 'reason': '无 user_meican_account'}
    if not (acc.access_token or '').strip():
        return {'ok': False, 'reason': '无美餐 access_token'}
    if (acc.account_namespace or '').strip() != ns:
        return {'ok': False, 'reason': 'namespace 与用户绑定 account_namespace 不一致'}

    day_list = sorted({d.strftime('%Y-%m-%d') if isinstance(d, date) else str(d)[:10] for d in dates})
    days_payload: List[Dict[str, Any]] = []
    for date_key in day_list:
        result = _fetch_date_restaurant_menus(acc, date_key, language=language)
        day_obj = _meal_sections_to_day_payload(date_key, _ensure_list(result.get('mealSections')))
        if day_obj.get('slots'):
            days_payload.append(day_obj)

    if not days_payload:
        return {'ok': False, 'reason': '美餐返回无可用菜品（日历或档口为空）'}

    out = sync_menu_days(ns, days_payload)
    return {
        'ok': not out.get('fatal') and out.get('slots_synced', 0) > 0,
        'slots_synced': out.get('slots_synced', 0),
        'errors': out.get('errors') or [],
        'fatal': out.get('fatal'),
    }
