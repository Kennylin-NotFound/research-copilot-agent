"""
LLM 工厂函数

统一管理所有 ChatOpenAI 和 Embeddings 实例的创建，屏蔽 provider 差异。

聊天模型支持智谱、DeepSeek 与 OpenAI 的兼容接口。Embedding 使用独立配置，
避免切换到不提供 embedding 接口的聊天模型后破坏现有 FAISS 知识库。

所有调用 LLM 的地方统一使用本模块的工厂函数，
之后若再切换 provider，只需修改 config.py 和本文件，其余代码零改动。
"""

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
import config


def _chat_provider_kwargs() -> dict:
    """Return provider-specific Chat Completions options."""
    if config.LLM_PROVIDER == "deepseek":
        return {
            "extra_body": {
                "thinking": {"type": config.DEEPSEEK_THINKING},
            }
        }
    return {}


def make_chat_llm(
    model: str | None = None,
    temperature: float | None = None,
) -> ChatOpenAI:
    """
    创建主推理 LLM（Agent 大脑）。

    Args:
        model: 覆盖默认模型（config.LLM_MODEL）
        temperature: 覆盖默认 temperature（config.LLM_TEMPERATURE）
    """
    return ChatOpenAI(
        model=model or config.LLM_MODEL,
        temperature=temperature if temperature is not None else config.LLM_TEMPERATURE,
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_API_BASE,
        **_chat_provider_kwargs(),
    )


def make_extraction_llm(temperature: float | None = None) -> ChatOpenAI:
    """
    创建信息提取专用 LLM（轻量小模型，用于 read_paper / assess_coverage 内部）。

    为什么单独拆一个函数：
    - 提取任务不需要强推理能力，用更便宜的小模型即可
    - glm-4-flash 比 glm-4 便宜约 10 倍，速度也更快
    - temperature 默认 0（确定性输出）
    """
    return ChatOpenAI(
        model=config.EXTRACTION_MODEL,
        temperature=temperature if temperature is not None else config.EXTRACTION_TEMPERATURE,
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_API_BASE,
        **_chat_provider_kwargs(),
    )


def make_embeddings() -> OpenAIEmbeddings:
    """
    创建 Embedding 模型（向量化，用于知识库存储和检索）。

    智谱 embedding-3 的输出维度为 2048，与 OpenAI text-embedding-3-small（1536 维）不同。
    如果之前已有存量数据，切换模型前需要先清空知识库（python main.py --reset），
    否则新旧向量维度不一致会导致检索错误。
    """
    return OpenAIEmbeddings(
        model=config.EMBEDDING_MODEL,
        api_key=config.EMBEDDING_API_KEY,
        base_url=config.EMBEDDING_API_BASE,
        # 智谱不支持 OpenAI 的 dimensions 参数，关闭 token 长度检查
        check_embedding_ctx_length=False,
    )
