# 本机开发入口

阶段顺序以 `D:/Resume/比特无限/06_Agent产品化方案/04_分阶段开发计划与验收门槛.md` 为准。
当前执行证据见 `artifacts/product/M0/`；目录已被 Git 忽略，不发布个人资料或密钥。

## M0 命令（PowerShell，项目根目录）

```powershell
.\.venv\Scripts\python.exe scripts/product_baseline.py
.\.venv\Scripts\python.exe scripts/product_local_init.py
docker compose --env-file .local/dev.env -f compose.local.yaml up -d db
.\.venv\Scripts\python.exe -m unittest discover -s tests/product -v
.\.venv\Scripts\python.exe scripts/product_live_probe.py
```

`legacy_source.zip` 是只创建一次的原型源码备份，`source_manifest.json` 逐文件记录 SHA-256。
恢复时解压到新空目录，核对 manifest，再选择性恢复文件；不要覆盖现有开发目录或个人数据。
原有 7 组测试运行入口是 `tests/run_tests.py`，不批量执行历史 live/Chroma 脚本。

`.env` 保留原有模型配置；`.local/dev.env` 是独立产品配置。开发数据库 `copilot_dev`、端口 15432、私有文件 `.local/storage`；测试使用另一数据库和临时文件目录。旧 `data/` 不做迁移或 reset。

## 决策依据

- 使用现有 PostgreSQL/pgvector 方案，首版采用精确向量查询，先验证小语料过滤正确性；[pgvector 官方文档](https://github.com/pgvector/pgvector) 提供固定版本 `0.8.6-pg16-bookworm`。运行后保存镜像 digest。
- 任务来源固定为 [ReAct v3](https://arxiv.org/abs/2210.03629v3) 和 [Reflexion v4](https://arxiv.org/abs/2303.11366v4)，原 PDF、页文本和 hash 位于本机证据目录。
- M0 三题及核查标准在 `evaluation/product/tasks.json`，模型调用前已保存。M0 只验证任务可行性，不冒充完整 Skills 或 M6 留出评测。
- `StatePatch` 是带预期版本的局部变更；纯函数只验证结构，业务层仍须在数据库里原子检查 revision，防止迟到结果覆盖新要求。
- 模型/API 超时、限流、格式错误只记录类别和状态；不保存密钥或隐藏推理内容。质量核查区分 schema、引文定位与语义支持。
