"""
Tool: assess_coverage
职责：评估当前调研对目标主题的覆盖度

这是整个项目的 **设计亮点 / 核心差异点**：

1. 赋予 Agent "元认知"能力 —— 不仅能做调研，还能评估自己做得好不好
2. 解决 Agent 的典型问题："浅尝辄止"（找几篇就急于总结）
3. 基于已读论文的实际内容（而非仅标题）做评估，判断更准确

实现细节：
- 从知识库中检索每篇论文的 method section，作为该论文的内容摘要
- 将 (标题, 内容摘要) 对传给评估 LLM
- 输出结构化的评估报告：已覆盖方向、空白方向、评分、下一步检索建议
"""

import logging
from langchain_core.tools import tool
import config
from agent.prompts import COVERAGE_ASSESSMENT_PROMPT
from utils.llm import make_extraction_llm

logger = logging.getLogger(__name__)


def create_assess_coverage_tool(knowledge_base):

    @tool
    def assess_coverage(topic: str) -> str:
        """Assess how well the current research covers the given topic.
        Call this periodically (every 3-5 papers) to check if important research areas are missing.
        Returns: covered areas, gaps, coverage score (1-10), and suggested next search keywords.

        Args:
            topic: The original research topic given by the user
        """
        all_titles = knowledge_base.get_all_titles()

        if not all_titles:
            return (
                "知识库中尚无论文记录。请先使用 search_papers 和 read_paper "
                "检索并阅读论文后再评估覆盖度。"
            )

        # ─── 核心增强：获取每篇论文的内容摘要 ───
        # 不只传标题，还从知识库中检索每篇论文的核心方法描述
        # 这样评估 LLM 能基于实际内容判断覆盖度
        papers_with_summaries = _build_paper_summaries(knowledge_base, all_titles)

        # 构造评估 prompt
        prompt_text = COVERAGE_ASSESSMENT_PROMPT.format(
            topic=topic,
            papers_with_summaries=papers_with_summaries,
        )

        assessment_llm = make_extraction_llm(temperature=0.1)

        try:
            response = assessment_llm.invoke(prompt_text)
            result = response.content

            # 在返回结果前追加统计信息，帮助 Agent 做决策
            stats = f"\n\n---\n📊 当前统计：已读 {len(all_titles)} 篇论文。"
            if len(all_titles) < config.MIN_PAPERS_BEFORE_SUMMARY:
                stats += f" （需至少 {config.MIN_PAPERS_BEFORE_SUMMARY} 篇才可生成综述）"

            return result + stats

        except Exception as e:
            logger.error(f"Coverage assessment failed: {e}")
            return f"[评估失败] {str(e)}。建议：继续检索和阅读论文，稍后再评估。"

    return assess_coverage


def _build_paper_summaries(knowledge_base, titles: list[str]) -> str:
    """
    为每篇论文构建简要摘要，用于覆盖度评估。

    策略：对每篇论文标题做知识库查询，取最相关的 1 条结果作为该论文的内容概要。
    这比只传标题给评估 LLM 信息量大得多，评估准确度显著提高。

    为什么不直接传所有 chunk？
    - 10 篇论文 * 平均 5 个 chunk = 50 条，太多会超出评估 LLM 的有效上下文
    - 每篇取 1 条 method 类 chunk 就够概括该论文的核心贡献
    """
    summaries = []

    for title in titles:
        # 用论文标题作为查询，检索该论文最相关的内容
        results = knowledge_base.query(
            query_text=f"core method of {title}",
            top_k=1,
        )

        if results:
            content, metadata = results[0]
            # 截断到 200 字符，避免整体过长
            summary = content[:200]
            summaries.append(f"**{title}**\n  核心内容：{summary}")
        else:
            summaries.append(f"**{title}**\n  核心内容：（无详细信息）")

    return "\n\n".join(summaries)
