"""
测试用例定义

每个测试用例包含：
- topic: 调研主题
- expected_directions: 预期应覆盖的子方向列表
- key_papers: 必须找到的关键论文（标题关键词）
- description: 测试用例的描述说明

评估时：
- 运行 Agent 完成调研
- 检查输出综述是否覆盖了 expected_directions
- 检查知识库中是否包含 key_papers
"""

TEST_CASES = [
    {
        "id": "tc_01",
        "topic": "Retrieval-Augmented Generation (RAG) 系统中的检索策略优化方法",
        "expected_directions": [
            "dense retrieval",       # 密集检索（基于 embedding）
            "sparse retrieval",      # 稀疏检索（BM25 等）
            "hybrid search",         # 混合检索
            "query rewriting",       # 查询重写/扩展
            "reranking",             # 重排序
        ],
        "key_papers": [
            "RAG",                   # Lewis et al. 2020 原始 RAG 论文
            "DPR",                   # Dense Passage Retrieval
            "ColBERT",               # Late interaction 检索
        ],
        "description": "基础测试：RAG 检索优化是一个文献丰富的领域，Agent 应能覆盖主要子方向",
    },
    {
        "id": "tc_02",
        "topic": "大语言模型 Agent 中的规划与推理机制",
        "expected_directions": [
            "ReAct",                 # 推理+行动
            "chain of thought",      # 思维链
            "tree of thought",       # 思维树
            "task decomposition",    # 任务分解
            "self-reflection",       # 自我反思
        ],
        "key_papers": [
            "ReAct",
            "Chain-of-Thought",
            "Reflexion",
        ],
        "description": "核心测试：与本项目直接相关的 Agent 推理机制研究",
    },
    {
        "id": "tc_03",
        "topic": "LLM 应用中的 Prompt Engineering 方法论与最佳实践",
        "expected_directions": [
            "few-shot prompting",
            "chain of thought",
            "instruction tuning",
            "prompt optimization",
        ],
        "key_papers": [
            "few-shot",
            "chain-of-thought",
        ],
        "description": "Prompt Engineering 是一个相对宽泛的主题，考验 Agent 的聚焦能力",
    },
    # --------------------------------------------------
    # 可以继续添加更多测试用例
    # 建议覆盖：
    # - 窄领域（如 "LoRA 微调方法的最新进展"）
    # - 交叉领域（如 "LLM 在软件工程中的应用"）
    # - 新兴方向（如 "Multi-Agent 协作框架"）
    # --------------------------------------------------
]
