# Tools 模块

## 设计哲学

**工具做重活，Agent 做决策。**

工具的职责是执行具体操作（检索、解析、存储、查询）并返回结构化结果。  
Agent 的职责是决定"接下来该调用哪个工具、传什么参数"。

这意味着：
- 工具返回的不是原始数据，而是**预处理、结构化后的信息**
- 比如 `read_paper` 不返回 PDF 全文，而是返回 LLM 提取后的关键信息
- 这样保持 Agent 推理上下文的简洁，决策质量更高

## 工具一览

| 工具 | 输入 | 输出 | 调用时机 |
|------|------|------|---------|
| `search_papers` | 检索关键词 | 论文列表（标题/摘要/URL） | 需要发现新论文时 |
| `read_paper` | 论文 URL | 结构化信息（问题/方法/发现/局限/引用） | 需要深入了解一篇论文时 |
| `save_to_kb` | 论文标题 + 内容 | 存储确认 | read_paper 之后，保存知识 |
| `query_knowledge` | 检索问题 | 相关知识片段 + 来源 | 生成综述或回答问题时 |
| `assess_coverage` | 调研主题 | 覆盖度报告（已覆盖/空白/评分/建议） | 每读 3-5 篇论文后 |

## 工具间的典型调用链

```
search_papers("RAG optimization")
    → 返回 5 篇论文列表
    → Agent 选择最相关的 2-3 篇

read_paper(paper_url)
    → 返回结构化信息
    → Agent 阅读并理解

save_to_kb(title, extracted_info)
    → 存入知识库
    → 返回当前知识库论文数量

[重复 read_paper + save_to_kb 若干次]

assess_coverage(topic)
    → 返回覆盖度评估
    → 如果有空白 → Agent 决定用新关键词 search_papers
    → 如果覆盖充分 → Agent 进入综述生成阶段

query_knowledge("What methods are used for X?")
    → 从知识库检索相关信息
    → Agent 基于检索结果撰写综述
```

## 错误处理策略

所有工具在遇到异常时**不抛出异常**，而是返回包含 `[错误]` 或 `[失败]` 前缀的文本信息。这样 Agent 可以根据错误信息自行决定下一步（重试、换参数、跳过），而不是整个推理链路崩溃。

## 依赖注入

需要访问知识库的工具（save_to_kb, query_knowledge, assess_coverage）通过工厂函数接收 `knowledge_base` 实例。这样做的好处：
- 所有知识库工具共享同一个 FAISS 实例
- 测试时可以传入 mock 对象
- 不依赖全局状态
