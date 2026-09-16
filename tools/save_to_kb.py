"""
Tool: save_to_kb
职责：将论文关键信息存入向量知识库

设计考量：
- 基于论文标题做去重，避免重复存储
- 存储的是 read_paper 提取后的结构化信息，不是原始全文
- 返回当前知识库统计，帮助 Agent 判断是否该做覆盖度评估
"""

import logging
from langchain_core.tools import tool
import config

logger = logging.getLogger(__name__)


def create_save_to_kb_tool(knowledge_base):

    @tool
    def save_to_kb(paper_title: str, content: str) -> str:
        """Save extracted paper information to the knowledge base for future retrieval.
        Call this after read_paper to persist the paper's key information.

        Args:
            paper_title: Title of the paper (used for deduplication)
            content: The extracted key information from read_paper output
        """
        # 去重
        if knowledge_base.has_document(paper_title):
            return f"论文 '{paper_title}' 已存在于知识库中，跳过重复存储。"

        try:
            knowledge_base.add_document(title=paper_title, content=content)
            knowledge_base.flush()  # 批量写盘（仅在有新数据时触发）
        except Exception as e:
            logger.error(f"Failed to save to KB: {e}")
            return f"[存储失败] {str(e)}。论文信息未能存入知识库，但不影响后续操作。"

        doc_count = knowledge_base.get_document_count()

        # 构造返回信息，包含覆盖度评估提醒
        msg = f"✅ 已存入知识库：'{paper_title}'。当前共 {doc_count} 篇论文。"

        if doc_count % config.COVERAGE_CHECK_INTERVAL == 0:
            msg += f"\n💡 已读 {doc_count} 篇论文，建议调用 assess_coverage 评估当前覆盖度。"

        return msg

    return save_to_kb
