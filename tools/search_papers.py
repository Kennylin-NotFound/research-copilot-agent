"""
Tool: search_papers
职责：根据关键词检索学术论文，返回结构化的论文列表

双后端设计：
- Semantic Scholar（默认）：免费、无需 API Key、返回标准学术元数据（引用数、年份、作者）
- Tavily：搜索质量更高（底层是搜索引擎），但需要 API Key

关键设计：
- 返回引用数（citationCount）帮助 Agent 判断论文重要性
- 返回 openAccessPdf URL 供 read_paper 直接下载 PDF
- 异常不抛出，返回文本错误信息让 Agent 自行决策
"""

import logging
from langchain_core.tools import tool
import config

logger = logging.getLogger(__name__)


def create_search_papers_tool():

    @tool
    def search_papers(query: str, max_results: int = 5) -> str:
        """Search for academic papers by keyword.
        Use this when you need to find papers on a specific topic.
        Returns a list of papers with title, authors, year, citation count, abstract, and URL.

        Args:
            query: Search keywords in English, e.g. "retrieval augmented generation optimization"
            max_results: Number of papers to return (default 5, max 10)
        """
        max_results = max(1, min(max_results, 10))

        if config.SEARCH_PROVIDER == "tavily":
            return _search_via_tavily(query, max_results)
        else:
            return _search_via_semantic_scholar(query, max_results)

    return search_papers


def _search_via_semantic_scholar(query: str, max_results: int) -> str:
    """
    通过 Semantic Scholar API 检索论文（免费、无需 API Key）。

    请求字段：
    - title, authors, year, abstract：基本元数据
    - citationCount：引用数，帮助 Agent 判断论文影响力
    - openAccessPdf：开放获取 PDF 链接，供 read_paper 直接下载
    - externalIds：包含 arXiv ID 等，用于构造 arXiv 链接
    - url：Semantic Scholar 论文页面链接
    """
    import requests

    api_url = f"{config.SEMANTIC_SCHOLAR_API_URL}/paper/search"
    params = {
        "query": query,
        "limit": max_results,
        "fields": "title,authors,year,abstract,citationCount,openAccessPdf,externalIds,url",
    }
    headers = {}
    if config.SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = config.SEMANTIC_SCHOLAR_API_KEY

    try:
        resp = requests.get(api_url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        return f"[检索超时] Semantic Scholar 响应超时。建议：稍后重试或换一组关键词。"
    except requests.exceptions.HTTPError as e:
        if resp.status_code == 429:
            return "[检索限流] Semantic Scholar API 请求过于频繁。建议：等待几秒后重试。"
        return f"[检索失败] HTTP {resp.status_code}: {str(e)}。建议：换一组关键词重新检索。"
    except Exception as e:
        return f"[检索失败] {str(e)}。建议：换一组关键词重新检索。"

    papers = data.get("data", [])
    if not papers:
        return f"未找到与 '{query}' 相关的论文。建议：尝试更宽泛的关键词或换一个角度描述。"

    formatted = []
    for i, p in enumerate(papers, 1):
        # 作者：取前 3 个，超出显示 et al.
        authors_list = p.get("authors", [])
        authors = ", ".join([a.get("name", "") for a in authors_list[:3]])
        if len(authors_list) > 3:
            authors += " et al."

        # 摘要：截断到 300 字符
        abstract = (p.get("abstract") or "无摘要")[:300]

        # 引用数
        citations = p.get("citationCount", 0) or 0

        # 获取可用的 PDF/论文链接（优先 openAccessPdf → arXiv → Semantic Scholar 页面）
        paper_url = _get_best_url(p)

        formatted.append(
            f"[{i}] 标题: {p.get('title', 'N/A')}\n"
            f"    作者: {authors}\n"
            f"    年份: {p.get('year', 'N/A')} | 引用数: {citations}\n"
            f"    链接: {paper_url}\n"
            f"    摘要: {abstract}\n"
        )

    header = f"检索关键词: \"{query}\"\n找到 {len(papers)} 篇论文（按相关度排序）:\n\n"
    return header + "\n".join(formatted)


def _get_best_url(paper: dict) -> str:
    """
    从 Semantic Scholar 返回的论文数据中提取最佳链接。

    优先级：
    1. openAccessPdf（可直接下载 PDF，read_paper 最友好）
    2. arXiv 页面（可以转换为 PDF 链接）
    3. Semantic Scholar 论文页面（fallback）
    """
    # 1. 开放获取 PDF
    oa_pdf = paper.get("openAccessPdf")
    if oa_pdf and oa_pdf.get("url"):
        return oa_pdf["url"]

    # 2. arXiv 链接
    external_ids = paper.get("externalIds", {}) or {}
    arxiv_id = external_ids.get("ArXiv")
    if arxiv_id:
        return f"https://arxiv.org/abs/{arxiv_id}"

    # 3. Semantic Scholar 页面
    return paper.get("url", "N/A")


def _search_via_tavily(query: str, max_results: int) -> str:
    """
    通过 Tavily Search API 检索论文。

    优势：搜索质量高（底层是搜索引擎），能搜到非学术来源的技术博客和文档。
    劣势：需要 API Key；返回的元数据不如 Semantic Scholar 规范。
    """
    from tavily import TavilyClient

    try:
        client = TavilyClient(api_key=config.TAVILY_API_KEY)
        academic_query = f"academic paper research: {query}"
        response = client.search(
            query=academic_query,
            max_results=max_results,
            search_depth="advanced",
            include_answer=False,
            include_domains=["arxiv.org", "semanticscholar.org", "aclanthology.org",
                             "openreview.net", "proceedings.neurips.cc"],
        )
    except Exception as e:
        return f"[检索失败] {str(e)}。建议：换一组关键词重新检索。"

    results = response.get("results", [])
    if not results:
        return f"未找到与 '{query}' 相关的论文。建议：尝试更宽泛或不同角度的关键词。"

    formatted = []
    for i, r in enumerate(results, 1):
        formatted.append(
            f"[{i}] 标题: {r.get('title', 'N/A')}\n"
            f"    链接: {r.get('url', 'N/A')}\n"
            f"    摘要: {r.get('content', 'N/A')[:300]}\n"
        )

    return f"检索关键词: \"{query}\"\n找到 {len(results)} 篇相关论文:\n\n" + "\n".join(formatted)
