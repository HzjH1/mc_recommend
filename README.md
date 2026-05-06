# mc_recommend（美餐推荐后端）

基于 **Django 3.2** 的微信云托管后端：用户偏好、菜单快照、**规则推荐**落库、每日推荐查询、自动点餐任务与内部定时接口。小程序（如 mc1）通过 V1 API 上报美餐会话与周菜单，服务端可同步美餐 Forward 菜单并写入 MySQL。

---

## 功能概览

| 能力 | 说明 |
|------|------|
| 用户偏好 | `PUT /api/v1/users/<id>/preferences` |
| 美餐会话 | `PUT /api/v1/users/<id>/meican-session`（access/refresh、namespace） |
| 菜单周同步 | `POST /api/v1/users/<id>/menu/week-sync` → `menu_snapshot` / `menu_item`（同 `dish_id` 原地更新） |
| 每日推荐查询 | `GET /api/v1/users/<id>/recommendations/daily`（读 `recommendation_batch` / `recommendation_result`） |
| 规则推荐落库 | `manage.py refresh_user_recommendations`、周任务 `run_weekly_recommendations` / 内部 `weekly-run` |
| 在线 AI 推荐（不落库） | `POST /api/recommend`（可选 OpenAI，失败回退规则） |
| 自动点餐 | 内部任务 `POST .../internal/jobs/auto-order/run` 等 |
| 计数器（模板遗留） | `GET/POST /api/count` |

**说明**：`refresh_user_recommendations` 与落库的推荐批次使用 **`wxcloudrun/recommendation_scoring` 规则打分**，**不经过** `POST /api/recommend` 的大模型链路。

---

## 环境要求

- **Python**：建议 3.8+（与云托管镜像一致即可）
- **MySQL**：5.7+ / 8.0（`utf8mb4`）
- 依赖安装：`pip install -r requirements.txt`

---

## 快速开始（本地）

1. 配置环境变量（至少 MySQL，见下文「环境变量」）。
2. 数据库迁移：

```bash
python3 manage.py migrate
```

3. 启动开发服务：

```bash
python3 manage.py runserver 0.0.0.0:8000
```

远端库若缺表/缺列，可使用（详见命令文档）：

- `python3 manage.py sync_missing_tables`
- `python3 manage.py sync_missing_columns`

---

## 后端命令文档

运维、排障、美餐 client 配置、推荐重算等：**[`docs/backend-commands.md`](docs/backend-commands.md)**

---

## 微信云托管部署

- [微信云托管快速开始](https://developers.weixin.qq.com/miniprogram/dev/wxcloudrun/src/basic/guide.html)
- [本地调试](https://developers.weixin.qq.com/miniprogram/dev/wxcloudrun/src/guide/debug/)
- [实时开发](https://developers.weixin.qq.com/miniprogram/dev/wxcloudrun/src/guide/debug/dev.html)
- [构建加速](https://developers.weixin.qq.com/miniprogram/dev/wxcloudrun/src/scene/build/speed.html)

---

## 目录结构（摘要）

```
.
├── manage.py
├── requirements.txt
├── Dockerfile
├── docs/
│   └── backend-commands.md      # 后端命令与排障
└── wxcloudrun/
    ├── settings.py              # 含 MySQL、OPENAI、INTERNAL_JOB、美餐等配置
    ├── urls.py                  # 路由（含 V1）
    ├── views.py                 # 计数器、POST /api/recommend
    ├── v1_views.py              # V1 用户偏好、美餐会话、菜单同步、每日推荐、内部任务
    ├── models.py
    ├── recommendation_service.py
    ├── recommendation_scoring.py
    ├── menu_sync_service.py
    ├── meican_menu_snapshot.py  # 美餐 Forward 拉菜单（可选）
    └── meican_client_config.py  # 美餐 client 库表解析
```

---

## HTTP API 一览

前缀均为服务根域名，例如 `https://<你的云托管域名>`。

### V1（小程序 / 推荐服务）

| 方法 | 路径 | 说明 |
|------|------|------|
| PUT | `/api/v1/users/<user_id>/preferences` | 用户饮食偏好 |
| GET/PUT | `/api/v1/users/<user_id>/auto-order-config` | 自动点餐配置 |
| PUT | `/api/v1/users/<user_id>/meican-session` | 美餐登录后上报 token、namespace |
| POST | `/api/v1/users/<user_id>/menu/week-sync` | 周菜单同步（**POST**，勿用 GET 调试） |
| GET | `/api/v1/users/<user_id>/recommendations/daily?date=YYYY-MM-DD&namespace=...` | 当日推荐 |
| POST | `/api/v1/users/<user_id>/orders` | 手动下单 |
| POST | `/api/v1/internal/jobs/auto-order/run` | 内部：自动点餐任务（需 `X-Internal-Token`） |
| GET | `/api/v1/internal/jobs/auto-order/<job_id>` | 内部：任务状态 |
| POST | `/api/v1/internal/jobs/recommendations/weekly-run` | 内部：周推荐任务（需 `X-Internal-Token`） |

### 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/count` | 计数器读 |
| POST | `/api/count` | 计数器写（模板示例） |
| POST | `/api/recommend` | 在线 Top3 推荐（可选 AI + 规则兜底） |

`POST /api/recommend` 请求体与环境变量说明见下文示例（与历史 README 一致）。

#### `POST /api/recommend` 请求示例

```json
{
  "personalPreference": {
    "是否吃辣": "否",
    "是否清真": "否",
    "是否正在减脂": "是",
    "喜欢吃粉面": "是",
    "喜欢吃饭": "否",
    "其他补充": "我不能吃葱，喜欢吃香菜"
  },
  "menuList": [
    {
      "id": 308630423,
      "name": "新鲜瘦肉汤米粉(中融专供)",
      "restaurant": {
        "name": "潮品鲜(荷光路店)",
        "available": true
      }
    }
  ]
}
```

#### `POST /api/recommend` 环境变量

- `OPENAI_API_KEY`：可选；不配则走规则排序
- `OPENAI_BASE_URL`：可选
- `OPENAI_MODEL`：可选
- `OPENAI_TIMEOUT_SECONDS`：可选

---

## 环境变量（核心）

### MySQL（必填）

- `MYSQL_ADDRESS`：形如 `host:3306`
- `MYSQL_USERNAME`
- `MYSQL_PASSWORD`
- `MYSQL_DATABASE`

### 内部任务鉴权（可选）

- `INTERNAL_JOB_TOKEN`：与请求头 `X-Internal-Token` 一致时，才允许调用内部 job 接口

### 周推荐开关（可选）

- `RECOMMENDATION_WEEKLY_REQUIRE_SUNDAY`：`1` / `true` / `yes` 时仅周日允许 `weekly-run`（防误触）

### 美餐 Forward（服务端拉菜单 / 换票，可选）

- 优先：**库表** `meican_client_config`（`manage.py set_meican_client_config`）
- 兜底：`MEICAN_FORWARD_CLIENT_ID` / `MEICAN_FORWARD_CLIENT_SECRET`，及可选 `MEICAN_GRAPHQL_*`、`MEICAN_FORWARD_USER_AGENT`、`MEICAN_FORWARD_REFERER`、`MEICAN_X_MC_DEVICE`

详见 `wxcloudrun/settings.py` 与 [`docs/backend-commands.md`](docs/backend-commands.md)。

### 自动点餐截止时间（可选）

- `AUTO_ORDER_LUNCH_DEADLINE`（默认 `10:30`）
- `AUTO_ORDER_DINNER_DEADLINE`（默认 `16:30`）

---

## License

[MIT](./LICENSE)
