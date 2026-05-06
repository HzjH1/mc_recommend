# 后端命令文档

本文档整理本项目常用的 Django 后端命令（`manage.py`），用于本地调试、线上排障和数据维护。

## 运行前准备

- 在项目根目录执行（包含 `manage.py` 的目录）
- 先安装依赖：`pip install -r requirements.txt`
- 关键环境变量（至少）：
  - `MYSQL_ADDRESS`
  - `MYSQL_USERNAME`
  - `MYSQL_PASSWORD`
  - `MYSQL_DATABASE`

## 数据库结构与补齐

### 执行标准迁移

```bash
python3 manage.py migrate
```

### 补建缺失表（远端库常用）

```bash
python3 manage.py sync_missing_tables
```

- 按模型顺序创建缺失表，不会删除已有表
- 输出 `Created tables` 和 `Skipped existing tables`

### 补齐缺失列（远端库常用）

```bash
python3 manage.py sync_missing_columns
```

- 只补缺失列，不覆盖已存在列
- 输出 `Added columns` 和 `Skipped existing columns`

## 菜单快照相关

### 从 JSON 导入菜单周快照

```bash
python3 manage.py import_menu_week_json --file ./tmp/week-menu.json --namespace 439456
```

参数说明：

- `--file`：必填，JSON 文件路径
- `--namespace`：可选，覆盖文件中的 namespace

输入 JSON 结构与接口 `POST /api/v1/users/<user_id>/menu/week-sync` 一致。

## 推荐生成相关

### 单用户按日重算推荐

```bash
python3 manage.py refresh_user_recommendations \
  --user-id 2176800661201728 \
  --date 2026-04-13 \
  --namespace 439456 \
  --meal-slot ALL \
  --top-n 3
```

可选参数：

- `--meal-slot ALL|LUNCH|DINNER`（默认 `ALL`）
- `--freeze`（将批次写为 `FROZEN`）
- `--no-meican-sync`（不自动从美餐补菜单，仅使用已有快照）

注意：

- 此命令使用规则引擎打分（`recommendation_scoring`），`usesAiLlm=false`
- 终端会打印 `hint` 便于排障（如无快照、无可用菜品、候选数等）

### 周任务推荐（建议周日触发）

```bash
python3 manage.py run_weekly_recommendations --workdays 5 --top-n 3
```

可选参数：

- `--week-start YYYY-MM-DD`：手动指定周一
- `--freeze`
- `--workdays`（默认 5）
- `--user-id`（仅跑指定用户）

默认逻辑与线上周任务一致：

- 若当天是周日：从下周一开始生成 5 个工作日推荐
- 否则：从本周一开始

## 美餐客户端配置

### 写入 `meican_client_config`（key=default）

```bash
python3 manage.py set_meican_client_config \
  --forward-id 'xxx' \
  --forward-secret 'xxx' \
  --graphql-id 'xxx' \
  --graphql-secret 'xxx' \
  --graphql-app 'meican/web-pc (prod;4.90.1;sys;main)'
```

可选补充：

- `--forward-base-url`
- `--forward-user-agent`
- `--forward-referer`
- `--mc-device`

说明：

- 优先使用库表 `meican_client_config`
- 若 Forward 凭证为空，会回退到 GraphQL 凭证

## 数据脱敏

### 脱敏已入库美餐账号信息

```bash
python3 manage.py mask_meican_account_pii
```

- 脱敏字段：`meican_username`、`meican_email`
- 输出 `masked records: N`

## 常见排障

### 1) `calendarHttpStatus=401`

说明美餐 token 失效。需让小程序重新登录，并调用：

- `PUT /api/v1/users/<user_id>/meican-session`

更新 `access_token` / `refresh_token` 后再重试推荐或菜单同步。

### 2) 同步菜单后推荐“变少/消失”

`menu/week-sync` 会按 `dish_id` 做增量同步：

- 相同 `dish_id`：原地更新，保留 `menu_item` 主键
- 消失的 `dish_id`：删除对应 `menu_item`（其推荐结果会级联删除）

若菜单变化较大，建议同步后重跑 `refresh_user_recommendations`。

### 3) 周日日期算错

不要手写 `mon = today - weekday()` 去算周起始；周日会落到上周。

请直接用：

- `run_weekly_recommendations`（内置周日=下周逻辑）
- 或业务函数 `resolve_sync_work_dates()`（在 `meican_menu_snapshot.py`）

