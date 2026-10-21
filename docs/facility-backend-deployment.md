# ACME 后端部署与恢复

本次交付以本机验收为目标。此文档说明与现有 Workshop 的兼容方式及云上线前的差异；没有执行 AWS 部署，也没有修改现有 Terraform、前端或部署脚本。

## 本地

遵循 `backend/facility-service/README.md`。原生 PostgreSQL 独立端口 55439，API 8019；Docker 和原生只能选一种。现有 Fleet Control 的端口、数据目录和账号不复用。运行文件位于新增服务自己的 `.runtime/` 并被忽略。

核心验证命令在该服务目录执行：

```powershell
.venv\Scripts\python.exe scripts/local.py test
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m compileall -q facility migrations scripts function.py migration_handler.py
```

测试使用随机 PostgreSQL schema，包含完整空库迁移、seed 幂等、权限/工单流程和角色矩阵。没有 SQLite 或 MongoDB 测试路径。迁移和 seed 不在 API 启动时自动运行。

## 现有 Workshop 接入

原模板的 `infra/locals.tf` 扫描 `backend/*/requirements.txt`，创建 Python Lambda，入口为 `function.handler`。新增服务符合此约定。云端路径为：

```text
/api/facility-service/api/v1/auth/login
/api/facility-service/api/v1/incidents
/api/facility-service/health
```

适配器只去除 `/api/facility-service` 前缀，保留访问令牌、Cookie、If-Match 和查询参数。API Gateway HTTP API、Lambda Function URL HTTP 事件均可处理；HTTP 请求不能触发迁移。

**现有 `bin/proxy-server.js` 仅转发少量头，会移除 Authorization、Cookie、If-Match。** 本机请直接访问 8019 上的 API，或在后续前端开发时新增保留这些头的同源 Vite 代理；不要通过原代理验证认证。当前前端文件完全保留。

Workshop 注入的 `POSTGRES_HOST/PORT/NAME/USER/PASS` 会被识别；也可提供显式 `FACILITY_DATABASE_URL`。只有 PostgreSQL asyncpg URL 被接受。云端 `IS_LOCAL=false` 启用数据库 TLS、安全 Cookie；缺失配置时明确失败，没有生产默认数据库或密钥。

## Secrets Manager 与浏览器来源

JWT 密钥必须至少 32 字节。推荐创建专用 JSON secret，通过 `FACILITY_SECRET_ARN` 指向它；若未配置 ARN，则使用现有注入的 `APP_NAME` 加 `/facility` 作为 secret 名。支持字段：

```json
{"jwt_secret":"REPLACE_WITH_RANDOM_32_PLUS_BYTE_SECRET"}
```

secret 也可提供 `database_url` 或 RDS 格式的 `host/port/username/password/dbname`。不要把上述占位值直接用于部署。运行角色需有该 secret 的 `GetSecretValue` 权限。通过受控环境传入唯一的公开 HTTPS 浏览器来源 `FACILITY_ALLOWED_ORIGINS`（JSON 数组），使 CloudFront 同源刷新/退出能通过来源校验。不要配置 `*`。

现有模板仅注入它已知的变量，新增参数需要在部署流程中显式合并到函数配置，不能用覆盖全部环境的方式丢失已有 POSTGRES/APP 变量。配置 secret 和来源后必须验证登录、刷新、退出、JWT 过期及内部备注隔离。

## 与 PRD 目标架构的差异

检查现有模板发现：Lambda 位于模板计算出的公共子网；入口是 Function URL，而不是 PRD 的 API Gateway/live alias；CloudFront 把 404 改写为 SPA；没有单独迁移函数与安全发布别名。这些现有文件均未修改，**自动发现服务不等于满足 PRD 全部云验收条件**。

云上线前须用另外新增的基础设施或经用户另行授权的模板变更完成：

1. 私网数据库安全组和非公开 RDS 验证；将 API/迁移 Lambda 放在可到达数据库和 Secrets Manager 的子网，提供接口端点或合适的出口。
2. 为 HTTP API 提供完整路径转发、Authorization/Cookie/If-Match 转发、禁用 API 缓存；保留真正的 API 404，不使用 SPA 错误重写。
3. 创建独立迁移 Lambda，入口 `migration_handler.handler`，打包同一服务源码和依赖，增加只允许部署身份调用的 IAM 权限；不得接入 Function URL/API Gateway。
4. 使用匹配云端 Linux/Python 3.13 的构建容器生成含原生扩展的部署包；不能将本机 Windows venv 上传到 Lambda。原模板 `build_in_docker=false`，因此需在 Linux/Workshop VDI 执行打包，或另行添加 Linux 构建流程。
5. 先迁移并初始化，再测试候选 API 版本和健康检查，最后切换 `live` 别名/流量。维持与旧版本兼容的增量数据库迁移。

本项目不自动创建或使用 DocumentDB/MongoDB；原模板中已有的相关示例和配置只保留原状，不由本服务调用。

## 迁移与首次管理员

本地：`python scripts/local.py migrate`，然后设置 `SEED_ADMIN_EMAIL/SEED_ADMIN_PASSWORD` 执行 `python scripts/local.py seed`。合成演示数据仅在明确的本地开发库通过 `seed --demo` 添加。

云端迁移函数仅接受 AWS 控制面调用体 `{"operation":"migrate"}`。它执行 Alembic 与幂等 seed，数据库 advisory lock 串行化迁移。部署程序须检查 Lambda `FunctionError` 和返回内容，失败则中止发布。

首次管理员的种子变量通过受控部署环境注入，成功后移除密码变量。已有有效管理员时不重设密码或恢复被移除权限；邮箱已有非管理员账号时明确失败，不静默提升。禁止移除最后一名有效管理员。零管理员恢复必须由运维显式操作。

刷新会话过期后保留七天，可运行 `python -m facility.sessions cleanup` 执行安全保留期清理；本地须先注入与服务相同的配置。它只删除满足保留期的认证会话，不删除业务记录。

## 回滚

每个交付批次必须带测试和对应 Git commit。使用 `git revert COMMIT_ID` 创建反向提交可追踪源码恢复；不要重置或清空用户工作树。涉及数据库时先备份并确认 API/schema 兼容。

候选版本失败时维持旧流量；上线后可把 API alias 指回前一版本。不要自动执行 Alembic downgrade，它会删除表。数据库恢复由备份和审阅后的迁移方案完成；源码回滚不等于数据回滚。记录服务版本、schema 版本及部署结果。
