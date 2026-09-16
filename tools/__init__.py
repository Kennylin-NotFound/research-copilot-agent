"""
工具注册中心

设计理念：
- 所有工具在此统一注册，由 Agent 核心模块调用 get_all_tools() 获取
- 需要共享状态的工具（如知识库相关工具）通过依赖注入获取 KnowledgeBase 实例
- 无状态工具（如 search_papers）直接导入
"""

from tools.search_papers import create_search_papers_tool
from tools.search_papers_ranked import create_search_papers_ranked_tool
from tools.read_paper import create_read_paper_tool
from tools.read_local_pdf import create_read_local_pdf_tool
from tools.save_to_kb import create_save_to_kb_tool
from tools.query_knowledge import create_query_knowledge_tool
from tools.assess_coverage import create_assess_coverage_tool


def get_all_tools(knowledge_base):
    """
    构建并返回 Agent 可用的全部工具列表。

    为什么用工厂函数而不是直接导出 @tool？
    - save_to_kb 和 query_knowledge 需要访问 knowledge_base 实例
    - 通过工厂函数做依赖注入，避免全局变量
    - 方便测试时传入 mock 的 knowledge_base

    当前工具列表（7 个）：
    - search_papers:         关键词检索，按相关度排序（Tavily 或 Semantic Scholar）
    - search_papers_ranked:  关键词检索，按引用数 × 期刊/会议影响力综合排序（仅 S2）
    - read_paper:            从 URL 下载并提取论文结构化信息
    - read_local_pdf:        从本地 PDF 文件提取论文结构化信息
    - save_to_kb:            将论文信息存入 FAISS 向量知识库
    - query_knowledge:       从知识库语义检索
    - assess_coverage:       评估当前知识库对调研主题的覆盖度

    Args:
        knowledge_base: KnowledgeBase 实例，供知识库相关工具使用

    Returns:
        list: LangChain Tool 对象列表
    """
    return [
        create_search_papers_tool(),
        create_search_papers_ranked_tool(),
        create_read_paper_tool(),
        create_read_local_pdf_tool(),
        create_save_to_kb_tool(knowledge_base),
        create_query_knowledge_tool(knowledge_base),
        create_assess_coverage_tool(knowledge_base),
    ]
