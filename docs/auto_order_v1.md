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

> 代码位置：`wxcloudrun/v1_views.py`、`wxcloudrun/urls.py`

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
- `TOKEN_REFRESH_FAILED`（预留）

## 说明

- 当前 `internal jobs run` 接口会创建任务和任务项，作为执行器入口；真实下单调用美餐 API 的执行逻辑建议在异步 worker 中实现，并回写 `auto_order_job_item` 与 `order_record`。
- `access_token/refresh_token` 字段已拆出独立表，建议在接入美餐时使用 KMS/应用层加密后入库（V1 模型已预留字段）。
