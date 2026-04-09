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
    # V1-手动下单
    url(r'^^api/v1/users/(?P<user_id>\d+)/orders(/)?$', v1_views.post_manual_order),
    # V1-内部任务触发
    url(r'^^api/v1/internal/jobs/auto-order/run(/)?$', v1_views.post_internal_auto_order_run),
    # V1-内部任务状态查询
    url(r'^^api/v1/internal/jobs/auto-order/(?P<job_id>\d+)(/)?$', v1_views.get_internal_auto_order_job),

    # 获取主页
    url(r'(/)?$', views.index),
)
