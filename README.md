# Research Copilot Agent

面向科研证据发现的 Agent + Workflow 混合原型。开放主题探索由 Agent 根据检索/阅读观察动态选择下一步；单篇精读、PDF 解析、状态归并和输出渲染保持为受控 Workflow。

## 当前验证状态（2026-08-27）

> 2026-08-27 Agent v2 核心验收完成：类型化 `ResearchBrief/ResearchState/AgentAction/ToolResult`、显式 StateGraph、运行护栏、typed tool gateway、state/decision artifact、SQLite checkpoint、action-level HITL 和 `agent-v2` CLI 均已实现。26 项 Agent v2 专项离线测试及 12-case Core Acceptance 全部通过；A1/A2/A3 与 strict HITL 共 4 条 DeepSeek dated live run 通过工件合同验收。该结果证明单 Agent 控制闭环完整可运行，不外推为生产成功率或论文事实正确率。任务合同见 `PRODUCT_SPEC_AGENT.md`。

- S1 宽主题调研：Agent 工具循环，可输出 Markdown 综述与不含论文正文/工具参数的 `.trace.json` 运行摘要。
- S2 单篇精读：URL/本地 PDF 固定流水线；本地路径已有纯离线端到端测试。
- 离线统一测试覆盖配置、语义切分、7 工具注册、标题推断、section fallback、递归降级、Deep-Dive 管线和 evaluator 数据隔离。
- AgentOps 覆盖版本化运行轨迹、6-case 离线 replay、7 维分层指标、回归门禁、健康检查与默认 dry-run 的保留策略。
- 当前 replay：4 个正常/恢复场景通过，2 个故障注入场景被拒绝，门禁判定与预期 6/6 匹配；该结果只验证离线回放和门禁逻辑。
- 单 Agent Core Acceptance：A1/A2/A3、no-progress、search/read 上限、来源白名单、动作—工具合同与 strict HITL 共 12/12 通过。
- Live artifact acceptance：A1、A2、A3 和 strict HITL 共 4/4 通过；A3 只有显式 `supported/partial/contradicted` 证据关系才消除 claim gap，`unknown` 不计入完成。
- 当前全量离线回归为 7/7 套件、54/54 测试函数通过；run store 健康检查为 healthy、issues 为空。
- 本地样本知识库存在 24 条论文标题元数据和一份综述产物；这不等同于生产级质量评测。
- 尚未实现多篇对比、引用上下游追踪、独立 KB-only 问答、多用户云端部署与论文图表/公式解析，详见 `TODO.md`。

## 为什么不直接问 LLM？

| 直接问 LLM 的局限 | Agent 如何解决 |
|---|---|
| 知识有截止日期，不知道最新论文 | 通过 `search_papers` 工具实时检索 |
| 无法阅读 PDF 原文 | 通过 `read_paper` 工具下载并结构化提取 |
| 上下文窗口放不下 20+ 篇论文 | 通过向量知识库增量存储和按需检索 |
| 不会主动说"还有方向没覆盖" | 通过 `assess_coverage` 工具自评并补充 |
| 单轮对话，没有多步规划能力 | ReAct 推理循环，每步动态决策 |

## 项目结构

```
research-copilot-agent/
├── main.py                  # 入口（命令行 / 交互模式）
├── config.py                # 配置管理
├── agent/
│   ├── core.py              # Agent 主类（LLM + 工具 + 执行器）
│   ├── state_graph.py       # Agent v2 显式业务状态图
│   ├── policies.py          # Structured LLM / 离线决策策略
│   ├── checkpointing.py     # allowlisted SQLite/Memory checkpointer
│   ├── tool_gateway.py      # 工具 allowlist、错误分类与 typed adapter
│   ├── render.py            # Evidence package 确定性渲染
│   └── prompts.py           # Prompt 模板集中管理
├── domain/
│   └── schemas.py           # 任务、状态、动作、证据与人工决策合同
├── tools/
│   ├── search_papers.py     # 相关度论文检索
│   ├── search_papers_ranked.py # 引用数 + venue 启发式重排
│   ├── read_paper.py        # 在线论文阅读与信息提取
│   ├── read_local_pdf.py    # 本地 PDF 阅读与信息提取
│   ├── save_to_kb.py        # 存入知识库
│   ├── query_knowledge.py   # 知识库检索
│   └── assess_coverage.py   # 覆盖度自评
├── rag/
│   ├── knowledge_base.py    # FAISS 向量知识库管理
│   └── chunking.py          # 文档切分策略
├── evaluation/
│   ├── fixtures/            # 版本化离线回放 case
│   ├── metrics.py           # 分层确定性指标
│   ├── regression.py        # 阈值与 baseline 退化门禁
│   ├── replay.py            # 零 API 回放入口
│   ├── agent_v2_acceptance.py # 12-case 单 Agent 控制面验收
│   ├── agent_v2_live_acceptance.py # dated live 工件合同验收
│   └── evaluator.py         # 真实 Agent 评估逻辑
├── observability/
│   └── run_store.py         # manifest / events / result 与维护接口
├── reports/                 # 离线回测 JSON/Markdown 报告
└── output/                  # Agent 生成的综述文件
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入自己的模型/检索服务配置；不要提交真实 .env

# 3. 运行
python main.py "RAG 系统中的检索策略优化方法"

# Agent v2：显式状态图（会调用真实模型与检索/阅读工具）
python main.py --mode agent-v2 "LLM Agent memory" --axis methods --axis evaluation

# 严格人工审批：每次外部搜索/精读前 pause；输出会显示 run_id
python main.py --mode agent-v2 "LLM Agent memory" \
  --axis evaluation --approval-policy strict

# 使用相同 run_id 从 SQLite checkpoint 恢复
python main.py --mode agent-v2 \
  --resume-run-id <run_id> --human-decision approve \
  --human-reason "approve bounded search"

# 其他决策：reject / edit / request_more_evidence
# edit 的字段必须通过 action schema 与 policy guard 重新校验
python main.py --mode agent-v2 \
  --resume-run-id <run_id> --human-decision edit \
  --edit-json '{"arguments":{"query":"agent memory benchmark","max_results":5}}'

# A3：证据缺口闭环；至少提供一个 claim
python main.py --mode agent-v2 "Agent reflection evidence" \
  --task-type evidence_gap_closure \
  --claim "Reflection consistently improves task success"

# 或者交互模式
python main.py -i

# 单篇精读
python main.py --mode deep-dive --url https://arxiv.org/abs/2210.03629
python main.py --mode deep-dive --file ./papers/example.pdf

# 纯离线测试（不消耗 API 配额）
python tests/run_tests.py

# 零 API 离线回放：生成 JSON + Markdown 报告
python -m evaluation.replay --json-out reports/backtest.json --md-out reports/backtest.md

# Agent v2 显式状态图回放（观察驱动分支 + 终止守卫）
python -m evaluation.agent_v2_replay \
  --json-out reports/agent_v2_stategraph_replay.json \
  --md-out reports/agent_v2_stategraph_replay.md

# 12-case 单 Agent 核心验收（零 API）
python -m evaluation.agent_v2_acceptance \
  --json-out reports/agent_v2_acceptance.json \
  --md-out reports/agent_v2_acceptance.md

# 对已保存的真实 run 做合同验收；--run-id 可重复传入
python -m evaluation.agent_v2_live_acceptance \
  --run-id <run_id> --run-id <another_run_id> \
  --json-out reports/agent_v2_live_acceptance.json \
  --md-out reports/agent_v2_live_acceptance.md

# 运行轨迹运维：检查、聚合、30 天保留策略预览
python main.py --ops check
python main.py --ops report
python main.py --ops prune --days 30       # dry-run
# python main.py --ops prune --days 30 --apply  # 显式执行
```

使用 DeepSeek Chat Completions 时，在本地 `.env` 中配置 `LLM_PROVIDER=deepseek`、`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL=deepseek-v4-pro` 和 `DEEPSEEK_THINKING=disabled`。AgentAction 使用 function-calling；Embedding 使用独立的 `EMBEDDING_*` 配置。真实密钥不得提交。

Agent v2 的本地 checkpoint 默认为 `./data/checkpoints/agent_v2.sqlite`。它保存可恢复业务状态；原始工具正文仍不写入 `state.json/decisions.jsonl`。SQLite 文件只应视为受信任的本地应用状态，序列化器显式 allowlist 项目 domain 类型。

---

## 完整工作链路详解

> 本节是旧 B1 `create_agent` Prompt 工具循环的说明性示例，不是 2026-08-27 Agent v2 的实跑轨迹。v2 真实轨迹必须以 `data/runs/<run_id>/state.json`、`decisions.jsonl` 和测试/运行报告为准。

> 以下用一个真实调研场景，完整展示 Agent 的每一步推理和工具调用。

### 场景：用户输入 "请帮我调研 RAG 系统中的检索策略优化方法"

---

#### Step 1: Agent 分析主题，规划初始检索策略

```
[Thought]
用户要调研 RAG 检索策略优化。这个主题可以从几个角度切入：
1. 检索模型本身（dense vs sparse vs hybrid）
2. 查询优化（query rewriting, expansion）
3. 结果优化（reranking, filtering）
我先用这三个角度分别生成检索关键词，做广度检索。

[Action] search_papers
[Input] query="retrieval augmented generation retrieval optimization", max_results=5
```

> **设计意图**：System Prompt 中要求 Agent "先广后深"、"生成 2-3 组不同角度的关键词"。Agent 在 Thought 中体现了这一规划。

---

#### Step 2: 获取第一批检索结果

```
[Observation]
检索关键词: retrieval augmented generation retrieval optimization
找到 5 篇相关论文:

[1] 标题: Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks
    URL: https://arxiv.org/abs/2005.11401
    摘要: We explore a general-purpose fine-tuning recipe for retrieval-augmented generation...

[2] 标题: Dense Passage Retrieval for Open-Domain Question Answering
    URL: https://arxiv.org/abs/2004.04906
    摘要: Open-domain question answering relies on efficient passage retrieval...

[3] 标题: REPLUG: Retrieval-Augmented Black-Box Language Models
    URL: ...
    摘要: ...

[4] ...
[5] ...
```

---

#### Step 3: Agent 选择最相关的论文深入阅读

```
[Thought]
找到了 5 篇论文。第 1 篇是 RAG 原始论文，第 2 篇是 DPR（密集检索的代表工作），
这两篇是必读的基础论文。先读第 1 篇。

[Action] read_paper
[Input] paper_url="https://arxiv.org/abs/2005.11401"
```

---

#### Step 4: 获取论文结构化信息

```
[Observation]
**研究问题**：如何结合参数化记忆（预训练语言模型）和非参数化记忆（检索系统），
  提升知识密集型 NLP 任务的性能。

**核心方法**：提出 RAG 框架，将预训练的 seq2seq 模型与 Dense Passage Retriever 结合。
  检索阶段用 DPR 从 Wikipedia 中检索相关段落，生成阶段将检索结果作为额外上下文输入生成模型。
  提出 RAG-Sequence 和 RAG-Token 两种变体。

**关键发现**：在 Open-domain QA、Jeopardy、MSMARCO 等任务上超过纯参数化模型。
  RAG 生成的答案更具体、更多样、更符合事实。

**局限性**：检索质量直接影响生成质量；检索器和生成器的联合训练计算开销大。

**重要引用**：
  - Dense Passage Retrieval (DPR) - Karpukhin et al.
  - REALM: Retrieval-Augmented Language Model Pre-Training
```

> **注意**：这里 `read_paper` 工具内部用 LLM 做了信息提取，返回给 Agent 的是压缩后的结构化信息（约 200 词），而非原始 PDF 全文（约 5000 词）。这是 **"工具做重活，Agent 做决策"** 原则的体现。

---

#### Step 5: Agent 将信息存入知识库

```
[Thought]
这是 RAG 的奠基论文，信息很关键。存入知识库。
另外注意到重要引用中提到了 DPR 和 REALM，DPR 已经在搜索结果中，
REALM 值得追踪。

[Action] save_to_kb
[Input] paper_title="Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
       content="（上一步的完整提取内容）"
```

```
[Observation]
已将论文的关键信息存入知识库。当前知识库共有 1 篇论文。
```

---

#### Step 6-10: Agent 继续阅读更多论文

Agent 重复 Step 3-5 的流程，依次阅读 DPR、REPLUG 等论文。

同时，基于第一篇论文中发现的引用线索，Agent 决定追踪 REALM：

```
[Thought]
RAG 原始论文引用了 REALM，这是另一个重要的检索增强预训练方法。
我还没有覆盖预训练阶段的检索增强方法，需要搜索这篇。

[Action] search_papers
[Input] query="REALM retrieval augmented language model pre-training"
```

> **设计意图**：这体现了 Agent 的"主动追踪引用链"能力——不是死板地只搜用户给的关键词，而是根据阅读中发现的线索主动扩展检索。

---

#### Step 11: Agent 触发覆盖度自评

```
[Thought]
已经读了 5 篇论文了。按照工作原则，该评估一下覆盖度了。

[Action] assess_coverage
[Input] topic="RAG 系统中的检索策略优化方法"
```

```
[Observation]
已覆盖的子方向：
  ✓ Dense retrieval（DPR, RAG）
  ✓ 检索增强预训练（REALM）
  ✓ 黑盒 LLM 的检索增强（REPLUG）

未覆盖的空白：
  ✗ Hybrid search（结合 sparse 和 dense 的混合检索）
  ✗ Reranking（检索结果重排序）
  ✗ Query rewriting / expansion（查询改写与扩展）
  ✗ Chunk 策略优化（文档切分策略对检索的影响）

覆盖度评分：5/10

下一步建议：
  建议检索 "hybrid search dense sparse retrieval" 和 "reranking retrieval augmented generation"
```

> **这是整个系统的设计亮点**：Agent 不只是机械地搜和读，它会停下来审视自己的工作，发现遗漏的方向，然后主动补充。这个工具让 Agent 具备了"元认知"能力。

---

#### Step 12-16: Agent 根据评估结果补充检索

```
[Thought]
覆盖度只有 5/10，有好几个重要方向没有覆盖。
先搜 hybrid search 方向。

[Action] search_papers
[Input] query="hybrid search combining dense sparse retrieval RAG", max_results=5
```

Agent 继续检索、阅读、存储关于 hybrid search、reranking 等方向的论文。

---

#### Step 17: 再次评估覆盖度

```
[Action] assess_coverage
[Input] topic="RAG 系统中的检索策略优化方法"
```

```
[Observation]
已覆盖的子方向：
  ✓ Dense retrieval
  ✓ 检索增强预训练
  ✓ 黑盒 LLM 检索增强
  ✓ Hybrid search
  ✓ Reranking

未覆盖的空白：
  ✗ Query rewriting / expansion（可选，非核心）

覆盖度评分：8/10

下一步建议：
  覆盖度已较好。可以考虑补充 query rewriting 方向，或直接进入综述生成。
```

---

#### Step 18-19: Agent 进入综述生成阶段

```
[Thought]
覆盖度达到 8/10，已读论文 10 篇，覆盖了主要子方向。
现在进入综述生成阶段。先从知识库中检索各方向的关键信息。

[Action] query_knowledge
[Input] question="dense retrieval 方法有哪些，各自的核心思路和优缺点"
```

```
[Observation]
从知识库中检索到 4 条相关信息:

[1] 来源: 《Dense Passage Retrieval for Open-Domain QA》
    内容: 提出 DPR，使用双编码器架构，分别对 query 和 passage 编码...

[2] 来源: 《Retrieval-Augmented Generation...》
    内容: RAG 使用 DPR 作为检索器，在生成阶段将检索段落作为上下文...
...
```

Agent 对多个方向依次调用 `query_knowledge`，收集素材后生成最终综述。

---

#### Step 20: 输出结构化综述

```
[Final Output]

# 文献调研：RAG 系统中的检索策略优化方法

## 1. 研究主题概述

检索增强生成（RAG）通过结合外部知识检索来增强 LLM 的生成能力，
其中检索策略的质量直接影响最终生成质量。本综述梳理了该领域的主要研究方向...

## 2. 主要研究方向

### 2.1 Dense Retrieval（密集检索）
代表工作：DPR (Karpukhin et al., 2020)
核心思路：...
优势与局限：...

### 2.2 Hybrid Search（混合检索）
...

### 2.3 Reranking（重排序）
...

## 3. 方法对比

| 方法 | 核心思路 | 优势 | 局限 | 适用场景 |
|------|---------|------|------|---------|
| DPR  | ...     | ...  | ...  | ...     |
| ...  | ...     | ...  | ...  | ...     |

## 4. 研究趋势与展望
...

## 5. 参考文献
[1] Lewis et al. "Retrieval-Augmented Generation..." (2020)
[2] Karpukhin et al. "Dense Passage Retrieval..." (2020)
...
```

---

#### 调研后追问

综述生成后，用户可以基于已积累的知识库继续追问：

```
用户: 这些方法中，哪个最适合长文档场景？

Agent:
[Thought] 用户问的是长文档场景。先从知识库中检索与长文档相关的信息。
[Action] query_knowledge
[Input] question="long document retrieval chunking strategy"
[Observation] ...

[Final Answer]
根据已读文献，对于长文档场景，建议关注以下两个方面：
1. Chunk 策略：...
2. 分层检索：...
```

---

## 架构设计图

```
┌──────────────────────────────────────────────────────────────┐
│                          main.py                             │
│                    (入口 & 交互管理)                           │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│                    agent/core.py                             │
│              ResearchCopilotAgent                            │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │              ReAct 推理循环                              │  │
│  │                                                        │  │
│  │  Thought ──→ Action ──→ Observation ──→ Thought ──→ …  │  │
│  │                 │                                      │  │
│  │                 ▼                                      │  │
│  │         ┌──────────────┐                               │  │
│  │         │  Tool 调用    │                               │  │
│  │         └──────┬───────┘                               │  │
│  └────────────────┼───────────────────────────────────────┘  │
└───────────────────┼──────────────────────────────────────────┘
                    │
        ┌───────────┼───────────┬──────────────┐
        ▼           ▼           ▼              ▼
  ┌───────────┐ ┌────────┐ ┌────────────┐ ┌──────────────┐
  │  search   │ │  read  │ │ save/query │ │   assess     │
  │  papers   │ │  paper │ │  knowledge │ │  coverage    │
  └───────────┘ └───┬────┘ └─────┬──────┘ └──────────────┘
   Tavily /         │            │
   Semantic         │            ▼
   Scholar          │   ┌──────────────────┐
                    │   │  rag/             │
                    │   │  knowledge_base   │
                    │   │  (FAISS)          │
                    │   └──────────────────┘
                    │
                    ▼
              ┌───────────┐
              │  内部 LLM  │
              │  信息提取   │
              └───────────┘
```

## 关键设计原则

1. **工具做重活，Agent 做决策**：`read_paper` 内部用 LLM 提取关键信息，Agent 只看摘要级结果
2. **元认知**：`assess_coverage` 让 Agent 能评估自己做得好不好，主动补充不足
3. **错误不崩溃**：工具返回错误信息文本，Agent 自行决定重试或跳过
4. **增量积累**：知识库持久化到磁盘，跨会话复用
5. **评估隔离**：evaluator 使用临时知识库，不清空用户真实数据
6. **失败可解释**：运行摘要记录结束原因、耗时、图步骤和工具调用分布
7. **轨迹最小化**：每次运行写入 manifest、事件 JSONL 和结果摘要，但不默认保存论文正文、完整 Prompt、工具参数或工具返回
8. **离线回放**：固定 fixture 在无 API 环境下重算指标与门禁，区分正常、降级恢复和故障注入场景
9. **回归门禁**：逐项检查任务成功、方向覆盖、论文命中、结构、工具覆盖/成功和效率，并支持相对 baseline 的退化限制
10. **维护安全**：运行目录可检查、聚合和按保留期清理；清理默认 dry-run，实际删除要求 `--apply` 且目标必须位于 run store 内
