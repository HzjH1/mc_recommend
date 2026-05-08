# -*- coding: utf-8 -*-
"""
解析美餐客户端配置：优先数据库 `meican_client_config`（key=default），
其次 Django settings 环境变量。凭证选择顺序与 mc1 `getForwardClientId` 一致：
forward 优先，缺省则 graphql。

说明：美餐 Forward 对 User-Agent / Referer / x-mc-device 较敏感；小程序里 wx.request 会带类微信 UA，
服务端若用桌面 Chrome UA 易出现「日历/档口返回空」而同一 token 在 curl/小程序下有数据。
"""
import uuid
from typing import Tuple
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings


def get_meican_client_config_row():
    try:
        from wxcloudrun.models import MeicanClientConfig

        return MeicanClientConfig.objects.filter(key='default').first()
    except Exception:
        return None


def resolve_forward_credentials() -> Tuple[str, str]:
    row = get_meican_client_config_row()
    if row is not None:
        cid = (row.forward_client_id or row.graphql_client_id or '').strip()
        csec = (row.forward_client_secret or row.graphql_client_secret or '').strip()
        if cid and csec:
            return cid, csec
    cid = (
        (getattr(settings, 'MEICAN_FORWARD_CLIENT_ID', None) or '')
        or (getattr(settings, 'MEICAN_GRAPHQL_CLIENT_ID', None) or '')
    ).strip()
    csec = (
        (getattr(settings, 'MEICAN_FORWARD_CLIENT_SECRET', None) or '')
        or (getattr(settings, 'MEICAN_GRAPHQL_CLIENT_SECRET', None) or '')
    ).strip()
    return cid, csec


def resolve_graphql_credentials() -> Tuple[str, str]:
    row = get_meican_client_config_row()
    if row is not None:
        cid = (row.graphql_client_id or row.forward_client_id or '').strip()
        csec = (row.graphql_client_secret or row.forward_client_secret or '').strip()
        if cid and csec:
            return cid, csec
    cid = (
        (getattr(settings, 'MEICAN_GRAPHQL_CLIENT_ID', None) or '')
        or (getattr(settings, 'MEICAN_FORWARD_CLIENT_ID', None) or '')
    ).strip()
    csec = (
        (getattr(settings, 'MEICAN_GRAPHQL_CLIENT_SECRET', None) or '')
        or (getattr(settings, 'MEICAN_FORWARD_CLIENT_SECRET', None) or '')
    ).strip()
    return cid, csec


def resolve_forward_base_url() -> str:
    def _normalize_forward_base_url(raw_value: str) -> str:
        base = str(raw_value or '').strip().rstrip('/')
        if not base:
            return 'https://www.meican.com/forward'
        parsed = urlsplit(base)
        if not parsed.scheme or not parsed.netloc:
            return base
        path = (parsed.path or '').rstrip('/')
        if path in {'', '/'}:
            path = '/forward'
        normalized = parsed._replace(path=path, query='', fragment='')
        return urlunsplit(normalized).rstrip('/')

    row = get_meican_client_config_row()
    if row is not None and (row.forward_base_url or '').strip():
        return _normalize_forward_base_url(str(row.forward_base_url))
    return _normalize_forward_base_url(getattr(settings, 'MEICAN_FORWARD_BASE_URL', None) or 'https://www.meican.com/forward')


def resolve_graphql_app() -> str:
    row = get_meican_client_config_row()
    if row is not None and (row.graphql_app or '').strip():
        return str(row.graphql_app).strip()
    return (getattr(settings, 'MEICAN_GRAPHQL_APP', None) or 'meican/web-pc (prod;4.90.1;sys;main)').strip()


def resolve_forward_user_agent() -> str:
    row = get_meican_client_config_row()
    if row is not None and getattr(row, 'forward_user_agent', None) and str(row.forward_user_agent).strip():
        return str(row.forward_user_agent).strip()[:512]
    return (getattr(settings, 'MEICAN_FORWARD_USER_AGENT', None) or '').strip()


def resolve_forward_referer() -> str:
    row = get_meican_client_config_row()
    if row is not None and getattr(row, 'forward_referer', None) and str(row.forward_referer).strip():
        return str(row.forward_referer).strip()[:512]
    return (getattr(settings, 'MEICAN_FORWARD_REFERER', None) or 'https://servicewechat.com/').strip()


def resolve_graphql_referer() -> str:
    return (getattr(settings, 'MEICAN_GRAPHQL_REFERER', None) or 'https://www.meican.com/').strip()


def resolve_x_mc_device() -> str:
    row = get_meican_client_config_row()
    if row is not None and getattr(row, 'x_mc_device', None) and str(row.x_mc_device).strip():
        return str(row.x_mc_device).strip()[:64]
    env_dev = (getattr(settings, 'MEICAN_X_MC_DEVICE', None) or '').strip()
    if env_dev:
        return env_dev[:64]
    cid, _ = resolve_forward_credentials()
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, 'meal-helper-meican-device:' + (cid or 'meican')))[:36]


def meican_forward_credentials_configured() -> bool:
    cid, csec = resolve_forward_credentials()
    return bool(cid and csec)
