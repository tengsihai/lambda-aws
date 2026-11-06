# lambda-aws

Python 编写的 AWS Lambda 增删改查后端：四个 Lambda 分别负责**新增、查询、更新、删除**，
数据存放在已部署好的 MySQL 中，数据库口令从 **AWS Secrets Manager** 获取——
代码、配置、流水线中不存在任何明文密钥。

- 运行时：**Python 3.13**
- 入口：API Gateway（HTTP API）关联四个 Lambda
- 依赖：**Lambda Layer**，四个函数共享
- CI/CD：GitHub Actions，通过 **OIDC 换取临时凭证**部署，无长期 AccessKey

> 表结构不在本工程内维护，由专门的 DB 工程负责（仓库中没有 `schema.sql`）。

---

## 一、目录结构

```
.
├── .github/workflows/ci-cd.yml   # CI/CD 流水线
├── layer/
│   └── requirements.txt          # 依赖层的依赖清单（部署时打包）
├── src/
│   ├── requirements.txt          # 函数包不含三方依赖（说明见文件内注释）
│   ├── common/                   # 四个函数共享的基础设施
│   │   ├── config.py             #   环境变量配置
│   │   ├── secrets.py            #   Secrets Manager 读取 + 进程内缓存
│   │   ├── db.py                 #   MySQL 连接与事务
│   │   ├── http.py               #   事件解析与响应封装
│   │   ├── errors.py             #   业务异常定义
│   │   └── logging_setup.py      #   日志初始化
│   ├── create/handler.py         # 新增
│   ├── read/handler.py           # 查询（单条 + 分页列表）
│   ├── update/handler.py         # 更新
│   └── delete/handler.py         # 删除
├── tests/                        # 单元测试（不访问真实网络与数据库）
├── template.yaml                 # AWS SAM 基础设施模板
├── pyproject.toml                # ruff / mypy / pytest 配置
├── requirements-dev.txt          # 本地开发依赖
└── Makefile                      # 常用命令
```

**打包方式**：`CodeUri: src/`，每个函数的包里包含 `handler + common`；
三方依赖（PyMySQL、cryptography）放在 `DependenciesLayer` 中，四个函数共享。
`boto3` 不打包——Lambda 运行时已自带。

---

## 二、API

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| POST | `/items` | create | 新增，返回 201 |
| GET | `/items` | read | 分页列表，支持 `?limit=&offset=` |
| GET | `/items/{id}` | read | 单条查询，不存在返回 404 |
| PUT | `/items/{id}` | update | 部分更新，返回 200 |
| DELETE | `/items/{id}` | delete | 删除，返回 204 |

统一响应结构：

```json
{ "data": { "...": "..." } }
{ "data": [], "meta": { "total": 0, "limit": 20, "offset": 0 } }
{ "code": "VALIDATION_ERROR", "message": "name 不能为空" }
```

`code` 取值：`VALIDATION_ERROR`(400)、`NOT_FOUND`(404)、`CONFLICT`(409)、
`INTERNAL_ERROR`(500)、`SERVICE_UNAVAILABLE`(503)。
未预期的异常只记录服务端日志，客户端看不到堆栈与 SQL。

---

## 三、分支模型与 CI/CD

| 分支 | 含义 | 是否部署 |
| --- | --- | --- |
| `dev` | 开发环境 | 合并后部署到 **dev** |
| `staging` | 预发环境 | 合并后部署到 **staging** |
| `prod` | 生产环境 | 合并后部署到 **prod** |
| `main` | **发布后归档** | 否，只跑测试 |

**触发时机**

- 从任意分支**合并进环境分支**（`push` 事件）→ 跑测试 → 部署到该分支对应的环境
- 面向环境分支 / `main` 的 **PR** → 只跑测试，合并前把关
- **手动触发**（`workflow_dispatch`）→ 跑测试 → 部署当前分支对应的环境

**流水线步骤**

1. `test`：`ruff check` → `ruff format --check` → `mypy` → `pytest`
2. `deploy`（仅环境分支且测试通过）：
   - 校验该环境的必需配置
   - `sam build` 构建依赖层与四个函数
   - OIDC 换取临时凭证
   - `sam deploy` 发布 **依赖层 + 四个 Lambda + API Gateway**
   - 输出 API 访问地址，并做一次冒烟测试验证 API 已关联到 Lambda

环境名与分支名一致，因此 `dev` 分支的改动一定只发到 `dev` 环境。

---

## 四、首次配置（只需一次）

### 1. 创建 GitHub 环境与变量

**Settings → Environments**，分别新建 `dev`、`staging`、`prod`，
然后在每个环境下配置 **Variables**（按环境各配一份）：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `AWS_ROLE_ARN` | 是 | 该环境用于 OIDC 换取凭证的 IAM 角色 ARN |
| `DB_HOST` | 是 | MySQL 主机名或 IP |
| `DB_NAME` | 是 | 数据库名 |
| `DB_SECRET_ARN` | 是 | Secrets Manager 中数据库凭据的 ARN |
| `AWS_REGION` | 否 | 默认 `ap-southeast-1` |
| `AWS_STACK_NAME` | 否 | 默认 `lambda-aws-<环境名>` |
| `PROJECT_NAME` | 否 | 默认 `lambda-aws` |
| `DB_PORT` | 否 | 默认 `3306` |
| `DB_USER` | 否 | 留空则使用密钥中的 `username` 字段 |
| `VPC_SUBNET_IDS` | 视情况 | 逗号分隔的子网 ID；数据库在私有子网时必须填 |
| `VPC_SECURITY_GROUP_IDS` | 视情况 | 逗号分隔的安全组 ID；同上 |

> 变量是**非敏感配置**，放 Variables 即可。若某环境的变量只对特定分支可见，
> 也可以直接配置在环境级别，GitHub 会优先取环境级变量。

### 2. 创建 OIDC 角色

先建身份提供者（每个账号一次）：

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```

再为每个环境建角色，**信任策略**限制到本仓库的对应环境：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<账号ID>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:tengsihai/lambda-aws:environment:dev"
        }
      }
    }
  ]
}
```

> `sub` 中的 `environment:dev` 对应 GitHub 环境名，`staging`、`prod` 各改一处。
> 若不使用 GitHub 环境保护，可写成 `repo:tengsihai/lambda-aws:*`（范围更宽，不推荐）。

**权限策略**基线（按需收紧）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudFormation",
      "Effect": "Allow",
      "Action": ["cloudformation:*"],
      "Resource": [
        "arn:aws:cloudformation:*:<账号ID>:stack/lambda-aws-*",
        "arn:aws:cloudformation:*:<账号ID>:changeSet/*"
      ]
    },
    {
      "Sid": "SamArtifactBucket",
      "Effect": "Allow",
      "Action": ["s3:*"],
      "Resource": [
        "arn:aws:s3:::aws-sam-cli-managed-*",
        "arn:aws:s3:::aws-sam-cli-managed-*/*"
      ]
    },
    {
      "Sid": "Deployables",
      "Effect": "Allow",
      "Action": [
        "lambda:*",
        "apigateway:*",
        "logs:*",
        "ec2:CreateNetworkInterface",
        "ec2:DeleteNetworkInterface",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups",
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:GetRole",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:TagRole",
        "iam:UntagRole",
        "iam:PassRole"
      ],
      "Resource": "*"
    }
  ]
}
```

> `secretsmanager:GetSecretValue` **不需要**给部署角色——它由 CloudFormation 写进
> Lambda 的执行角色里，只有函数运行时需要。

### 3. 配置 Secrets Manager

密钥内容必须是 JSON，至少包含 `password`：

```json
{ "username": "app_user", "password": "<口令>" }
```

口令的轮换由该密钥负责，代码只按 ARN 读取并缓存 5 分钟
（见 `src/common/secrets.py` 的 `DEFAULT_CACHE_TTL_SECONDS`）。

---

## 五、环境变量

四个函数共享同一组环境变量，由 CloudFormation 参数注入：

| 变量 | 来源 | 说明 |
| --- | --- | --- |
| `DB_HOST` / `DB_PORT` / `DB_NAME` | 模板参数 | 非敏感连接信息 |
| `DB_USER` | 模板参数 | 可选，留空则用密钥中的 `username` |
| `DB_SECRET_ARN` | 模板参数 | **只存 ARN，不存口令** |
| `TABLE_NAME` | 模板参数 | 默认 `items`，会校验为合法 SQL 标识符 |
| `LOG_LEVEL` | 模板参数 | 默认 `INFO` |
| `STAGE` | 模板参数 | 环境名 |

代码中**不存在** `DB_PASSWORD` 之类的变量——口令只能来自 Secrets Manager。

---

## 六、本地开发

```bash
# 依赖用 Python 3.13，与 Lambda 运行时一致
python3.13 -m venv .venv
make install        # 安装依赖

make lint           # ruff check
make format         # ruff format + 自动修复
make typecheck      # mypy src tests
make test           # pytest + 覆盖率
make check          # 以上全部
```

测试全部在内存中完成，数据库与 Secrets Manager 均被替换为替身，
不需要网络，也不会读取任何真实凭据。

本地如果要连真实数据库调试，需要能读到对应 Secrets Manager 密钥的 AWS 凭证；
**不要**为了省事把口令写进任何文件。

---

## 七、常见问题

**部署报缺少变量**
→ 到 `Settings → Environments → <环境> → Variables` 补齐 `AWS_ROLE_ARN`、
`DB_HOST`、`DB_NAME`、`DB_SECRET_ARN`。

**`AssumeRoleWithWebIdentity` 被拒绝**
→ 检查角色信任策略里的 `sub` 是否写成了
`repo:tengsihai/lambda-aws:environment:<环境名>`，以及身份提供者 ARN 是否正确。

**冒烟测试提示「请求没有到达 Lambda」**
→ 多半是 API Gateway 的路由或集成没建好，查看 CloudFormation 栈事件。

**冒烟测试返回 200 以外但路由正常**
→ 流水线只给出 warning。表结构由 DB 工程维护，本工程不做建表。
