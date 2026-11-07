# 自检记录 API（dev）

面向前端对接的接口说明。数据表为 `t_migration_check`，仅用于验证发布链路。

## 基本信息

| 项 | 值 |
| --- | --- |
| Base URL | `https://x4oneny2hi.execute-api.ap-northeast-1.amazonaws.com/dev` |
| 请求格式 | `Content-Type: application/json`（GET/DELETE 无请求体） |
| 响应格式 | JSON，UTF-8，字段原样中文，不转义 |
| 认证 | **无**（当前接口未接入鉴权） |
| CORS | 已允许 `https://d193yl670roc4e.cloudfront.net` |

> 目前只有 dev 环境可用。staging / prod 尚未部署，不要写死 URL，建议放在前端环境变量里。

## 数据模型

一条记录（`Item`）的字段：

| 字段 | 类型 | 创建 | 更新 | 说明 |
| --- | --- | --- | --- | --- |
| `id` | integer | — | — | 自增主键，只读 |
| `biz_no` | string(64) | 必填 | **不可改** | 业务编号，全局唯一 |
| `name` | string(128) | 必填 | 可改 | 名称，首尾空白会被去掉 |
| `status` | integer | 可选 | 可改 | `0`=待处理，`1`=处理中，`2`=已完成；默认 `0` |
| `amount` | string | 可选 | 可改 | 金额，`DECIMAL(18,2)`。**响应里是字符串**，默认 `"0.00"` |
| `payload` | any JSON | 可选 | 可改 | 任意可序列化 JSON 值，可为 `null` |
| `remark` | string(255) | 可选 | 可改 | 备注，可为 `null` |
| `is_deleted` | integer | — | — | 软删除标记，接口返回的行恒为 `0` |
| `created_at` | string | — | — | 创建时间，`YYYY-MM-DD HH:MM:SS`，无时区后缀 |
| `updated_at` | string | — | — | 更新时间，格式同上 |

前端可以直接用：

```ts
export interface Item {
  id: number;
  biz_no: string;
  name: string;
  status: 0 | 1 | 2;
  amount: string;       // 字符串，避免精度丢失
  payload: unknown;     // 任意 JSON
  remark: string | null;
  is_deleted: 0 | 1;
  created_at: string;
  updated_at: string;
}
```

## 通用响应

单条成功：

```json
{ "data": { "id": 1, "biz_no": "...", "...": "..." } }
```

列表成功：

```json
{
  "data": [ { "...": "..." } ],
  "meta": { "total": 4, "limit": 20, "offset": 0 }
}
```

失败：

```json
{ "code": "VALIDATION_ERROR", "message": "name 不能为空" }
```

| HTTP | `code` | 含义 |
| --- | --- | --- |
| 400 | `VALIDATION_ERROR` | 参数不合法，`message` 可直接提示用户 |
| 404 | `NOT_FOUND` | 记录不存在或已被删除 |
| 500 | `INTERNAL_ERROR` | 服务端异常，`message` 固定为「服务器内部错误」 |
| 503 | `SERVICE_UNAVAILABLE` | 下游依赖暂不可用（预留） |

> 重复的 `biz_no` 目前会返回 **500 `INTERNAL_ERROR`**，而不是 409。前端如果需要精确提示，
> 建议同时校验本地唯一性；后端后续会把唯一键冲突映射为 409 `CONFLICT`。

## 接口

### 1. 新增 `POST /items`

只有 `biz_no`、`name` 必填；未提供的可选字段使用数据库默认值。

```http
POST /dev/items
Content-Type: application/json

{
  "biz_no": "BIZ-0001",
  "name": "示例记录",
  "status": 1,
  "amount": "12.50",
  "payload": { "source": "web" },
  "remark": "可选备注"
}
```

成功 `201`：

```json
{
  "data": {
    "id": 6,
    "biz_no": "BIZ-0001",
    "name": "示例记录",
    "status": 1,
    "amount": "12.50",
    "payload": { "source": "web" },
    "remark": "可选备注",
    "is_deleted": 0,
    "created_at": "2026-11-07 05:30:00",
    "updated_at": "2026-11-07 05:30:00"
  }
}
```

校验规则：

- `biz_no` / `name`：非空字符串，去首尾空白，长度分别 ≤ 64 / ≤ 128
- `status`：整数，只能是 `0`、`1`、`2`（字符串 `"1"` 不合法）
- `amount`：数字或数字字符串，绝对值 ≤ `9999999999999999.99`
- `payload`：任意可序列化为 JSON 的值
- `remark`：字符串 ≤ 255，或 `null`
- 请求体上限 1 MB；不认识的字段会被忽略

### 2. 分页列表 `GET /items`

```http
GET /dev/items?limit=20&offset=0
```

| 查询参数 | 默认 | 范围 | 说明 |
| --- | --- | --- | --- |
| `limit` | `20` | 1–100 | 每页条数 |
| `offset` | `0` | 0–1000000 | 跳过条数 |

成功 `200`，按 `id` 倒序返回未删除的记录：

```json
{
  "data": [ { "id": 5, "...": "..." }, { "id": 3, "...": "..." } ],
  "meta": { "total": 4, "limit": 20, "offset": 0 }
}
```

`meta.total` 是未删除记录总数，翻页请以它为准。

### 3. 单条查询 `GET /items/{id}`

```http
GET /dev/items/1
```

成功 `200`：`{ "data": { ... } }`

- `id` 必须是正整数，否则 `400 VALIDATION_ERROR`
- 不存在或已软删除：`404 NOT_FOUND`

### 4. 部分更新 `PUT /items/{id}`

只提交要修改的字段，未提交的字段保持不变。

```http
PUT /dev/items/1
Content-Type: application/json

{ "status": 2, "remark": "已完成" }
```

成功 `200`：`{ "data": { ...更新后的完整记录... } }`

- 可更新字段：`name`、`status`、`amount`、`payload`、`remark`
- `biz_no` 与 `id` 不可修改，传了会被忽略
- 请求体为空对象或没有可更新字段：`400 VALIDATION_ERROR`
- 记录不存在或已删除：`404 NOT_FOUND`
- 值没变化时仍返回 `200`，不会误报 404

### 5. 删除 `DELETE /items/{id}`

软删除：记录留在库中，只是对读接口不可见。

```http
DELETE /dev/items/1
```

- 成功：`204 No Content`，**无响应体**
- 不存在或已删除：`404 NOT_FOUND`（重复删除第二次会返回 404）

## 前端注意事项

1. **金额用字符串传输**：`amount` 在响应里是 `"12.50"` 这样的字符串，不要直接转 `number` 再回传，否则可能丢精度。
2. **CORS 只允许 `https://d193yl670roc4e.cloudfront.net`**：允许的方法为 `GET/POST/PUT/DELETE/OPTIONS`，允许的请求头为 `content-type`。
   本地 `localhost` 或其他来源直接调用会被浏览器拦截；需要联调时把来源告诉后端加进 `CorsAllowOrigin` 参数。
3. **无鉴权**：任何人都能调用，dev 环境不要放敏感数据。
4. **删除是软删除**：列表和单条查询都看不到已删除记录；`total` 也不包含它们。
5. **时间字段是数据库原样字符串**，没有时区后缀，前端按服务端时区展示或自行转换。
6. 分页使用 `limit` + `offset`，不是游标分页；数据量增大后注意深分页性能。

## curl 示例

```bash
BASE=https://x4oneny2hi.execute-api.ap-northeast-1.amazonaws.com/dev

curl "$BASE/items?limit=10&offset=0"
curl "$BASE/items/1"
curl -X POST "$BASE/items" -H 'Content-Type: application/json' \
  -d '{"biz_no":"BIZ-0001","name":"示例","status":1,"amount":"12.50"}'
curl -X PUT "$BASE/items/1" -H 'Content-Type: application/json' \
  -d '{"status":2,"remark":"已完成"}'
curl -X DELETE "$BASE/items/1" -i
```
