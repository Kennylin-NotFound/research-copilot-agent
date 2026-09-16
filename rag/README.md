# RAG 模块

## 职责

管理向量知识库的完整生命周期：文档切分 → Embedding → 存储 → 检索。

## 文件说明

| 文件 | 职责 |
|------|------|
| `knowledge_base.py` | 封装 FAISS 操作，提供 add / query / has / clear 接口 |
| `chunking.py` | 文档切分策略，将结构化论文信息切分为适合向量存储的 chunk |

## 核心设计决策

### 切分策略：按语义 section 而非固定长度

`read_paper` 返回的信息已经是结构化的（研究问题 / 核心方法 / 关键发现 / 局限性 / 重要引用），所以切分策略优先按 section 边界切分。

好处：
- 每个 chunk 是一个完整的语义单元
- metadata 中记录了 `section_type`，后续可以做更精准的检索（如"只检索 method 类内容"）
- 避免固定长度切分导致的语义截断

Fallback：如果 `read_paper` 返回格式不规范（无法识别 section 标识），回退到固定长度切分。

### 去重策略：基于论文标题

- 内存中维护一个 `_paper_titles` 集合
- `has_document()` 检查标题是否已存在
- 启动时从磁盘（titles.json）恢复已存储的标题集合
- 简单有效，避免重复存储同一篇论文

### 增量更新

- 新论文直接追加存储，不需要全量重建索引
- FAISS 的 save_local / load_local 持久化到磁盘（`./data/faiss_index/`）
- 这意味着：上次调研积累的知识在下次调研中仍然可用

## 扩展方向

- 支持按 `section_type` 过滤检索（如 `query(question, filter={"section_type": "method"})`）
- 引入 reranking 机制（先 embedding 召回 top-20，再用 cross-encoder 精排到 top-5）
- 支持多 collection 管理（每个调研主题一个 collection）
