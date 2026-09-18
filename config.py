"""
项目配置管理
统一管理 API Key、模型参数、知识库路径等配置项。

切换 LLM Provider 只需修改这里的三个值：
  LLM_API_KEY  / LLM_API_BASE  / LLM_MODEL
  EXTRACTION_MODEL / EMBEDDING_MODEL
"""

import os
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# LLM Provider 配置（核心，切换 provider 改这里）
# ============================================================

_ZHIPU_API_KEY = os.getenv("ZHIPUAI_API_KEY", "")
_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
_DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# 显式 provider 优先；未指定时根据已有 key 兼容旧配置。
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").strip().lower()
if not LLM_PROVIDER:
    if _ZHIPU_API_KEY:
        LLM_PROVIDER = "zhipu"
    elif _DEEPSEEK_API_KEY:
        LLM_PROVIDER = "deepseek"
    else:
        LLM_PROVIDER = "openai"

if LLM_PROVIDER == "deepseek":
    LLM_API_KEY = _DEEPSEEK_API_KEY
    LLM_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com")
    _DEFAULT_LLM_MODEL = "deepseek-v4-pro"
    _DEFAULT_EXTRACTION_MODEL = "deepseek-flash"
elif LLM_PROVIDER == "openai":
    LLM_API_KEY = _OPENAI_API_KEY
    LLM_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    _DEFAULT_LLM_MODEL = "gpt-4.1-mini"
    _DEFAULT_EXTRACTION_MODEL = "gpt-4.1-mini"
else:
    LLM_PROVIDER = "zhipu"
    LLM_API_KEY = _ZHIPU_API_KEY or _OPENAI_API_KEY
    LLM_API_BASE = os.getenv(
        "LLM_API_BASE",
        "https://open.bigmodel.cn/api/paas/v4/",
    )
    _DEFAULT_LLM_MODEL = "glm-4-plus"
    _DEFAULT_EXTRACTION_MODEL = "glm-4-flash"

# ============================================================
# 模型名称配置
# ============================================================

# 主 Agent 推理模型（强推理能力，用于规划和决策）
#   glm-4-plus   —— 智谱旗舰，综合能力最强
#   glm-4        —— 平衡版，性价比高
#   glm-4-air    —— 更快更便宜
LLM_MODEL = (
    os.getenv("DEEPSEEK_MODEL", _DEFAULT_LLM_MODEL)
    if LLM_PROVIDER == "deepseek"
    else os.getenv("LLM_MODEL", _DEFAULT_LLM_MODEL)
)
LLM_TEMPERATURE = 0.2

# DeepSeek V4 Pro 默认启用思考模式，但结构化 AgentAction 通过强制
# function-calling 获取；该组合目前不兼容，因此 Agent 决策路径默认关闭思考。
DEEPSEEK_THINKING = os.getenv("DEEPSEEK_THINKING", "disabled").strip().lower()
if DEEPSEEK_THINKING not in {"enabled", "disabled"}:
    raise ValueError("DEEPSEEK_THINKING must be 'enabled' or 'disabled'")

# 信息提取模型（工具内部调用，任务简单，用轻量模型节省成本）
#   glm-4-flash  —— 最便宜最快，够用于结构化提取
EXTRACTION_MODEL = (
    os.getenv("DEEPSEEK_EXTRACTION_MODEL", _DEFAULT_EXTRACTION_MODEL)
    if LLM_PROVIDER == "deepseek"
    else os.getenv("EXTRACTION_MODEL", _DEFAULT_EXTRACTION_MODEL)
)
EXTRACTION_TEMPERATURE = 0.0

# Embedding 模型
#   embedding-3  —— 智谱最新，输出 2048 维
#   embedding-2  —— 旧版，输出 1024 维
# ⚠️  如果已有知识库数据，切换模型前必须先运行 `python main.py --reset` 清空数据，
#     否则新旧向量维度不一致会导致检索异常。
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "embedding-3")
EMBEDDING_API_KEY = (
    os.getenv("EMBEDDING_API_KEY")
    or _ZHIPU_API_KEY
    or _OPENAI_API_KEY
    or LLM_API_KEY
)
EMBEDDING_API_BASE = os.getenv(
    "EMBEDDING_API_BASE",
    os.getenv("LLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4/"),
)

# ============================================================
# 检索 API 配置
# ============================================================
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
SEMANTIC_SCHOLAR_API_URL = "https://api.semanticscholar.org/graph/v1"
SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")

DEFAULT_SEARCH_MAX_RESULTS = 5
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "semantic_scholar")

# ============================================================
# 知识库配置（使用 FAISS；CHROMA_PERSIST_DIR 保留作为数据根目录基准）
# ============================================================
CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")  # 仅用于派生 faiss_index 路径
KB_DATA_DIR = os.getenv("KB_DATA_DIR", "./data")          # FAISS 索引存储根目录
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
RETRIEVAL_TOP_K = 5

# 文档处理安全上限
MAX_PDF_SIZE_MB = 50
MAX_PAPER_WORDS = 8000
# 快速 read_paper 抽取阶段的字符预算；Deep-Dive 使用 MAX_PAPER_WORDS。
MAX_EXTRACTION_CHARS = 15000

# ============================================================
# Agent 行为配置
# ============================================================
MAX_AGENT_ITERATIONS = 100  # LangGraph: ~2 recursions per tool call, need ~50 for full research
COVERAGE_CHECK_INTERVAL = 4       # 每存入 N 篇论文后提示做覆盖度评估
MIN_PAPERS_BEFORE_SUMMARY = 5     # 至少读 N 篇才允许生成综述

# Agent v2 显式状态图运行护栏（用于终止、复现和防止循环失控）。
# 它们不是项目的成本预算或对外卖点。
AGENT_V2_MAX_ITERATIONS = int(os.getenv("AGENT_V2_MAX_ITERATIONS", "12"))
AGENT_V2_MAX_SEARCHES = int(os.getenv("AGENT_V2_MAX_SEARCHES", "6"))
AGENT_V2_MAX_READS = int(os.getenv("AGENT_V2_MAX_READS", "5"))
AGENT_V2_TIMEOUT_SECONDS = int(os.getenv("AGENT_V2_TIMEOUT_SECONDS", "900"))
AGENT_V2_MAX_NO_PROGRESS = int(os.getenv("AGENT_V2_MAX_NO_PROGRESS", "2"))
AGENT_V2_CHECKPOINT_DB = os.getenv(
    "AGENT_V2_CHECKPOINT_DB",
    "./data/checkpoints/agent_v2.sqlite",
)

# ============================================================
# LangSmith 可观测性（可选）
# ============================================================
LANGSMITH_TRACING = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
LANGSMITH_PROJECT = os.getenv("LANGCHAIN_PROJECT", "research-copilot")

# ============================================================
# 输出配置
# ============================================================
OUTPUT_DIR = "./output"

# ============================================================
# AgentOps：运行轨迹、回放与维护
# ============================================================
RUNS_DIR = os.getenv("RUNS_DIR", "./data/runs")
RUN_RETENTION_DAYS = int(os.getenv("RUN_RETENTION_DAYS", "30"))
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "2026-08-21.2")
TOOL_SCHEMA_VERSION = os.getenv("TOOL_SCHEMA_VERSION", "1.0")
