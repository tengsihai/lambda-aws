# lambda-aws

Python 编写的 AWS Lambda 增删改查后端：四个 Lambda 分别负责**新增、查询、更新、删除**，
数据存放在已部署好的 MySQL 中，数据库口令从 **AWS Secrets Manager** 获取——
代码、配置、流水线中不存在任何明文密钥。

- 运行时：**Python 3.13**
- 入口：API Gateway（HTTP API）关联四个 Lambda
- 依赖：**Lambda Layer**，四个函数共享
- CI/CD：GitHub Actions，通过 **OIDC 换取临时凭证**部署，无长期 AccessKey

> 表 `t_migration_check` 由 mysql-aws 工程创建与维护，本工程只读写（仓库中没有 `schema.sql`）。

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
│   │   ├── schema.py             #   表名（写死）与字段校验
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

## 二、表与 API

操作的表是 **`t_migration_check`**，由 `mysql-aws` 工程创建与维护，本工程只读写、不建表。
表名**直接写死在 [src/common/schema.py](src/common/schema.py)** 里，不走环境变量：
改表名必须改代码并走一次完整发布，避免某个环境的配置和真实表结构对不上。

| 字段 | 类型 | 约束 | 接口行为 |
| --- | --- | --- | --- |
| `id` | BIGINT UNSIGNED PK | 自增 | 路径参数 |
| `biz_no` | VARCHAR(64) | NOT NULL，唯一 | 新增必填，**创建后不可修改** |
| `name` | VARCHAR(128) | NOT NULL | 新增必填，可更新 |
| `status` | TINYINT UNSIGNED | DEFAULT 0，CHECK ∈ {0,1,2} | 可选，越界直接 400 |
| `amount` | DECIMAL(18,2) | DEFAULT 0.00 | 可选，按字符串传给驱动避免浮点误差 |
| `payload` | JSON | NULL | 可选，读写都做 JSON 编解码 |
| `remark` | VARCHAR(255) | NULL | 可选 |
| `is_deleted` | TINYINT(1) | DEFAULT 0 | **软删除标记**，见下 |
| `created_at` / `updated_at` | DATETIME | 列默认值 | 只读 |

**软删除**：`DELETE` 只把 `is_deleted` 置 1，不物理删除；
读接口一律带 `is_deleted = 0`，因此已删除的记录查询会得到 404。

| 方法 | 路径 | 函数 | 说明 |
| --- | --- | --- | --- |
| POST | `/items` | create | 新增，返回 201 |
| GET | `/items` | read | 分页列表，支持 `?limit=&offset=` |
| GET | `/items/{id}` | read | 单条查询，不存在或已删除返回 404 |
| PUT | `/items/{id}` | update | 部分更新，返回 200 |
| DELETE | `/items/{id}` | delete | 软删除，返回 204 |

新增示例：

```json
POST /items
{
  "biz_no": "BIZ-0001",
  "name": "示例",
  "status": 1,
  "amount": "12.50",
  "payload": { "source": "pipeline" },
  "remark": "可选"
}
```

只有 `biz_no` 和 `name` 必填；没提供的可选字段不写入，交给列默认值。

统一响应结构：

```json
{ "data": { "...": "..." } }
{ "data": [], "meta": { "total": 0, "limit": 20, "offset": 0 } }
{ "code": "VALIDATION_ERROR", "message": "biz_no 不能为空" }
```

`code` 取值：`VALIDATION_ERROR`(400)、`NOT_FOUND`(404)、`CONFLICT`(409)、
`INTERNAL_ERROR`(500)、`SERVICE_UNAVAILABLE`(503)。
未预期的异常只记录服务端日志，客户端看不到堆栈与 SQL。

---

## 三、分支模型与 CI/CD

与 `terraform-aws` 完全一致：**分支即环境，合并 PR 就是审批，`main` 只归档不部署。**

| 分支 | 含义 | 流水线行为 |
| --- | --- | --- |
| `dev` / `staging` / `prod` | 环境分支，分支名即环境名 | 合并进来 → 跑测试 → 部署到同名环境 |
| `main` | 发布后的归档分支 | 只跑测试，**不部署**，也不接收日常提交 |
| 功能分支（`feat/*`、`fix/*`…） | 开发用 | PR 到环境分支时跑测试，不部署 |

```
feat/xxx ──PR──► dev ──PR──► staging ──PR──► prod ──PR──► main（归档）
                  │          │              │
                  └ 部署 dev └ 部署 staging  └ 部署 prod
```

**触发时机**

- **PR（任意目标分支）** → 只跑测试，作为合并前的评审辅助
- **push 到 `main` / 环境分支** → 跑测试
- **合并进环境分支（push）** → 测试通过后部署到该分支对应的环境
- **没有 `workflow_dispatch`**：合并即审批，不提供手动指定环境部署的入口；
  失败后重跑对应的 job 即可

环境名与分支名一致，因此 `dev` 分支的改动一定只发到 `dev` 环境。
未接入的环境由仓库变量 `DEPLOY_ENVIRONMENTS` 控制（默认 `["dev"]`），
不在列表里的环境会跳过部署 job，不产生红叉。

**分支保护（建议）**：给 `dev` / `staging` / `prod` 配 ruleset——必须走 PR、
必须通过 `test` 检查、禁止直接 push。`prod` 再给 GitHub Environment 加
Required reviewers，作为合并之外的第二道审批。

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
| `TF_STATE_BUCKET` | 是 | terraform-aws 存远端 state 的 S3 桶名 |
| `AWS_REGION` | 否 | 默认 `ap-northeast-1` |
| `AWS_STACK_NAME` | 否 | 默认 `lambda-aws-<环境名>` |
| `PROJECT_NAME` | 否 | 默认 `lambda-aws` |
| `DB_PORT` | 否 | 默认 `3306` |
| `DB_USER` | 否 | 留空则使用密钥中的 `username` 字段 |
| `TF_STATE_KEY_PREFIX` | 否 | state key 前缀，默认 `network` |

> **VPC 子网与安全组没有变量**：它们在部署时从 terraform-aws 的 state 里读取，
> 见下节。手工配置只会造成两个工程不一致。

> **仓库级变量 `DEPLOY_ENVIRONMENTS`**（Settings → Secrets and variables → Actions →
> Variables）：决定哪些环境允许自动部署，默认 `["dev"]`。接入 staging / prod 时
> 改成 `["dev","staging","prod"]`。

> 变量是**非敏感配置**，放 Variables 即可。若某环境的变量只对特定分支可见，
> 也可以直接配置在环境级别，GitHub 会优先取环境级变量。

### 2. 依赖的外部工程

本工程不自己定义网络，也不自己维护表结构：

| 工程 | 提供什么 | 本工程怎么用 |
| --- | --- | --- |
| `terraform-aws` | VPC 私有子网、Lambda 安全组 | 部署时从其 S3 state 读 `outputs` |
| `mysql-aws` | 数据库表结构 | 表名已写死在 `common/schema.py`，表由它创建 |

**terraform-aws 现状**（state key：`<前缀>/<环境>/terraform.tfstate`）：

1. 根模块导出 `private_subnet_ids`（已有）；
2. `lambda` 安全组 dev 已建好：零入站，出站只有 TCP/3306 到 MySQL、
   TCP/443 到 Secrets Manager 的 Interface 端点，并已加入 RDS 的
   `allowed_security_group_ids`；
3. Secrets Manager Interface 端点 dev 已开启
   （`env.tfvars` 里 `interface_endpoint_services = ["secretsmanager"]`）；
4. 根模块已导出 `lambda_security_group_id`，且已 `apply` 进 state。

> 该 VPC **没有 NAT 网关**，私有子网里的 Lambda 读口令必须走 Interface 端点。
> 目前只维护 dev 环境；staging/prod 若要启用，需先补上 `secretsmanager` 端点。
> Interface 端点按 AZ-hour 计费。

**mysql-aws 的用法**：表 `t_migration_check` 由该工程创建与维护，本工程不建表、
也不在部署时去读它。表结构若有变动，需要同步修改 `common/schema.py` 里的
字段校验与列白名单，并重新发布本工程。

### 3. 创建 OIDC 角色

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
      "Sid": "ReadTerraformState",
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::platform-terraform-state-<账号ID>-<区域>/network/*"
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

### 4. 配置 Secrets Manager

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
| `LOG_LEVEL` | 模板参数 | 默认 `INFO` |
| `STAGE` | 模板参数 | 环境名 |
| `LambdaVpcSubnetIds` | 流水线从 terraform-aws state 注入 | 私有子网，逗号分隔 |
| `LambdaVpcSecurityGroupIds` | 流水线从 terraform-aws state 注入 | Lambda 安全组 |

代码中**不存在** `DB_PASSWORD` 之类的变量——口令只能来自 Secrets Manager。

表名 `t_migration_check` 是代码常量，**不是**环境变量，因此不在此表中。

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
`DB_HOST`、`DB_NAME`、`DB_SECRET_ARN`、`TF_STATE_BUCKET`。

**`AssumeRoleWithWebIdentity` 被拒绝**
→ 检查角色信任策略里的 `sub` 是否写成了
`repo:tengsihai/lambda-aws:environment:<环境名>`，以及身份提供者 ARN 是否正确。

**`读取 terraform state 失败`**
→ 确认 `TF_STATE_BUCKET` 是否正确，以及 key 是否为
`<TF_STATE_KEY_PREFIX>/<环境>/terraform.tfstate`（默认前缀 `network`）。
同时确认部署角色有该桶的 `s3:GetObject`。

**`state 中缺少 outputs.lambda_security_group_id`**
→ terraform-aws 还没在 `environments/<环境>/outputs.tf` 里导出这个输出，
按「依赖的外部工程」一节补上后重新 apply。

**运行时读口令报 `SecretsManager` 连接超时**
→ Lambda 在私有子网里，而该 VPC 没有 NAT 网关。确认该环境的
`interface_endpoint_services` 包含 `secretsmanager` 并已 apply
（dev 已开启）。

**运行时连不上数据库（安全组/超时）**
→ 确认 terraform-aws 已把 `lambda` 安全组加进 RDS 的 `allowed_security_group_ids`。

**冒烟测试提示「请求没有到达 Lambda」**
→ 多半是 API Gateway 的路由或集成没建好，查看 CloudFormation 栈事件。

**冒烟测试返回 200 以外但路由正常**
→ 流水线只给出 warning。表结构由 mysql-aws 工程维护，本工程不做建表。
