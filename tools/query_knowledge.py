"""
Tool: query_knowledge
职责：从向量知识库中检索与问题相关的论文信息

设计考量：
- 返回结果附带来源论文标题和 section 类型，保证可溯源
- 支持按 section_type 过滤（如只检索 method 类内容）
- Agent 在综述生成阶段会频繁调用此工具
"""

import logging
from langchain_core.tools import tool
import config

logger = logging.getLogger(__name__)


def create_query_knowledge_tool(knowledge_base):

    @tool
    def query_knowledge(question: str, section_filter: str = "") -> str:
        """Query the knowledge base for information related to a question.
        Use this to retrieve previously saved paper information when writing the survey
        or answering follow-up questions.

        Args:
            question: The question to search for, e.g. "What methods are used for dense retrieval?"
            section_filter: Optional filter by section type: "problem", "method", "findings", "limitations". Leave empty for all sections.
        """
        normalized_filter = section_filter if section_filter else None
        if hasattr(knowledge_base, "query_with_diagnostics"):
            results, diagnostics = knowledge_base.query_with_diagnostics(
                query_text=question,
                top_k=config.RETRIEVAL_TOP_K,
                section_filter=normalized_filter,
            )
        else:
            # 兼容测试中的轻量 mock / 旧实现。
            results = knowledge_base.query(
                query_text=question,
                top_k=config.RETRIEVAL_TOP_K,
                section_filter=normalized_filter,
            )
            diagnostics = {"fallback_used": False}

        if not results:
            if section_filter:
                return (
                    f"知识库中未找到与 '{question}' 相关的 {section_filter} 类信息。"
                    f"尝试去掉 section_filter 参数搜索全部内容。"
                )
            return "知识库中未找到相关信息。可能需要先检索和阅读更多论文。"

        formatted = []
        for i, (content, metadata) in enumerate(results, 1):
            source = metadata.get("paper_title", "未知来源")
            section = metadata.get("section_type", "")
            section_label = f" [{section}]" if section and section != "unknown" else ""

            formatted.append(
                f"[{i}] 来源: 《{source}》{section_label}\n"
                f"    内容: {content}\n"
            )

        prefix = f"从知识库中检索到 {len(results)} 条相关信息"
        if diagnostics.get("fallback_used"):
            prefix += (
                f"（未命中 section_filter={section_filter!r}，"
                "以下为未过滤的语义检索 fallback）"
            )
        return prefix + ":\n\n" + "\n".join(formatted)

    return query_knowledge
