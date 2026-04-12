# -*- coding: utf-8 -*-
"""
与 mc1 小程序 `services/meican/api.js` 一致：Forward calendarItems → restaurants/list +
recommendations/dishes，组装 days 后调用 sync_menu_days 落库。
美餐 client 凭证：优先表 `meican_client_config`（key=default，与 mc1 config 字段对应），
缺省再读环境变量 MEICAN_FORWARD_* / MEICAN_GRAPHQL_*（Forward 缺省回退 GraphQL，与 mc1 一致）。

支持从仓库根目录执行: python3 wxcloudrun/meican_menu_snapshot.py（会补全 sys.path 并 django.setup）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'wxcloudrun.settings')

import django

django.setup()

import json
from datetime import date, datetime, timedelta, timezone as dt_timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.utils import timezone as django_timezone

from wxcloudrun.meican_client_config import (
    meican_forward_credentials_configured,
    resolve_forward_base_url,
    resolve_forward_credentials,
    resolve_forward_referer,
    resolve_forward_user_agent,
    resolve_graphql_app,
    resolve_x_mc_device,
)
from wxcloudrun.menu_sync_service import sync_menu_days
from wxcloudrun.models import UserAccount, UserMeicanAccount

# 美餐 targetTime 毫秒戳按中国时区格式化（云主机 TZ=UTC 时 naive fromtimestamp 会偏一天，导致档口/菜品全空）
_CN_TZ = dt_timezone(timedelta(hours=8))


def _forward_base() -> str:
    return resolve_forward_base_url()


def _forward_credentials() -> Tuple[str, str]:
    return resolve_forward_credentials()


def _forward_headers() -> Dict[str, str]:
    cid, csec = _forward_credentials()
    app = resolve_graphql_app()
    return {
        'clientID': cid,
        'clientSecret': csec,
        'x-mc-app': app,
        'x-mc-device': resolve_x_mc_device(),
        'x-mc-page': '/auth/verification?stamp=AC',
        'Referer': resolve_forward_referer(),
        'accept-language': 'zh',
        'Accept': '*/*',
        'Content-Type': 'application/json',
        'User-Agent': resolve_forward_user_agent(),
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
            'Referer': resolve_forward_referer(),
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': resolve_forward_user_agent(),
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
    acc.token_expire_at = django_timezone.now() + timedelta(seconds=ttl)
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


def _forward_dict_candidates(payload: Any) -> List[Dict[str, Any]]:
    """美餐 Forward 常见 { data: {...} } 包裹；返回若干 dict 根供解析。"""
    out: List[Dict[str, Any]] = []
    if isinstance(payload, dict):
        out.append(payload)
        data = payload.get('data')
        if isinstance(data, dict):
            out.append(data)
    return out


def _infer_meal_bucket(item: Dict[str, Any]) -> str:
    """与小程序档口一致：晚餐 -> afternoon(DINNER)，其余含午餐/午 -> morning(LUNCH)。"""
    src = (
        f'{_pick_first(item, ["tabName", "name", "title", "mealType", "categoryName"], "")}'
        f'{_pick_first(item, ["targetTime", "startTime"], "")}'
    ).lower()
    if '晚餐' in src or '晚' in src or 'dinner' in src or 'night' in src:
        return 'afternoon'
    return 'morning'


def _normalize_forward_target_time(item: Dict[str, Any], date_key: str, bucket: str) -> str:
    raw = _pick_first(item, ['targetTime', 'startTime'], None)
    if raw is not None and f'{raw}'.strip() != '':
        try:
            n = float(raw)
            if n == n and n > 1e11:
                return datetime.fromtimestamp(n / 1000.0, tz=_CN_TZ).strftime('%Y-%m-%d %H:%M')
        except (TypeError, ValueError):
            pass
        s = str(raw).strip()
        if s:
            return s
    base = str(date_key).strip() if date_key else datetime.now().strftime('%Y-%m-%d')
    return f'{base} 17:00' if bucket == 'afternoon' else f'{base} 10:00'


def _calendar_item_to_entry(item: Dict[str, Any], default_date_key: str) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    tt_raw = _pick_first(item, ['targetTime', 'startTime'], None)
    if tt_raw is None or f'{tt_raw}'.strip() == '':
        return None
    tab_uid = _pick_first(item, ['tabUniqueId', 'tabUUID', 'userTab.uniqueId', 'userTab.tabUniqueId'], '')
    if not str(tab_uid).strip():
        ut = item.get('userTab')
        if isinstance(ut, dict):
            tab_uid = ut.get('uniqueId') or ut.get('tabUniqueId') or ut.get('id') or ''
    if not str(tab_uid).strip():
        return None
    dk = _pick_first(item, ['operativeDate', 'date', 'targetDate'], default_date_key)
    bucket = _infer_meal_bucket(item)
    target_time = _normalize_forward_target_time(item, str(dk)[:10], bucket)
    return {
        'dateKey': str(dk)[:10],
        'tabUniqueId': str(tab_uid).strip(),
        'tabName': _pick_first(item, ['tabName', 'name', 'title'], ''),
        'targetTime': target_time,
        'bucket': bucket,
    }


def _extract_calendar_entries(payload: Any, default_date_key: str = '') -> List[Dict[str, Any]]:
    def pred(item: Dict[str, Any]) -> bool:
        if not isinstance(item, dict):
            return False
        has_target = item.get('targetTime') is not None and f'{item.get("targetTime")}'.strip() != ''
        if not has_target:
            return False
        tab_id = _pick_first(item, ['tabUniqueId', 'tabUUID', 'uniqueId', 'userTab.uniqueId'], '')
        if not tab_id and isinstance(item.get('userTab'), dict):
            tab_id = item['userTab'].get('uniqueId') or item['userTab'].get('tabUniqueId') or ''
        return bool(tab_id)

    dkey = (default_date_key or datetime.now().strftime('%Y-%m-%d'))[:10]
    seen_keys: Set[str] = set()
    out: List[Dict[str, Any]] = []

    for root in _forward_dict_candidates(payload):
        date_list = root.get('dateList')
        if isinstance(date_list, list):
            for day_block in date_list:
                if not isinstance(day_block, dict):
                    continue
                day_date = str(day_block.get('date') or dkey)[:10]
                cal_items = day_block.get('calendarItemList')
                if not isinstance(cal_items, list):
                    continue
                for item in cal_items:
                    ent = _calendar_item_to_entry(item, day_date) if isinstance(item, dict) else None
                    if not ent:
                        continue
                    k = f'{ent["tabUniqueId"]}-{ent["targetTime"]}'
                    if k in seen_keys:
                        continue
                    seen_keys.add(k)
                    out.append(ent)

        for arr_key in ('calendarItems', 'items', 'list', 'dayList', 'calendarItemList', 'calendar'):
            arr = root.get(arr_key)
            if not isinstance(arr, list):
                continue
            for item in arr:
                ent = _calendar_item_to_entry(item, dkey) if isinstance(item, dict) else None
                if not ent:
                    continue
                k = f'{ent["tabUniqueId"]}-{ent["targetTime"]}'
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                out.append(ent)

    if not out:
        for root in _forward_dict_candidates(payload):
            candidates: List[Any] = []
            _collect_matching(root, pred, candidates, set())
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                ent = _calendar_item_to_entry(item, dkey)
                if not ent:
                    continue
                k = f'{ent["tabUniqueId"]}-{ent["targetTime"]}'
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                out.append(ent)

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

    seen_rid: Set[str] = set()
    restaurants: List[Dict[str, Any]] = []
    for root in _forward_dict_candidates(payload):
        lst = root.get('restaurantList')
        if not isinstance(lst, list):
            continue
        for r in lst:
            if not isinstance(r, dict):
                continue
            rid = str(_pick_first(r, ['restaurantId', 'id', 'uniqueId'], '') or '').strip()
            rname = str(_pick_first(r, ['restaurantName', 'name', 'title'], '') or '').strip()
            if not rid or not rname or rid in seen_rid:
                continue
            seen_rid.add(rid)
            restaurants.append(
                {
                    'id': rid,
                    'name': rname,
                    'distance': _pick_first(r, ['distance', 'distanceInMeter'], ''),
                    'status': 'available' if r.get('open') is True else _pick_first(r, ['status', 'sellStatus'], 'available'),
                    'menus': _extract_restaurant_menus(r),
                }
            )
    if restaurants:
        return restaurants

    found: List[Any] = []
    for root in _forward_dict_candidates(payload):
        _collect_matching(root, pred, found, set())
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
    """
    recommendations/dishes 根级常为 othersRegularDishList / myRegularDishList（与小程序抓包一致），
    不能仅靠深度扫描：内嵌 restaurant 会被误判成「菜」且真实列表易漏。
    """
    seen: Set[str] = set()
    dishes: List[Dict[str, Any]] = []

    for root in _forward_dict_candidates(payload):
        for list_key in ('othersRegularDishList', 'myRegularDishList'):
            lst = root.get(list_key)
            if not isinstance(lst, list):
                continue
            for item in lst:
                if not isinstance(item, dict) or item.get('isSection'):
                    continue
                did = item.get('dishId') if item.get('dishId') is not None else item.get('id')
                if did is None:
                    continue
                sid = str(did).strip()
                name = _pick_first(item, ['name', 'dishName', 'title'], '')
                if not sid or not name:
                    continue
                dedupe = f'{sid}:{name}'
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                pic = item.get('priceInCent')
                if pic is None:
                    pic = item.get('originalPriceInCent')
                price_fmt = _format_price(pic) if pic is not None else _format_price(
                    _pick_first(item, ['price', 'priceString', 'priceCent'], '')
                )
                pic_int = None
                if pic is not None and f'{pic}'.strip() != '':
                    try:
                        pic_int = int(float(pic))
                    except (TypeError, ValueError):
                        pic_int = None
                dishes.append(
                    {
                        'id': sid,
                        'name': name,
                        'price': price_fmt,
                        'priceInCent': pic_int,
                        'status': _pick_first(item, ['status', 'sellStatus'], 'available'),
                        'restaurant': item.get('restaurant') if isinstance(item.get('restaurant'), dict) else {},
                    }
                )
        if dishes:
            return dishes

    def pred(item: Dict[str, Any]) -> bool:
        if not isinstance(item, dict) or item.get('isSection'):
            return False
        name = item.get('name') or item.get('dishName') or item.get('title')
        if not name:
            return False
        if item.get('dishId') or item.get('revisionId'):
            return True
        if item.get('id') is not None and (
            item.get('priceInCent') is not None
            or item.get('originalPriceInCent') is not None
            or item.get('priceString')
        ):
            return True
        return False

    found: List[Any] = []
    for root in _forward_dict_candidates(payload):
        _collect_matching(root, pred, found, set())
    for dish in found:
        if not isinstance(dish, dict):
            continue
        sid = str(_pick_first(dish, ['dishId', 'id', 'revisionId', 'productId'], '') or '').strip()
        name = _pick_first(dish, ['dishName', 'name', 'title'], '')
        if not sid or not name:
            continue
        dedupe = f'{sid}:{name}'
        if dedupe in seen:
            continue
        seen.add(dedupe)
        pic = dish.get('priceInCent') if dish.get('priceInCent') is not None else dish.get('originalPriceInCent')
        pic_int = None
        if pic is not None and f'{pic}'.strip() != '':
            try:
                pic_int = int(float(pic))
            except (TypeError, ValueError):
                pic_int = None
        dishes.append(
            {
                'id': sid,
                'name': name,
                'price': _format_price(pic) if pic is not None else _format_price(
                    _pick_first(dish, ['price', 'priceString', 'priceCent'], '')
                ),
                'priceInCent': pic_int,
                'status': _pick_first(dish, ['status', 'sellStatus'], 'available'),
                'restaurant': dish.get('restaurant') if isinstance(dish.get('restaurant'), dict) else {},
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
        dish_id = str(
            _pick_first(dish, ['id', 'dishId', 'revisionId', 'productId', 'uniqueId'], '') or ''
        ).strip()
        dish_name = _pick_first(dish, ['name', 'dishName', 'title'], None)
        if not dish_id or not dish_name:
            continue
        rest = dish.get('restaurant') if isinstance(dish.get('restaurant'), dict) else {}
        rid = str(rest.get('uniqueId') or rest.get('id') or '').strip()[:64]
        rname = str(rest.get('name') or '').strip()[:128]
        pic = dish.get('priceInCent')
        if pic is None:
            pic = dish.get('originalPriceInCent')
        if pic is not None:
            try:
                price_cent = int(pic)
            except (TypeError, ValueError):
                price_cent = _parse_price_cent(dish.get('price'))
        else:
            price_cent = _parse_price_cent(dish.get('price'))
        out.append(
            {
                'dishId': dish_id,
                'dishName': dish_name,
                'priceInCent': price_cent,
                'status': dish.get('status') or 'available',
                'restaurantId': rid,
                'restaurantName': rname,
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
    entries = _extract_calendar_entries(cal, default_date_key=date_key)
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
    return meican_forward_credentials_configured()


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
        return {
            'ok': False,
            'reason': '未配置美餐 client：请在 meican_client_config 写入 default 行，或配置环境变量 MEICAN_FORWARD_* / MEICAN_GRAPHQL_*',
        }
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
        hint: Dict[str, Any] = {
            'datesQueried': day_list,
            'note': (
                '若今天是周日，勿用 mon=today-weekday()（会得到上周一）；请用 '
                'resolve_sync_work_dates() 或 sync_meican_menu_snapshot_for_current_workweek()。'
                ' shell -c 须单行，import sync_meican_menu_snapshot_for_user_dates 勿断行。'
            ),
            'suggestedWorkDates': [d.isoformat() for d in resolve_sync_work_dates()],
        }
        if day_list:
            d0 = day_list[0]
            cal0 = _json_request(
                acc,
                'GET',
                '/api/v2.1/calendarItems/list',
                {'withOrderDetail': 'false', 'beginDate': d0, 'endDate': d0},
            )
            hint['sampleDate'] = d0
            hint['calendarIsDict'] = isinstance(cal0, dict)
            if isinstance(cal0, dict):
                hint['calendarTopKeys'] = sorted(cal0.keys())[:24]
                dl = cal0.get('dateList')
                hint['dateListLen'] = len(dl) if isinstance(dl, list) else 0
                hint['calendarEntriesParsed'] = len(_extract_calendar_entries(cal0, default_date_key=d0))
            if isinstance(cal0, dict) and cal0.get('_http_status'):
                hint['calendarHttpStatus'] = cal0.get('_http_status')
                if cal0.get('_http_status') == 401:
                    hint['auth'] = (
                        'calendarItems 返回 401：access_token 已失效，请小程序重新登录后 '
                        'PUT /api/v1/users/<id>/meican-session 更新 token'
                    )
            if isinstance(cal0, dict) and cal0.get('_parse_error'):
                hint['calendarJsonParseError'] = True
        return {
            'ok': False,
            'reason': '美餐返回无可用菜品（日历或档口为空），详见 hint',
            'hint': hint,
        }

    out = sync_menu_days(ns, days_payload)
    return {
        'ok': not out.get('fatal') and out.get('slots_synced', 0) > 0,
        'slots_synced': out.get('slots_synced', 0),
        'errors': out.get('errors') or [],
        'fatal': out.get('fatal'),
    }


def resolve_sync_work_dates(today: Optional[date] = None) -> List[date]:
    """
    与 `recommendation_service.resolve_week_start_monday` 及周推荐任务对齐的 5 个工作日：
    - 若基准日是周日：从「下周一」起连续 5 个工作日（例如 4/12 周日 → 4/13–4/17）。
    - 否则：从「本周一」起连续 5 个工作日。
    """
    t = today or django_timezone.now().date()
    if t.weekday() == 6:
        monday = t + timedelta(days=1)
    else:
        monday = t - timedelta(days=t.weekday())
    return [monday + timedelta(days=i) for i in range(5)]


def sync_meican_menu_snapshot_for_current_workweek(
    user: UserAccount,
    namespace: str,
    *,
    language: str = 'zh-CN',
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """按「当前业务周」同步 5 个工作日菜单（周日用下一周，勿用手写 mon=...-weekday）。"""
    return sync_meican_menu_snapshot_for_user_dates(
        user, namespace, resolve_sync_work_dates(today), language=language
    )


if __name__ == '__main__':
    print(
        '本文件为库模块，无独立 CLI。请在 manage.py shell 中导入并调用：\n'
        '  sync_meican_menu_snapshot_for_user_dates(user, namespace, [date(...), ...])\n'
        '  sync_meican_menu_snapshot_for_current_workweek(user, namespace)  # 与周推荐一致：周日=下一周\n'
        '  resolve_sync_work_dates()  # 查看当前应同步的 5 个工作日\n'
        '生成推荐前会自动尝试同步；亦可执行 python3 manage.py run_weekly_recommendations --help',
        flush=True,
    )
