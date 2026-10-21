# ACME 设施工单服务（核心 MVP）

独立 FastAPI 后端。接口使用 `/api/v1`，数据全程存储于 PostgreSQL 16。通过 `function.py` 兼容 Workshop Lambda 服务入口；本地 API 使用端口 `8019`。

## 本机启动

需要 Python 3.12 或更新版本，以及 PostgreSQL 16 本机安装。服务脚本会创建独立数据库集群，地址 `127.0.0.1:55439`、数据库 `acme_facility_dev`；数据库密码与 JWT 密钥由脚本随机生成，保存在被忽略的 `.runtime/local.json`，不要提交该文件。

在 `backend/facility-service` 目录执行：

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe scripts/local.py init
.venv\Scripts\python.exe scripts/local.py migrate
.venv\Scripts\python.exe scripts/local.py seed --demo
.venv\Scripts\python.exe scripts/local.py serve
```

如果 PostgreSQL 安装位置不同，先设置 `FACILITY_PG_BIN` 指向其 `bin` 目录。Windows 原生服务使用当前用户启动；Linux/macOS 在普通用户下运行。首次 `init` 只初始化空的服务目录；后续运行复用集群，不重置密码、不删除数据。端口被其他程序占用时应先处理端口冲突。

浏览器打开 [交互式 API 文档](http://localhost:8019/docs)。演示账号为 `admin@acme.inc`、`employee@acme.inc`、`engineer@acme.inc`、`employee2@acme.inc`，初始演示密码为 `DemoPass123!`。可在首次演示初始化前通过 `DEMO_PASSWORD` 设置自己的密码。演示数据仅允许写入本地 `acme_facility_dev`；重复执行不会重设已有账号、权限、密码或工单。

正式初始化不使用 `--demo`：设置 `SEED_ADMIN_EMAIL`（`@acme.inc`）与 `SEED_ADMIN_PASSWORD` 后执行 `seed`。已有管理员时不会自动提升其他账号权限。普通注册始终只有员工权限。

仓库根目录也可使用 `bin/facility-local.ps1 init|migrate|seed|serve|test|status|stop`；Linux/macOS 使用 `bash bin/facility-local.sh ...`。可设置 `FACILITY_PYTHON` 选择解释器。运行 `stop` 仅停止本服务拥有的集群，不删除任何文件。

## PostgreSQL 可选容器方式

`compose.yaml` 提供 PostgreSQL 16，并只绑定回环地址。先设置强密码 `FACILITY_POSTGRES_PASSWORD`，再执行 `docker compose up -d`。容器方式和原生方式使用相同端口，只选择其中一种。使用容器或自有 PostgreSQL 时，显式设置：

```text
FACILITY_DATABASE_URL=postgresql+asyncpg://USER:URL_ENCODED_PASSWORD@127.0.0.1:55439/acme_facility_dev
FACILITY_JWT_SECRET=<至少32字符的随机密钥>
IS_LOCAL=true
TEST_DATABASE_URL=<明确指定用于验证的PostgreSQL URL>
```

数据库账号需有迁移权限；测试账号需能创建和删除测试 schema。`IS_LOCAL=true` 仅用于本地 HTTP；云环境使用 HTTPS 和安全 Cookie。生产密钥由环境或 Secrets Manager 注入。

## 接口与业务边界

固定业务取值统一定义在 `facility/enums.py`：工单状态、优先级、附加角色、工程师可用性、备注可见性、升级状态、阻塞原因和审计事件类型，以及列表筛选/排序与 API 错误码。模型、接口校验、权限、流程、报表和 seed 都复用这些枚举。

SQLAlchemy 读写使用枚举的 `.value`，例如 `In Progress`，不会保存成员名 `IN_PROGRESS`。PostgreSQL 保持原有 VARCHAR、TEXT 和 JSONB 列类型；角色仍保存为字符串数组，Employee 仍是隐含权限。迁移 `0002` 只补充合法值约束，不改写已有记录，也不创建 PostgreSQL 原生 ENUM 类型。升级发现不合法的旧数据时会中止，须核实后处理，不能自动猜测替换。

- 认证：`/auth/register`、`/auth/login`、`/auth/refresh`、`/auth/logout`、`/auth/change-password`、`/auth/me`。
- 人员：`/users`、`/users/me`、`/users/{id}`、`/engineers`、`/engineers/{id}`、`/engineers/me/availability`。
- 设施：`/buildings`、`/buildings/{id}/floors`、`/floors/{id}/seats`、`/locations/tree`、`/categories`。停用替代物理删除，历史记录保留。
- 工单：`/incidents`、`/incidents/{id}`，以及 `/assign`、`/priority`、`/transitions`、`/escalation`、`/escalation/decision`、`/notes`、`/timeline`。
- 备注：`/notes/{id}` 编辑自己的备注（创建后 10 分钟内）；`/notes/{id}/hide` 由管理员隐藏并保留审计。
- 报表：`/dashboard/employee`、`/dashboard/engineer`、`/dashboard/admin`。

所有接口字段、权限和示例以交互式文档和自动化验收为准。更新工单、添加备注、审批升级时，将最新响应的 `updated_at` 放入 `If-Match`；缺失返回 `428`，版本过期返回 `409`。编辑或隐藏备注使用备注自身版本。员工只能查看自己的工单，工程师只能查看自己创建或被分派的工单，管理员可查看全部；内部和隐藏备注不会出现在员工投影中。

此交付提供后端工作流与图形前端所需的状态、权限、统计和下钻链接。附件、关注者、自动通知、外部集成及自动关闭属于后续增强范围。云部署配置见仓库新增的设施服务部署说明。

## 测试和验证

```powershell
.venv\Scripts\python.exe scripts/local.py test
.venv\Scripts\python.exe -m ruff check facility tests scripts function.py
.venv\Scripts\python.exe -m compileall -q facility scripts function.py
```

测试只连接明确配置的 PostgreSQL。每个集成测试创建随机的 `acme_test_<uuid>` schema，并只删除自己创建的 schema；不清空业务表。测试覆盖认证与令牌撤销、角色与归属权限矩阵、工单闭环、并发版本、备注可见性、升级边界、设施层级和报表结果。工单与权限模块覆盖率下限为 70%。测试运行器显式加载所需插件，避免受当前机器其他 pytest 插件影响。

迁移命令可重复执行，`seed` 为幂等添加。升级前备份 PostgreSQL，并按新增部署说明执行回滚与验证。不要直接移动运行中的 `.runtime/postgres-data`；将源代码复制到新位置后应在该位置初始化独立运行目录。
