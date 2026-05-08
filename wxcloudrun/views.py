import json
import logging
import re
from pathlib import Path
from urllib import request as urllib_request
from urllib import error as urllib_error

from django.conf import settings
from django.http import JsonResponse
from django.http import HttpResponse
from django.shortcuts import render
from wxcloudrun.models import Counter


logger = logging.getLogger('log')


def _json_rsp(payload):
    return JsonResponse(payload, json_dumps_params={'ensure_ascii': False})


def _normalize_base_url(value):
    return str(value or '').strip().rstrip('/')


def _request_origin_with_forwarded_proto(request):
    """
    在反向代理（如云托管）后优先使用 X-Forwarded-Proto/Host 还原外部访问域名与协议，
    避免 web runtime config 注入 http 导致浏览器 Mixed Content。
    """
    forwarded_proto = str(request.META.get('HTTP_X_FORWARDED_PROTO') or '').split(',')[0].strip().lower()
    forwarded_host = str(request.META.get('HTTP_X_FORWARDED_HOST') or '').split(',')[0].strip()
    if forwarded_proto in {'http', 'https'} and forwarded_host:
        return f'{forwarded_proto}://{forwarded_host}'
    if forwarded_proto in {'http', 'https'}:
        host = request.get_host()
        if host:
            return f'{forwarded_proto}://{host}'
    return _normalize_base_url(request.build_absolute_uri('/'))


def _build_web_runtime_config(request):
    backend_api_base = _normalize_base_url(getattr(settings, 'WEB_BACKEND_API_BASE', ''))
    meican_api_base = _normalize_base_url(getattr(settings, 'WEB_MEICAN_API_BASE', ''))
    request_origin = _request_origin_with_forwarded_proto(request)
    if not backend_api_base:
        backend_api_base = request_origin
    if not meican_api_base:
        meican_api_base = backend_api_base
    return {
        'backendApiBase': backend_api_base,
        'meicanApiBase': meican_api_base,
    }


def _normalize_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {'是', 'true', '1', 'yes', 'y', '吃', '能'}:
            return True
        if value in {'否', 'false', '0', 'no', 'n', '不吃', '不能'}:
            return False
    return None


def _contains_any(text, keywords):
    return any(keyword in text for keyword in keywords)


def _extract_preference(pref):
    pref = pref if isinstance(pref, dict) else {}
    spicy = _normalize_bool(
        pref.get('isSpicy')
        if 'isSpicy' in pref else pref.get('是否吃辣', pref.get('eatSpicy'))
    )
    halal = _normalize_bool(
        pref.get('isHalal')
        if 'isHalal' in pref else pref.get('是否清真')
    )
    losing_fat = _normalize_bool(
        pref.get('isLosingFat')
        if 'isLosingFat' in pref else pref.get('是否正在减脂')
    )
    prefer_noodle = _normalize_bool(
        pref.get('preferNoodle')
        if 'preferNoodle' in pref else pref.get('喜欢吃粉面')
    )
    prefer_rice = _normalize_bool(
        pref.get('preferRice')
        if 'preferRice' in pref else pref.get('喜欢吃饭')
    )
    extra = str(pref.get('other', pref.get('其他补充', '')) or '')
    return {
        'spicy': spicy,
        'halal': halal,
        'losing_fat': losing_fat,
        'prefer_noodle': prefer_noodle,
        'prefer_rice': prefer_rice,
        'extra': extra,
    }


def _score_menu_items(pref, menu_items):
    scored = []
    extra = pref['extra']
    avoid_onion = bool(re.search(r'不能吃葱|不吃葱', extra))
    like_coriander = '香菜' in extra
    for item in menu_items:
        name = str(item.get('name', ''))
        if not name:
            continue
        score = 0
        reasons = []
        if pref['spicy'] is False and _contains_any(name, ['辣', '香辣', '辣子', '微辣', '麻辣']):
            score -= 3
            reasons.append('避开辣味偏好')
        elif pref['spicy'] is True and _contains_any(name, ['辣', '香辣', '辣子', '微辣', '麻辣']):
            score += 1
            reasons.append('匹配吃辣偏好')

        if pref['halal'] is True and _contains_any(name, ['猪', '排骨', '猪手', '里脊']):
            score -= 4
            reasons.append('清真偏好下规避猪肉类')

        if pref['losing_fat'] is True:
            if _contains_any(name, ['汤', '青菜', '瘦肉', '牛肉']):
                score += 2
                reasons.append('减脂期优先清淡高蛋白')
            if _contains_any(name, ['红烧', '排骨', '猪手', '里脊', '糖醋']):
                score -= 2
                reasons.append('减脂期降低高油高糖菜品权重')

        if pref['prefer_noodle'] is True and _contains_any(name, ['粉', '面', '米粉']):
            score += 2
            reasons.append('主食偏好粉面')
        if pref['prefer_rice'] is True and '饭' in name:
            score += 2
            reasons.append('主食偏好米饭')

        if avoid_onion and '葱' in name:
            score -= 5
            reasons.append('备注不吃葱')
        if like_coriander and '香菜' in name:
            score += 2
            reasons.append('备注喜欢香菜')

        scored.append({
            'id': item.get('id'),
            'name': name,
            'restaurant': item.get('restaurant', {}),
            'score': score,
            'reason': '；'.join(reasons) if reasons else '基础推荐',
        })

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored


def _call_ai_recommendation(pref_raw, menu_items):
    api_key = getattr(settings, 'OPENAI_API_KEY', '')
    if not api_key:
        raise ValueError('OPENAI_API_KEY is missing')

    base_url = getattr(settings, 'OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
    model = getattr(settings, 'OPENAI_MODEL', 'gpt-4o-mini')
    timeout = int(getattr(settings, 'OPENAI_TIMEOUT_SECONDS', 15))

    prompt = {
        '个人饮食偏好': pref_raw,
        '菜单列表': menu_items,
        '要求': [
            '从菜单里推荐最符合偏好的3道菜',
            '严格返回JSON，不要额外文字',
            'JSON格式：{"recommendations":[{"id":菜品id,"reason":"推荐理由"}]}',
            '如果存在冲突偏好（如不吃辣），要明确规避',
            '若用户备注提到忌口（如不吃葱），优先规避',
        ],
    }

    payload = json.dumps({
        'model': model,
        'temperature': 0.2,
        'messages': [
            {
                'role': 'system',
                'content': '你是点餐推荐助手，只返回合法JSON。',
            },
            {
                'role': 'user',
                'content': json.dumps(prompt, ensure_ascii=False),
            },
        ],
    }).encode('utf-8')

    req = urllib_request.Request(
        url=base_url + '/chat/completions',
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer {}'.format(api_key),
        },
        method='POST',
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8')
    except urllib_error.URLError as err:
        raise ValueError('AI service request failed: {}'.format(err))

    data = json.loads(body)
    content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
    if not content:
        raise ValueError('AI returned empty content')
    if isinstance(content, str) and '```' in content:
        content = content.strip().strip('`')
        content = content.replace('json\n', '', 1).strip()
    ai_json = json.loads(content)
    return ai_json.get('recommendations', [])


def index(request, _):
    """
    获取主页

     `` request `` 请求对象
    """

    return render(request, 'index.html')


def web_index(request, *_args, **_kwargs):
    """
    Vue Web SPA 入口：构建产物位于 wxcloudrun/static/web/index.html（Vite 输出）。
    """
    try:
        p = Path(settings.BASE_DIR) / 'wxcloudrun' / 'static' / 'web' / 'index.html'
        html = p.read_text(encoding='utf-8')
        runtime_config_json = json.dumps(
            _build_web_runtime_config(request),
            ensure_ascii=False,
        ).replace('</', '<\\/')
        runtime_config_script = (
            '<script>'
            f'window.__MEAL_HELPER_RUNTIME_CONFIG__ = {runtime_config_json};'
            '</script>'
        )
        if '</head>' in html:
            html = html.replace('</head>', f'  {runtime_config_script}\n</head>', 1)
        else:
            html = f'{runtime_config_script}\n{html}'
        return HttpResponse(html, content_type='text/html; charset=utf-8')
    except Exception as exc:
        logger.error('web_index render failed: %s', exc)
        return HttpResponse('web build not found', status=404, content_type='text/plain; charset=utf-8')


def counter(request, _):
    """
    获取当前计数

     `` request `` 请求对象
    """

    rsp = _json_rsp({'code': 0, 'errorMsg': ''})
    if request.method == 'GET' or request.method == 'get':
        rsp = get_count()
    elif request.method == 'POST' or request.method == 'post':
        rsp = update_count(request)
    else:
        rsp = _json_rsp({'code': -1, 'errorMsg': '请求方式错误'})
    logger.info('response result: {}'.format(rsp.content.decode('utf-8')))
    return rsp


def get_count():
    """
    获取当前计数
    """

    try:
        data = Counter.objects.get(id=1)
    except Counter.DoesNotExist:
        return _json_rsp({'code': 0, 'data': 0})
    return _json_rsp({'code': 0, 'data': data.count})


def update_count(request):
    """
    更新计数，自增或者清零

    `` request `` 请求对象
    """

    logger.info('update_count req: {}'.format(request.body))

    body_unicode = request.body.decode('utf-8')
    body = json.loads(body_unicode)

    if 'action' not in body:
        return JsonResponse({'code': -1, 'errorMsg': '缺少action参数'},
                            json_dumps_params={'ensure_ascii': False})

    if body['action'] == 'inc':
        try:
            data = Counter.objects.get(id=1)
        except Counter.DoesNotExist:
            data = Counter()
        data.id = 1
        data.count += 1
        data.save()
        return JsonResponse({'code': 0, "data": data.count},
                    json_dumps_params={'ensure_ascii': False})
    elif body['action'] == 'clear':
        try:
            data = Counter.objects.get(id=1)
            data.delete()
        except Counter.DoesNotExist:
            logger.info('record not exist')
        return JsonResponse({'code': 0, 'data': 0},
                    json_dumps_params={'ensure_ascii': False})
    else:
        return JsonResponse({'code': -1, 'errorMsg': 'action参数错误'},
                    json_dumps_params={'ensure_ascii': False})


def recommend_dishes(request, _):
    """
    根据个人饮食偏好和菜单列表推荐top3菜品
    """
    if request.method not in {'POST', 'post'}:
        return _json_rsp({'code': -1, 'errorMsg': '请求方式错误，请使用POST'})

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return _json_rsp({'code': -1, 'errorMsg': '请求体不是合法JSON'})

    personal_preference = body.get('personalPreference', {})
    menu_items = body.get('menuList')
    if menu_items is None:
        menu_items = body.get('othersRegularDishList')
    if isinstance(menu_items, dict):
        menu_items = menu_items.get('othersRegularDishList')

    if not isinstance(menu_items, list) or len(menu_items) == 0:
        return _json_rsp({'code': -1, 'errorMsg': '缺少menuList/othersRegularDishList参数或为空'})

    available_items = [
        item for item in menu_items
        if isinstance(item, dict) and item.get('restaurant', {}).get('available', True)
    ]
    if not available_items:
        return _json_rsp({'code': -1, 'errorMsg': '菜单中无可用菜品'})

    pref = _extract_preference(personal_preference)
    scored_items = _score_menu_items(pref, available_items)
    scored_map = {item['id']: item for item in scored_items}
    recommendations = []
    selected_ids = set()

    try:
        ai_results = _call_ai_recommendation(personal_preference, available_items)
    except Exception as err:
        logger.warning('AI recommendation failed, fallback to rules: %s', err)
        ai_results = []

    for ai_item in ai_results:
        dish_id = ai_item.get('id') if isinstance(ai_item, dict) else None
        if dish_id in scored_map and dish_id not in selected_ids:
            merged = dict(scored_map[dish_id])
            merged['reason'] = ai_item.get('reason') or merged['reason']
            recommendations.append(merged)
            selected_ids.add(dish_id)
        if len(recommendations) >= 3:
            break

    if len(recommendations) < 3:
        for item in scored_items:
            if item['id'] in selected_ids:
                continue
            recommendations.append(item)
            selected_ids.add(item['id'])
            if len(recommendations) >= 3:
                break

    return _json_rsp({'code': 0, 'data': recommendations[:3]})
