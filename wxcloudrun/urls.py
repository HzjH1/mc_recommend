"""wxcloudrun URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from wxcloudrun import views, v1_views
from django.conf.urls import url

urlpatterns = (
    # 计数器接口
    url(r'^^api/count(/)?$', views.counter),
    # 推荐菜品接口
    url(r'^^api/recommend(/)?$', views.recommend_dishes),
    # V1-用户偏好
    url(r'^^api/v1/users/(?P<user_id>\d+)/preferences(/)?$', v1_views.put_user_preferences),
    # V1-自动点餐配置
    url(r'^^api/v1/users/(?P<user_id>\d+)/auto-order-config(/)?$', v1_views.put_auto_order_config),
    # V1-每日推荐
    url(r'^^api/v1/users/(?P<user_id>\d+)/recommendations/daily(/)?$', v1_views.get_daily_recommendations),
    # V1-本周工作日推荐（服务端）
    url(r'^^api/v1/users/(?P<user_id>\d+)/recommendations/week(/)?$', v1_views.get_week_recommendations),
    # V1-同步美餐周菜单并生成推荐
    url(r'^^api/v1/users/(?P<user_id>\d+)/sync/meican-week(/)?$', v1_views.post_user_sync_meican_week),
    # V1-手动下单
    url(r'^^api/v1/users/(?P<user_id>\d+)/orders(/)?$', v1_views.post_manual_order),
    # V1-内部任务触发
    url(r'^^api/v1/internal/jobs/auto-order/run(/)?$', v1_views.post_internal_auto_order_run),
    # V1-内部任务状态查询
    url(r'^^api/v1/internal/jobs/auto-order/(?P<job_id>\d+)(/)?$', v1_views.get_internal_auto_order_job),
    # V1-美餐会话同步（小程序登录后上报，供云端任务换票）
    url(r'^^api/v1/users/(?P<user_id>\d+)/meican-session(/)?$', v1_views.put_user_meican_session),
    url(r'^^api/v1/users/(?P<user_id>\d+)/meican/access(/)?$', v1_views.get_user_meican_access),
    # V1-内部：单用户 ensure / 强制 refresh
    url(r'^^api/v1/internal/meican/users/(?P<user_id>\d+)/ensure-token(/)?$', v1_views.post_internal_meican_ensure_token),
    # V1-内部：批量主动刷新临近过期的 token
    url(r'^^api/v1/internal/meican/tokens/refresh-due(/)?$', v1_views.post_internal_meican_refresh_due_tokens),

    # 获取主页
    url(r'(/)?$', views.index),
)
