# -*- coding: utf-8 -*-
"""
解析美餐客户端配置：优先数据库 `meican_client_config`（key=default），
其次 Django settings 环境变量。凭证选择顺序与 mc1 `getForwardClientId` 一致：
forward 优先，缺省则 graphql。
"""
from typing import Tuple

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


def resolve_forward_base_url() -> str:
    row = get_meican_client_config_row()
    if row is not None and (row.forward_base_url or '').strip():
        return str(row.forward_base_url).strip().rstrip('/')
    return (getattr(settings, 'MEICAN_FORWARD_BASE_URL', None) or 'https://www.meican.com/forward').rstrip('/')


def resolve_graphql_app() -> str:
    row = get_meican_client_config_row()
    if row is not None and (row.graphql_app or '').strip():
        return str(row.graphql_app).strip()
    return (getattr(settings, 'MEICAN_GRAPHQL_APP', None) or 'meican/web-pc (prod;4.90.1;sys;main)').strip()


def meican_forward_credentials_configured() -> bool:
    cid, csec = resolve_forward_credentials()
    return bool(cid and csec)
