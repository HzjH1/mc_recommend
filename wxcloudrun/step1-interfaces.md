# 美餐接口清单（第1步）

抓取时间：2026-04-09 20:45-20:48 CST

说明：
- 以下为本次“登录、浏览餐厅菜品、点餐、取消餐、查看我的信息、查看今日餐品数据”过程中确认触发的核心业务接口。
- 已对 `client_id`、`client_secret`、手机号、验证码、token、支付单号等敏感值做脱敏描述。
- 原始抓包日志在 `/Users/huangzhijie/meican-capture/logs/meican-network-log.jsonl`，其中包含敏感信息，不建议直接外发。

## 1. 登录相关

### 1.1 获取手机验证码
- `POST https://gateway.meican.com/graphql?op=GetPhoneVerificationCode`
- GraphQL Operation: `GetPhoneVerificationCode`
- 关键入参：

```json
{
  "operationName": "GetPhoneVerificationCode",
  "variables": {
    "input": {
      "phone": "<手机号>",
      "idpType": "UserCenterIDP"
    }
  }
}
```

### 1.2 验证码登录
- `POST https://gateway.meican.com/graphql?op=LoginByAuthWay`
- GraphQL Operation: `LoginByAuthWay`
- 关键入参：

```json
{
  "operationName": "LoginByAuthWay",
  "variables": {
    "input": {
      "authMethod": "PhoneVerificationCode",
      "phone": "<手机号>",
      "verificationCode": "<验证码>",
      "createAccountIfNotExist": true,
      "createAccountScene": "UserSelfRegistration"
    }
  }
}
```

### 1.3 选择账号并换取 token
- `POST https://gateway.meican.com/graphql?op=ChooseAccountLogin`
- GraphQL Operation: `ChooseAccountLogin`
- 关键入参：

```json
{
  "operationName": "ChooseAccountLogin",
  "variables": {
    "input": {
      "ticket": "<ticket>",
      "snowflakeId": "<userSnowflakeId>",
      "signature": "<signature>"
    }
  }
}
```

### 1.4 Token 刷新
- `POST https://www.meican.com/forward/api/v2.1/oauth/token`
- 关键入参：

```text
grant_type=refresh_token&refresh_token=<refresh_token>
```

## 2. 我的信息

### 2.1 账户信息
- `GET https://www.meican.com/forward/api/v2.1/accounts/show`
- 用途：获取用户名、邮箱、手机号验证状态、企业列表、用户类型等

### 2.2 账户入口信息
- `POST https://www.meican.com/forward/api/v2.1/accounts/entrance`
- 用途：进入个人账户相关页面时触发的入口聚合接口

### 2.3 真实姓名
- `GET https://www.meican.com/forward/api/v2.1/client/getrealname`

### 2.4 支付账户与设置
- `GET https://www.meican.com/forward/api/v3.0/paymentadapter/user/account/list?includeCheckout=true`
- `GET https://www.meican.com/forward/api/v3.0/paymentadapter/user/account/activity/title`
- `GET https://www.meican.com/forward/api/v3.0/paymentadapter/user/setting/show`
- `GET https://www.meican.com/forward/api/v3.0/paymentadapter/user/main/transaction/list?typeList=Pay,Refund&lineLimit=20`

## 3. 今日餐品与日历数据

### 3.1 今日/指定日期餐期列表
- `GET https://www.meican.com/forward/api/v2.1/calendarItems/list?withOrderDetail=false&beginDate=<yyyy-MM-dd>&endDate=<yyyy-MM-dd>`
- 用途：查询某一天的午餐/晚餐餐期、是否可下单、是否已有订单

### 3.2 日历全量数据
- `GET https://www.meican.com/forward/api/v2.1/calendarItems/all?withOrderDetail=false&beginDate=<yyyy-MM-dd>&endDate=<yyyy-MM-dd>`

### 3.3 餐期状态检查
- `GET https://www.meican.com/forward/api/v2.1/calendarItems/checkStatus?date=<yyyy-MM-dd>`

### 3.4 企业餐配置信息
- `GET https://www.meican.com/forward/api/v2.1/corps/show?namespace=<corpNamespace>`
- 用途：返回企业地址、取餐点、开放时间、是否支持餐柜、支付方式等

## 4. 浏览餐厅和菜品

### 4.1 餐厅列表
- `GET https://www.meican.com/forward/api/v2.1/restaurants/list?tabUniqueId=<tabUniqueId>&targetTime=<yyyy-MM-dd HH:mm>`
- 用途：获取某餐期下可选餐厅列表

### 4.2 推荐菜品列表
- `GET https://www.meican.com/forward/api/v2.1/recommendations/dishes?tabUniqueId=<tabUniqueId>&targetTime=<yyyy-MM-dd HH:mm>`
- 用途：获取推荐菜、常点菜

### 4.3 收藏夹
- `GET https://www.meican.com/forward/api/v2.1/favourite/all`

### 4.4 附近餐厅
- `GET https://www.meican.com/forward/api/v2.1/card/getnearrestaurant`

## 5. 购物车与下单

### 5.1 查询预下单购物车
- `POST https://www.meican.com/forward/api/preorder/cart/query`
- 关键入参：

```text
tabUUID=<tabUniqueId>&closeTime=<yyyy-MM-dd HH:mm>
```

### 5.2 更新预下单购物车
- `POST https://www.meican.com/forward/api/preorder/cart/update`
- 关键入参示例：

```json
{
  "<tabUniqueId>/<targetTime>": {
    "dishes": [
      {
        "corpRestaurantId": "<restaurantId>",
        "count": 1,
        "name": "<dishName>",
        "priceInCent": 1800,
        "revisionId": "<dishRevisionId>"
      }
    ],
    "corpName": "<corpName>",
    "tabUUID": "<tabUniqueId>",
    "tabName": "<tabName>",
    "operativeDate": "<yyyy-MM-dd>"
  }
}
```

### 5.3 正式提交订单
- `POST https://www.meican.com/forward/api/v2.1/orders/add`
- 关键入参：

```text
tabUniqueId=<tabUniqueId>&order=[{"count":1,"dishId":<dishId>}]&remarks=[{"dishId":"<dishId>","remark":""}]&targetTime=<yyyy-MM-dd HH:mm>&userAddressUniqueId=<addressId>&corpAddressUniqueId=<addressId>&corpAddressRemark=
```

### 5.4 支付订单
- `POST https://meican-pay-checkout-bff.meican.com/api/v2/payment-slips/pay`
- 关键入参：

```json
{
  "themeName": "default",
  "paymentSlipId": "<paymentSlipId>"
}
```

## 6. 取消餐与订单详情

### 6.1 取消订单
- `POST https://www.meican.com/forward/api/v2.1/orders/delete`
- 关键入参：

```text
uniqueId=<orderUniqueId>&type=CORP_ORDER&restoreCart=false
```

### 6.2 餐柜订单详情
- `GET https://www.meican.com/forward/api/v2.1/orders/closetShow?uniqueId=<orderUniqueId>`

### 6.3 团餐订单详情
- `GET https://www.meican.com/forward/api/gateway/group-meals/v1/order/<orderUniqueId>`

### 6.4 历史订单
- `GET https://www.meican.com/forward/api/v2.1/orders/history`

## 7. 其他伴随接口

- `GET https://www.meican.com/forward/api/v2.1/cafeteria/myCafeteria`
- `GET https://www.meican.com/forward/api/v2.1/corpmembers/dinnerin`
- `GET https://www.meican.com/forward/api/v2.1/corpNotice/list`
- `GET https://www.meican.com/forward/api/v2.1/card/mine?internalCode=true`
- `GET https://www.meican.com/forward/api/v2.1/electricCard/mine`
- `GET https://www.meican.com/forward/api/v2.1/electricCard/usableness`
- `GET https://www.meican.com/forward/api/v2.1/electricCard/apiKey?v=2`
- `GET https://www.meican.com/forward/api/v2.1/corpaddresses/getmulticorpaddress?namespace=<corpNamespace>`
- `GET https://www.meican.com/forward/api/v3.0/regulation/meican/v1/terms/check`
- `GET https://www.meican.com/forward/serverTime`

## 8. 当前结论

- 美餐前台核心业务并不全是 REST，也包含 `gateway.meican.com/graphql` 的登录链路。
- 点餐流程分成三段：`cart/update` -> `orders/add` -> `payment-slips/pay`。
- 取消餐核心接口是 `orders/delete`。
- “今日餐品数据”核心来源是 `calendarItems/list`、`corps/show`、`restaurants/list`、`recommendations/dishes`。
