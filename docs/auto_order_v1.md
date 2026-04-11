# 推荐服务 + 自动点餐任务中心（V1）

## 统一响应

```json
{
  "code": 0,
  "message": "ok",
  "requestId": "trace-xxx",
  "data": {}
}
```

## 已落地接口

- `PUT /api/v1/users/{userId}/preferences`
- `PUT /api/v1/users/{userId}/auto-order-config`
- `GET /api/v1/users/{userId}/recommendations/daily?date=YYYY-MM-DD`
- `POST /api/v1/users/{userId}/orders`
- `POST /api/v1/internal/jobs/auto-order/run`
- `GET /api/v1/internal/jobs/auto-order/{jobId}`
- `PUT /api/v1/users/{userId}/meican-session`（小程序登录后同步 token，供云端主动换票）
- `POST /api/v1/internal/meican/users/{userId}/ensure-token`（内部：按需或强制 refresh）
- `POST /api/v1/internal/meican/tokens/refresh-due`（内部：批量刷新临近过期 token，供定时任务）
- `POST /api/v1/users/{userId}/sync/meican-week`（上报本周工作日各餐期菜单，服务端落库并生成推荐）
- `GET /api/v1/users/{userId}/recommendations/week?weekStart=YYYY-MM-DD&namespace=`（本周工作日推荐 Top3，含理由与下单参数）

> 代码位置：`wxcloudrun/v1_views.py`、`wxcloudrun/urls.py`、`wxcloudrun/meican_oauth.py`、`wxcloudrun/recommendation_engine.py`

### 小程序 `userId` 约定

当前小程序使用 **手机号稳定哈希** 映射为路径中的 `{userId}`（见小程序 `utils/recommendClient.js` 中 `stableUserIdFromPhone`）。后续可改为云开发下发的数字用户主键，只要与 `user_account.id` 一致即可。

## 数据模型与表映射

以下 Django 模型与 MySQL 表一一对应（见 `wxcloudrun/models.py`）：

- `UserAccount` -> `user_account`
- `UserMeicanAccount` -> `user_meican_account`
- `UserPreference` -> `user_preference`
- `AutoOrderConfig` -> `auto_order_config`
- `CorpAddress` -> `corp_address`
- `MenuSnapshot` -> `menu_snapshot`
- `MenuItem` -> `menu_item`
- `RecommendationBatch` -> `recommendation_batch`
- `RecommendationResult` -> `recommendation_result`
- `AutoOrderJob` -> `auto_order_job`
- `AutoOrderJobItem` -> `auto_order_job_item`
- `OrderRecord` -> `order_record`

## 关键约束落地点

- 幂等约束：
  - `order_record.uk_user_date_slot(user_id, date, meal_slot)`
  - `order_record.uk_idempotency(idempotency_key)`
- 推荐明细唯一：
  - `recommendation_result.uk_batch_user_rank(batch_id, user_id, rank_no)`
- 任务唯一：
  - `auto_order_job.uk_date_slot_trigger(date, meal_slot, trigger_type)`
  - `auto_order_job_item.uk_job_user(job_id, user_id)`
- 时区：
  - `settings.TIME_ZONE = Asia/Shanghai`
- 定时窗口：
  - `AUTO_ORDER_LUNCH_DEADLINE`（默认 `10:30`）
  - `AUTO_ORDER_DINNER_DEADLINE`（默认 `16:30`）
  - 超窗口触发内部任务时返回 `40902`

## 失败码（已接入/保留）

- `NO_DEFAULT_ADDRESS`
- `MENU_ITEM_UNAVAILABLE`
- `ORDER_ALREADY_EXISTS`
- `AUTO_ORDER_DISABLED`（预留）
- `MEICAN_API_ERROR`（预留）
- `TOKEN_REFRESH_FAILED`（OAuth 失败时见 `50201` / `40020`）

## 美餐 token 主动刷新（与小程序对齐）

环境变量（与小程序里 forward 的 `client_id` / `client_secret` 一致）：

- `MEICAN_CLIENT_ID`
- `MEICAN_CLIENT_SECRET`
- `MEICAN_TOKEN_REFRESH_SKEW_SECONDS`（默认 `300`：距过期前多少秒即视为需刷新）
- `MEICAN_TOKEN_DEFAULT_TTL_SECONDS`（默认 `3600`：oauth 未返回 `expires_in` 时写入 `token_expire_at` 的兜底秒数）

### `PUT /api/v1/users/{userId}/meican-session`

请求体 JSON（字段名可与下划线形式二选一）：

```json
{
  "meicanUsername": "脱敏展示名或登录名",
  "accessToken": "...",
  "refreshToken": "...",
  "expiresIn": 3600,
  "meicanEmail": "",
  "accountNamespace": ""
}
```

响应 `data.tokenExpireAt` 为服务端推算的过期时间（ISO）。**响应不回传 token 明文。**

### `POST /api/v1/internal/meican/users/{userId}/ensure-token`

需请求头 `X-Internal-Token`（与 `INTERNAL_JOB_TOKEN` 一致，未配置则不校验）。

请求体可选：

```json
{ "force": false, "skewSeconds": 300 }
```

- `force: true`：无视临近过期判断，始终用 `refresh_token` 换票。
- `force: false`：仅当将在 `skewSeconds`（缺省用环境变量）内过期时才换票。

### `POST /api/v1/internal/meican/tokens/refresh-due`

请求体可选：

```json
{ "withinSeconds": 300, "limit": 50 }
```

选出「已有 refresh_token 且 access 空 / 无过期时间 / 在 withinSeconds 内过期」的用户，逐个调用与 `ensure-token` 相同的换票逻辑；`items` 中逐条返回成功或 `error` 文案（不含 token）。

### `POST /api/v1/users/{userId}/sync/meican-week`

请求体：

```json
{
  "namespace": "美餐 corp namespace",
  "days": [
    {
      "date": "2026-04-07",
      "slots": {
        "LUNCH": {
          "tabUniqueId": "...",
          "targetTime": "2026-04-07 10:30",
          "dishes": [
            {
              "dishId": "123",
              "dishName": "番茄炒蛋",
              "restaurantName": "食堂",
              "priceCent": 1200,
              "tabUniqueId": "...",
              "targetTime": "2026-04-07 10:30",
              "corpNamespace": "..."
            }
          ]
        },
        "DINNER": { "tabUniqueId": "", "targetTime": "", "dishes": [] }
      }
    }
  ]
}
```

`slots` 的键支持 `LUNCH`/`DINNER` 或 `morning`/`afternoon`（会映射到 LUNCH/DINNER）。服务端写入 `menu_snapshot` / `menu_item`，按 `user_preference` 规则打分后写入 `recommendation_batch` + `recommendation_result`（每用户每餐期 Top3）。

### `GET /api/v1/users/{userId}/recommendations/week`

- `weekStart`：可选，所在周的 **周一** 日期；缺省为「今天」所在周周一。
- `namespace`：可选；缺省时尝试使用 `user_meican_account.account_namespace`。

响应 `data.days` 为长度 5 的数组（周一至周五），每日含 `LUNCH.items`、`DINNER.items`（每项含 `reason`、`tabUniqueId`、`targetTime` 等）。

### `GET .../recommendations/daily` 行为调整

按 **当前用户在该日该餐期下存在推荐结果** 的最新 `recommendation_batch` 读取（避免读到无当前用户数据的空批次）。

## 说明

- 当前 `internal jobs run` 接口会创建任务和任务项，作为执行器入口；真实下单调用美餐 API 的执行逻辑建议在异步 worker 中实现，并回写 `auto_order_job_item` 与 `order_record`。
- `access_token/refresh_token` 字段已拆出独立表，建议在接入美餐时使用 KMS/应用层加密后入库（V1 模型已预留字段）。
