# 网页产品开发记录

## 当前断点：M1 已通过，M2 文件工作区进行中

2026-09-16：21 项产品测试通过（真实 PostgreSQL、模型替身）；另有网页两次真实 DeepSeek 调用。实际 API/worker 重启后 4 条消息、2 个 Run、4 个 span 保持一致，重新登录、会话归档恢复与项目隔离已在网页验证。证据见 `artifacts/product/M1/acceptance.json` 和 `docs/product/M1_acceptance.md`。

下一步：不可变 blob、Folder/File/FileVersion、文件流式上传与版本管理、页级解析任务和网页文件区；解析完成仅标记待索引。M3 后才允许可检索状态。M2–M7 未通过，未部署服务器。

## 2026-09-16｜M0 进行中

- 执行入口：`D:/Resume/比特无限/06_Agent产品化方案/04_分阶段开发计划与验收门槛.md`。
- 原 CLI、旧 FAISS 和个人数据保持独立。新产品使用 PostgreSQL/pgvector 与私有文件卷。
- Python 3.11.7；Docker Client 29.4.3、desktop-linux context、WSL Ubuntu-24.04 可用；Linux engine 尚未连通。
- 初次 Docker 配置/WSL 读取被沙箱阻止，提升权限后确认不是未安装而是引擎没有运行；启动 Desktop 后再次检查仍未就绪，继续定位启动日志。
- 新增 `scripts/product_baseline.py`：只归档明确的源码与 fixture，核查已配置密钥未混入文件，保存 hash/依赖清单并运行原有 7 组离线测试。
- K0 尚未通过；M1 尚未开始。所有后续测试结果以 artifacts/product 下实际记录为准。

## M0 验收通过，进入 M1

- 初始源码 57 个文件归档并校验；新建本地 Git 仓库，提交前检查 staged blobs，排除密钥/个人 KB/运行记录。
- 原有 7 组 / 54 项测试通过；新增 10 项合同测试通过；依赖更新后原测试再跑通过，pip check 通过。
- Docker Desktop 4.91.0 / Linux Engine 29.8.0 恢复；两处旧零字节 socket 目录改名备份后重新生成，没有 reset/删除数据卷。
- PostgreSQL + pgvector 0.8.6 已迁移；数据库实际重启后同一 UUID 记录和向量查询保留。镜像 digest：`sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b`。
- 三题真实论文测试和第二模型探测完成。QA Pro 逐条原文复核通过；另外输出的数量/引文问题原样保留，未记为完整 Skills 成功。
- 智谱 429/1113 是账户欠费；用户充值后同一密钥恢复，embedding-3 实测 2048 维。DeepSeek Pro/Flash、Tavily 均连通。
- `artifacts/product/M0/acceptance.json` 的全部 K0 检查通过；开始 M1。M1 只验收登录、对话与任务/trace 基础，RAG/Skills/文件功能在后续阶段实现。
