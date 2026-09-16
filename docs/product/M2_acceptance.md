# M2 文件工作区通过

2026-09-16。K2 证据保存在 `artifacts/product/M2/`。

- PDF/TXT/MD 流式上传，20 MB / 100 页 / 每项目 10 份有效资料限制；不支持的类型、非法名称、跨用户/项目访问被拒绝。
- 逻辑目录与不可变私有 blob 分离。重命名和移动只修改元数据；编辑和替换以预期版本检查防止覆盖，保留旧文件版本；回收/恢复保留关联。
- 独立 worker 持久领取解析任务，子进程 30 秒超时；Linux 子进程另有 1 GB 地址空间、25 秒 CPU 上限。页文本、段落、字符范围、chunk hash 与解析器版本持久保存。
- 30 项产品测试通过，包括真实 PostgreSQL、真实 PDF 解析、上传中断、读取中超限、事务故障、解析失败、越权、版本冲突与页面上限。模型替身测试与本阶段文件测试分开理解。
- 网页真实上传 ReAct PDF、预览第 1/2 页，创建/编辑 Markdown 笔记，回查旧版，创建目录、移动、回收恢复、重命名。TXT 从同一公开 PDF 首页提取，经 HTTP API 入库并验证下载。
- 实际重启 API/worker 后，3 个文件、8 个版本、480 个原文片段及全部下载文件 hash 一致。

## 发现与修复

初版 PDF 排序模式引入了多余布局空格和页边文本混排，改用文档原始文字顺序。随后测试发现页面末尾单换行会使最后段落不产生 chunk，已修复并加入最后段落检查。最终 parser 为 `pymupdf-1.27.2.2-text-3`。历史解析保留，新版通过不可变文件版本产生；`before_parser_fix.json` 是修复前记录。

## 边界与下一阶段

当前文件是“解析完成、待索引”，尚无 RAG ready 状态。PDF 图表的嵌入字体、扫描图和复杂排版可能无法可靠提取；原 PDF 下载始终保留，不执行 OCR。原文页/摘录定位正确不自动证明引用能支持某个结论，M3/M6 分别验证结构与语义。

上传失败采用只读对账报告定位孤立 blob 或中断记录，未自动永久清理。Windows 解析子进程有限时/输入/输出量限制，Linux 增加进程资源限制。

## 本机服务恢复

```powershell
docker compose --env-file .local/dev.env -f compose.local.yaml up -d db
powershell.exe -NoProfile -File scripts/product_services.ps1 -Action Start
```

Restart 只停止当前 checkout 的 API/worker 进程树；日志在 `.local/`。执行证据采集 `scripts/product_file_snapshot.py --verify` 仅用于该冻结快照，后续正常文件变化不应被当作丢数据。
