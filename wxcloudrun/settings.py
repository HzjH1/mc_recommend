import os
from pathlib import Path
import time

CUR_PATH = os.path.dirname(os.path.realpath(__file__))  
LOG_PATH = os.path.join(os.path.dirname(CUR_PATH), 'logs') # LOG_PATH是存放日志的路径
if not os.path.exists(LOG_PATH): os.mkdir(LOG_PATH)  # 如果不存在这个logs文件夹，就自动创建一个

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-_&03zc)d*3)w-(0grs-+t-0jjxktn7k%$3y6$9=x_n_ibg4js6'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['*']

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'wxcloudrun'
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    # 'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'wxcloudrun.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'wxcloudrun.wsgi.application'

# Database
# https://docs.djangoproject.com/en/3.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get("MYSQL_DATABASE", 'django_demo'),
        'USER': os.environ.get("MYSQL_USERNAME"),
        'HOST': os.environ.get("MYSQL_ADDRESS").split(':')[0],
        'PORT': os.environ.get("MYSQL_ADDRESS").split(':')[1],
        'PASSWORD': os.environ.get("MYSQL_PASSWORD"),
        'OPTIONS': {'charset': 'utf8mb4'},
    }
}

# Password validation
# https://docs.djangoproject.com/en/3.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
    'formatters': {
        # 日志格式
        'standard': {
            'format': '[%(asctime)s] [%(filename)s:%(lineno)d] [%(module)s:%(funcName)s] '
                      '[%(levelname)s]- %(message)s'},
        'simple': {  # 简单格式
            'format': '%(levelname)s %(message)s'
        },
    },
    # 过滤
    'filters': {
    },
    # 定义具体处理日志的方式
    'handlers': {
        # 默认记录所有日志
        'default': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(LOG_PATH, 'all-{}.log'.format(time.strftime('%Y-%m-%d'))),
            'maxBytes': 1024 * 1024 * 5,  # 文件大小
            'backupCount': 5,  # 备份数
            'formatter': 'standard',  # 输出格式
            'encoding': 'utf-8',  # 设置默认编码，否则打印出来汉字乱码
        },
        # 输出错误日志
        'error': {
            'level': 'ERROR',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(LOG_PATH, 'error-{}.log'.format(time.strftime('%Y-%m-%d'))),
            'maxBytes': 1024 * 1024 * 5,  # 文件大小
            'backupCount': 5,  # 备份数
            'formatter': 'standard',  # 输出格式
            'encoding': 'utf-8',  # 设置默认编码
        },
        # 控制台输出
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'standard'
        },
        # 输出info日志
        'info': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(LOG_PATH, 'info-{}.log'.format(time.strftime('%Y-%m-%d'))),
            'maxBytes': 1024 * 1024 * 5,
            'backupCount': 5,
            'formatter': 'standard',
            'encoding': 'utf-8',  # 设置默认编码
        },
    },
    # 配置用哪几种 handlers 来处理日志
    'loggers': {
        # 类型 为 django 处理所有类型的日志， 默认调用
        'django': {
            'handlers': ['default', 'console'],
            'level': 'INFO',
            'propagate': False
        },
        # log 调用时需要当作参数传入
        'log': {
            'handlers': ['error', 'info', 'console', 'default'],
            'level': 'INFO',
            'propagate': True
        },
    }
}

# Internationalization
# https://docs.djangoproject.com/en/3.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'Asia/Shanghai'

USE_I18N = True

USE_L10N = True

USE_TZ = False

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.2/howto/static-files/

STATIC_URL = '/static/'

# Default primary key field type
# https://docs.djangoproject.com/en/3.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGS_DIR = '/data/logs/'

# AI 推荐接口配置
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', 'sk-a3933f52b50d4629b9df0dfd3a99e133')
OPENAI_BASE_URL = os.environ.get('OPENAI_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
OPENAI_MODEL = os.environ.get('OPENAI_MODEL', 'qwen-plus-3.6')
OPENAI_TIMEOUT_SECONDS = int(os.environ.get('OPENAI_TIMEOUT_SECONDS', '15'))

# 内部任务接口鉴权（为空时不校验）
INTERNAL_JOB_TOKEN = os.environ.get('INTERNAL_JOB_TOKEN', '')
AUTO_ORDER_LUNCH_DEADLINE = os.environ.get('AUTO_ORDER_LUNCH_DEADLINE', '10:30')
AUTO_ORDER_DINNER_DEADLINE = os.environ.get('AUTO_ORDER_DINNER_DEADLINE', '16:30')

# 每周推荐任务：请在微信云托管控制台将「定时触发」配置为每周日调用
# POST /api/v1/internal/jobs/recommendations/weekly-run（需 X-Internal-Token，与 INTERNAL_JOB_TOKEN 一致）
# 为 true 时仅允许在周日执行该接口（防误触）；默认 false 便于联调
RECOMMENDATION_WEEKLY_REQUIRE_SUNDAY = os.environ.get('RECOMMENDATION_WEEKLY_REQUIRE_SUNDAY', '').lower() in (
    '1', 'true', 'yes',
)

# 服务端拉取美餐菜单（与 mc1 Forward 一致）；未配置时推荐任务仍依赖小程序 week-sync 上报
MEICAN_FORWARD_BASE_URL = os.environ.get('MEICAN_FORWARD_BASE_URL', 'https://www.meican.com/forward')
MEICAN_FORWARD_CLIENT_ID = os.environ.get('MEICAN_FORWARD_CLIENT_ID', 'Xqr8w0Uk4ciodqfPwjhav5rdxTaYepD')
MEICAN_FORWARD_CLIENT_SECRET = os.environ.get('MEICAN_FORWARD_CLIENT_SECRET', 'vD11O6xI9bG3kqYRu9OyPAHkRGxLh4E')
# 与 mc1 一致：Forward 未配置时可回退为 GraphQL client（仅作环境变量兜底，优先用库表 meican_client_config）
MEICAN_GRAPHQL_CLIENT_ID = os.environ.get('MEICAN_GRAPHQL_CLIENT_ID', 'WYAiIJZPc8e21UHcKHVUeVo2SpNVrni')
MEICAN_GRAPHQL_CLIENT_SECRET = os.environ.get('MEICAN_GRAPHQL_CLIENT_SECRET', 'WbRV03U0MyQzRhXrvXhyopkavkIRaBg')
MEICAN_GRAPHQL_APP = os.environ.get('MEICAN_GRAPHQL_APP', 'meican/web-pc (prod;4.90.1;sys;main)')
MEICAN_GRAPHQL_REFERER = os.environ.get('MEICAN_GRAPHQL_REFERER', 'https://www.meican.com/').strip()
WEB_BACKEND_API_BASE = os.environ.get('WEB_BACKEND_API_BASE', '').strip().rstrip('/')
WEB_MEICAN_API_BASE = os.environ.get('WEB_MEICAN_API_BASE', '').strip().rstrip('/')
# 与小程序 wx.request 实际行为接近：桌面 Chrome UA 易被美餐返回空列表；留空则用下方默认「类微信」UA
_default_meican_ua = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) '
    'Mobile/15E148 MicroMessenger/8.0.38(0x1800262c) NetType/WIFI Language/zh_CN'
)
MEICAN_FORWARD_USER_AGENT = (os.environ.get('MEICAN_FORWARD_USER_AGENT', '') or _default_meican_ua).strip()
MEICAN_FORWARD_REFERER = (os.environ.get('MEICAN_FORWARD_REFERER', '') or 'https://servicewechat.com/').strip()
# 与小程序 storage 中 x-mc-device 一致最佳；留空则按 client_id 派生稳定 UUID（多进程一致）
MEICAN_X_MC_DEVICE = os.environ.get('MEICAN_X_MC_DEVICE', '').strip()
