# ACME 核心后端验收记录

验证日期：2026 年 10 月 20 日（America/New_York）。交付范围为 PRD v1.1 的核心后端；前端页面和 AWS 实际上线不在本次已完成范围。

## 验证环境与结果

| 项目 | 结果 |
| --- | --- |
| 项目 | `C:\Users\roger\Documents\GitHub\coding-workshop-participant` |
| 后端目录 | `backend/facility-service`，独立 `.venv` |
| 实际验证环境 | Windows、Python 3.14.7、PostgreSQL 16.15、FastAPI 0.141.1、SQLAlchemy 2.1.1 |
| 数据库隔离 | 本项目独立集群、回环端口 55439；未访问 Fleet Control 数据库 |
| 完整自动测试 | **111 passed，0 failed，0 errors，0 skipped**，236.05 秒 |
| 权限和工单流程覆盖率 | **93.38%**，门槛 70% |
| 权限模块 | 97%（63 条可执行语句中覆盖 61 条） |
| 工单模块 | 93%（496 条可执行语句中覆盖 461 条） |
| 依赖一致性 | `pip check` 通过；显式包含异步 SQLAlchemy 所需 greenlet |
| 代码质量 | Ruff 检查及格式检查通过，31 个 Python 文件 |
| Python 编译 | facility、migrations、scripts 与两个 Lambda 入口编译通过 |
| 脚本 | 新增 PowerShell、Bash 入口语法检查通过 |
| 空库迁移 | 成功，重复升级不重建记录；冻结迁移另有随机 schema 测试 |
| 演示初始化 | 首次新增 66 条参考/演示记录；自动测试确认重复执行不重置密码、名称和工单 |
| Lambda 入口 | 真实 Mangum Function URL HTTP 事件和 Workshop 路径健康检查通过 |
| 原有文件 | 原有 81 个文件 SHA-256 校验值保持一致；Git 提交只能包含新增文件 |

核心测试覆盖账号注册、令牌过期/撤销/重放、并发锁定、首次改密、最后管理员保护、三角色及组合角色矩阵、对象归属、设施层级、停用限制、工单完整流转、分派/升级冲突、最高优先级升级、备注编辑窗口与隐藏、时间线隐私、搜索、报表 SQL 数值、迁移和 seed。

## 真实 HTTP 验收

通过实际监听 `127.0.0.1:8019` 的目标项目服务完成以下流程，使用合成演示账号：

1. 员工、管理员、工程师分别登录。
2. 员工创建带建筑、楼层和分类的工单。
3. 管理员分派给有效工程师。
4. 工程师开始处理并添加内部备注，员工读取备注时没有内部内容。
5. 工程师填写公开解决说明并转为 Resolved，员工确认 Closed。
6. 管理员报表返回成功；员工刷新令牌、退出，退出后的刷新请求返回 401。

该合成工单保留在演示库中，标题以 `Delivery smoke:` 开头，便于检查审计历史。

## 复现

在服务目录执行：

```powershell
.venv\Scripts\python.exe scripts/local.py test --junitxml=.runtime/test-results.xml --cov-report=xml:.runtime/coverage.xml
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe -m pip check
```

`.runtime/test-run.log`、`test-results.xml` 与 `coverage.xml` 保存本机详细证据，运行文件和凭据不提交到 Git。`requirements-verified.txt` 记录本次全新环境解析的精确依赖版本，供相同平台复现；云端须在其 Linux/Python 运行环境重新验证原生依赖包。

启动和数据库操作见服务 README，架构与云端差异见 `facility-backend-architecture.md`、`facility-backend-deployment.md`。本批次以一个包含实现、测试、PRD 和验收记录的原子 Git commit 交付，便于追踪和源码回滚。
