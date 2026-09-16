# M0 数据合同与实现边界

代码：`product/contracts.py`；测试：`tests/product/test_contracts.py`。

## 身份与版本

| 对象 | 主键与归属 | 版本/边界 |
|---|---|---|
| ProjectRevision | project_id + revision，owner_id | 业务真相；修改用 expected_revision，数据库 CAS 待 M1/M4 接入 |
| MessageIntent | 消息在数据库拥有 message_id / conversation_id / owner_id | kind=ask/revise/execute/cancel/clarify；仅 revise 带 StatePatch |
| StatePatch | 关联 ProjectRevision | schema_version=1；只改明确字段，空列表清除；null/额外字段拒绝 |
| FileVersion | file_version_id、file_id、project_id、owner_id | 不可变 hash+version；物理路径不出现在模型输入或浏览器参数 |
| SkillRuntime | skill_id+version+body_sha256 | 固定三种 Skill，运行锁定 manifest；工具枚举白名单 |
| ToolObservation | action_id、run_id、attempt | 显式 ok/error/partial；出错必须归类；证据引用无完整原文副本 |
| EvidenceRef | evidence_id、chunk_id、file_version_id、project_id | 页/段/hash/摘录；只允许 original 或 user_note |
| RunStatus | 数据库 run_id 归属项目/会话/owner | terminal 状态不能复活；取消中不能提交成功 |
| TraceSpan | trace_id、span_id、parent_span_id、run_id | 状态/用量/版本；输入输出用受权限保护的引用保存，未知 token 为 null |

UUID 不能代替权限验证，Pydantic 也不能代替数据库约束。M0 当前只实现结构校验、纯函数修改、允许状态转移和基础迁移。归属查询、事务/CAS、worker fencing、payload 存储在对应后续阶段接入，当前不声称产品运行可靠性已经完成。

## 运行与错误规则

- queued → running → completed / waiting_user / cancelling / failed。
- waiting_user → queued（收到补充后继续）或 cancelled / failed；等待时不持有 worker。
- cancelling → cancelled / failed；终态没有自动出边，重试由新 attempt/显式恢复流程管理。
- invalid_input / unauthorized / not_found / version_conflict 不盲目重试。
- timeout / rate_limit / unavailable 在统一重试上限内处理；余额/额度错误单列 quota，不能靠循环重试解决。
- schema_error 允许一次受预算约束的纠正；无依据或不存在的引文不能直接发布。
- no_evidence 请求补充或明确不足；budget_exceeded 停止继续调用，保留已验证资料。

## 首版默认预算

每 run 最多 12 步、3 次搜索、3 次新全文读取、最多 2 次错误重试、180 秒、40000 总 token、单次最多输出 2000 token；总 token 包含重试。20 MB/100 页/项目 10 篇为待容量验证的限额，不能据此声称 2C4G 已通过压力测试。金额需配置提供商实际价格再计算，当前不编造 cost。

## 开发/测试隔离

`.local/dev.env` 提供开发连接；集成测试使用独立 `copilot_test` 数据库并只清理该测试库。迁移按 SQL 文件 SHA-256 记录，已应用文件变更立即报错；版本更新用新的迁移。没有在旧 FAISS/SQLite 数据上执行迁移。外部 API 使用当前本机密钥，日志仅保留 provider/model/错误类别，不输出凭据。

## 本次真实任务暴露的问题

两个模型的 evidence-qa 输出结构与原文定位通过。paper-review 虽然 schema 合法，却输出了 4 条 claim（需求最多 3）；表明自然语言约束须转成 Skill 的输出校验。部分 PDF 引文差异来自行尾断词和弯引号；今后采用保留定位的文本规范化，不能把改写后的字符串默认为原文，更不能用宽松模糊匹配代替语义核查。
