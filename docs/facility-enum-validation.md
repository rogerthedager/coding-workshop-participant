# 后端共享 Enum 重构验收记录

日期：2026 年 10 月 21 日（America/New_York）。基于提交 `32f033b` 的设施工单核心后端完成本次重构。

## 改动

- `facility/enums.py` 统一定义工单状态、优先级、附加角色、工程师可用性、备注可见性、升级状态、阻塞原因和审计事件类型。
- 列表范围、排序字段/方向、未分派/无升级筛选以及 API 错误码也采用共享 Enum；状态集合、转换表和优先级排序集中维护。
- 模型、Pydantic 请求、权限、业务流程、报表和 seed 复用同一组枚举。SQLAlchemy 读取后返回 Enum 成员；角色数组中的元素也恢复为 `UserRole`。
- 对外 JSON 和数据库继续使用 `.value`，例如 `In Progress`、`High`；不写入 `IN_PROGRESS` 等成员名。隐含 Employee 权限和已有角色数组契约保持不变。
- PostgreSQL 保持原有 VARCHAR、TEXT、JSONB 类型。保留迁移 `0001`；新增 `0002` 为角色、阻塞原因和事件类型补充合法值约束，不改写历史记录。
- 分类名称、描述、备注正文等可配置或自由文本字段仍使用字符串。

## 验证结果

| 项目 | 结果 |
| --- | --- |
| 目标目录完整测试 | 130 项全部通过，0 失败、0 错误、0 跳过；292.84 秒 |
| 权限与工单流程联合覆盖率 | 93.67%，门槛 70% |
| 目标目录 Enum/迁移专项复测 | 25 项通过 |
| 数据兼容 | 实际 `0002` 迁移前后，11 张表的行数和完整行内容摘要一致 |
| 迁移回归 | 带历史字符串数据的 `0001 → 0002` 升级成功；非法历史值导致事务失败，原值和 schema 版本保留 |
| 类型/存储回归 | 全部枚举值往返后是 Enum；底层保存原字符串；JSONB 角色查询保留；不创建 PostgreSQL 原生 ENUM |
| API 回归 | 共享 OpenAPI 枚举、原字符串 JSON、查询哨兵、排序、非法输入 422 和错误码通过 |
| 真实 HTTP | 三角色登录、创建、分派、开始、阻塞/恢复、升级至 Critical、内部备注隔离、解决/关闭、三角色报表通过 |
| 质量检查 | Ruff 检查/格式、编译与 pip check 通过 |
| 原仓库保护 | 原 Workshop 的 81 个文件校验值保持一致，仅修改此前新增的设施后端并新增本记录 |

完整测试在目标项目独立 `.venv` 和专用 PostgreSQL 实例中执行。证据保存在被 Git 忽略的 `.runtime/enum-full-tests.xml`、`.runtime/enum-coverage.xml`、`.runtime/enum-full-test-run.log`。此前还在隔离工作副本中完成 130 项全量回归。

本机 API 为 `http://127.0.0.1:8019/docs`。本次真实 HTTP 验收生成的合成工单以 `Enum smoke:` 开头，并已关闭。

## 复现与回滚

在 `backend/facility-service` 执行：

```powershell
.venv\Scripts\python.exe scripts/local.py migrate
.venv\Scripts\python.exe scripts/local.py test
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
```

遇到不合法历史值时先调查，不自动映射到其他枚举成员。源码通过本次对应 Git commit 追踪。回退业务代码时应保留已应用的迁移历史，或创建前向修复提交；不能删除数据库中已登记的 `0002` 文件后继续运行 Alembic。`0002` 只新增约束；不要自动降级数据库，若确需移除这些约束应先审阅迁移及备份。旧业务代码使用相同字符串值，可读取升级后的数据。
