# ACME 设施工单后端：结构与边界

本实现依据 PRD v1.1 Core MVP，借鉴 Fleet Control 的 FastAPI、SQLAlchemy、认证依赖、路由、初始化脚本和 PostgreSQL 测试分层。车队、车辆、检查报告及其 MongoDB 模块没有迁入。所有身份、会话、锁定记录、工单、备注、审计和报表都存储在 PostgreSQL。

## 新增目录

```text
backend/facility-service/
  function.py                 Workshop function.handler，HTTP 适配
  jobs_handler.py             定时任务 Lambda 入口（EventBridge 每 15 分钟）
  migration_handler.py        独立迁移 Lambda 的控制面入口
  facility/
    config.py                 本地/Workshop 环境与 Secrets Manager 配置
    database.py               PostgreSQL 会话，NullPool
    enums.py                  全部固定取值（状态、优先级、角色、错误码等）
    db_types.py               枚举与 JSON 列的 SQLAlchemy 类型
    errors.py                 带错误码的 HTTP 异常
    models/                   数据表，每个模型一个文件
      __init__.py             统一导出：from facility.models import Incident
      base.py                 公共 id/时间戳列与 utcnow()
      user.py  refresh_session.py  team.py  engineer_profile.py
      building.py  floor.py  seat.py  category_group.py  category.py
      incident.py  incident_follower.py  incident_note.py  incident_event.py
      escalation_request.py  attachment.py  notification.py
    security.py               bcrypt、JWT、刷新令牌摘要
    dependencies.py           当前数据库身份和角色检查
    permissions.py            工单归属、操作权限、版本、审计
    workflow.py               状态流转公共规则
    routing.py                新工单按分组自动分派、工程师排名
    sla.py                    SLA 到期时间与状态
    notifications.py          事件生成站内通知（同一事务）
    jobs.py                   自动关闭、SLA 预警、过期上传清理
    metrics.py                全公司与团队报表指标
    storage.py                S3 附件预签名与文件头校验
    routers/
      auth.py                 登录、注册、会话、改密
      people.py               用户、工程师、可用性和工作量
      teams.py                团队、成员、类别分组
      facilities.py           建筑/楼层/座位、分类
      incidents.py            工单、状态、分派、备注、升级、CSV 导出
      coordination.py         相似工单、关注、重复、退回分派、工程师推荐
      attachments.py          附件上传与下载
      notifications.py        通知列表与已读
      dashboards.py           员工/工程师/管理员/团队负责人报表
    seed.py                   幂等参考数据、管理员与可选演示数据
    sessions.py               会话保留期清理工具
  migrations/                 冻结的 Alembic 迁移
  scripts/local.py            独立 PostgreSQL 运行与验证
  tests/                      PostgreSQL 集成、权限矩阵和回归测试
bin/facility-local.ps1         新增 Windows 入口
bin/facility-local.sh          新增 Bash 入口
```

使用一个按业务分模块的 API 服务，保证分派、升级、备注、状态与事件在同一数据库事务内完成。服务位于 `backend/` 下一层，`requirements.txt` 和 `function.py` 符合现有 Workshop Terraform 自动发现规则。运行时支持 Python 3.12+；现有 Workshop 模板配置 Python 3.13。

## 关键一致性规则

- Employee 是隐含基础权限，Engineer/Admin 叠加。读取条件在 SQL 层限制到创建者/当前被分派者/管理员，绝不先返回全部再由前端过滤。
- 每个请求使用独立会话。操作先检查可见性，再检查权限，最后检查状态和版本；对应 404、403、409/428。
- 修改工单必须提供最新 `updated_at` 作为 `If-Match`。数据库行锁下重新读取版本并修改，避免两个操作互相覆盖。
- 设施选择、停用和工程师分派/停用使用一致的锁顺序；全局人员变更锁保护最后一个有效管理员。
- 所有状态变化与事件、要求的公开备注在同一事务中提交。备注只可由作者在十分钟内编辑；隐藏后所有普通响应仅返回占位。
- 最高 Critical 优先级的升级批准要求公开行动计划；每张工单最多一个待审批升级，终结时自动标记为 Superseded。
- PostgreSQL 持久保存刷新令牌摘要与令牌家族、失败登录时间、锁定截止时间。多进程/Lambda 重启不丢失认证状态。
- API 使用数据库当前角色和 token_version，不信任 JWT 中的旧权限。工程师临时密码改密前无法进入业务接口。
- 报表计数、平均时长、样本数和分类/位置聚合来自 SQL；没有已知时间戳的记录不参加相应平均值。

## 核心接口用法

本机基址 `http://127.0.0.1:8019/api/v1`。Swagger 在 `/docs`，OpenAPI 在 `/openapi.json`。先登录取得 `access_token`，点击 Swagger 的 Authorize 输入访问令牌。浏览器保存 httpOnly 刷新 Cookie，客户端请求刷新时必须携带 Cookie。

创建工单后，使用其 `updated_at` 调用分派和状态接口。`assigned_to` 是工程师的 **user_id**，不是工程师档案的 id。示例：

```http
POST /api/v1/incidents/{id}/assign
Authorization: Bearer ACCESS_TOKEN
If-Match: 2026-10-20T20:00:00+00:00
Content-Type: application/json

{"assigned_to":"ENGINEER_USER_UUID"}
```

状态变更体使用 `status`；Blocked 的 `reason` 是标准原因代码且 `note` 必填；Resolved 的 `note` 是公开解决说明。Closed/Cancelled 不再允许一般修改。备注编辑和隐藏使用备注自己的 `updated_at`。

搜索支持 `q`，重复 `status`/`priority`，`category_id`（包括下级），建筑/楼层/座位、`assigned_to`（含 `unassigned`）、管理员 `created_by`、升级状态、带时区的 `date_from/date_to`、`sort/direction/page/page_size`。`scope=mine` 限定自己创建；`scope=assigned` 限定当前被分派给自己。前端将这些参数保存在 URL 即可恢复视图。

管理员报表支持 `from/to/building_id`。工程师周报默认 UTC 周，可传入浏览器本地周转换后的 `week_start/week_end` UTC 边界。员工报表仅统计创建者自身；工程师队列只统计其被分派工单。报表中的下钻链接指向可核对的 API 列表。

## 后续增强

按照 PRD v1.1，附件、关注、重复建议、Kanban、SLA 自动化、通知和自动关闭尚未启用。这些功能没有伪造接口或内存替身。步骤条所需的状态、事件时间、操作者和后端校验已经提供。前端实现不在本次后端交付范围内。
