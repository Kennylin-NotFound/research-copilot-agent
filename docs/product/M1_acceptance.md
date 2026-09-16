# M1 本机网页与持久对话通过

时间：2026-09-16。证据：`artifacts/product/M1/acceptance.json`、`browser_verification.md`、`product_tests.json`。

- 三栏网页：项目与会话、消息区、运行详情；真实创建、重命名、归档恢复、切项目、退出重登和刷新验证完成。
- Argon2 密码散列、可撤销 Cookie Session、同源状态变更校验、按 owner 过滤与数据库复合外键。
- 同一事务创建 Message+Run+Job；同一 client_message_id 重试与并发重复只产生一个任务，异内容冲突返回 409。
- 独立 worker 执行模型调用并落最终消息；最小 run/model spans 包含模型、Prompt、消息来源、耗时和 usage。
- 21 项产品测试通过，涵盖数据库回滚、跨用户拒绝访问、模拟模型故障脱敏、多轮上下文等；两条真实模型运行另行记录。
- API 与 worker 实际重启后 4 条消息、2 个 run、4 个 span 内容和关联一致；浏览器重新登录后可继续查看。

## 本机启动

在两个终端中分别运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn product.api:create_app --factory --host 127.0.0.1 --port 18080
.\.venv\Scripts\python.exe -m product.worker
```

页面：http://127.0.0.1:18080。开发账户在 `.local/demo-login.md`，该文件已忽略。

## 本次修复与下一步

浏览器发现 Grid 默认最小高度使长消息撑开整页；修复各面板高度限制并实际刷新检查。异步会话列表切换增加 epoch 检查，阻止迟到响应替换当前列表；切对话清空旧 Trace 详情。

当前是 M1 对话基础，下一步 M2 文件工作区和解析。Trace 不是隐藏思维链，当前记录的是可核对的调用元数据。M5 才完善任务恢复与 SSE，M4 才将产品意图/状态接入既有 LangGraph 内核。
