"""
Tool: search_papers_ranked
职责：检索学术论文，并按「综合学术影响力」排序返回结果

与 search_papers 的区别：
- search_papers：按 Semantic Scholar 相关度排序（默认）或 Tavily 搜索引擎排序
- search_papers_ranked：仅支持 Semantic Scholar，额外拉取 venue 字段，
  按「引用量 × 期刊/会议影响力权重」综合打分后重排

综合分公式：
  composite_score = log(1 + citations) × venue_weight

  - log 压缩量级差异：100 引 vs 10000 引 的差距从 100x 压缩到 2x
  - venue_weight：顶会/顶刊 1.5，次顶 1.2，arXiv 预印本 0.9，其他 1.0
  - 效果：100 引 × NeurIPS(1.5) ≈ 7.0 > 500 引 × unknown(1.0) ≈ 6.2
    → 顶会少引用论文能排在普通高引论文前面，符合学术质量直觉

支持参数：
  - min_year：过滤发表年份（过滤掉太旧的综述或已被超越的方法）
  - min_citations：过滤低引用量（去噪，过滤还没被检验的极新工作）

⚠️ 仅支持 Semantic Scholar 后端；Tavily 不返回结构化 venue/citation 数据。
"""

import logging
import math
from langchain_core.tools import tool
import config

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 期刊 / 会议影响力权重表
# ──────────────────────────────────────────────

# Tier 1：AI/ML 旗舰，权威性最高（weight = 1.5）
_TIER1_VENUES = [
    # Machine Learning
    "neurips", "nips", "neural information processing",
    "icml", "international conference on machine learning",
    "iclr", "international conference on learning representations",
    # NLP
    "acl", "association for computational linguistics",
    "emnlp", "empirical methods in natural language processing",
    "naacl", "north american chapter",
    # Computer Vision
    "cvpr", "computer vision and pattern recognition",
    "iccv", "international conference on computer vision",
    "eccv", "european conference on computer vision",
    # Data Mining / Web
    "kdd", "sigkdd", "knowledge discovery",
    "www", "the web conference", "world wide web",
    # Top Journals
    "nature", "science", "jmlr", "journal of machine learning",
    "transactions on pattern analysis",  # IEEE TPAMI
    "artificial intelligence",           # AIJ
]

# Tier 2：领域重要会议（weight = 1.2）
_TIER2_VENUES = [
    "aaai", "association for the advancement of artificial intelligence",
    "ijcai", "international joint conference on artificial intelligence",
    "coling", "computational linguistics",
    "acm mm", "acm multimedia",
    "sigir", "research and development in information retrieval",
    "wsdm", "web search and data mining",
    "cikm", "information and knowledge management",
    "uai", "uncertainty in artificial intelligence",
    "aistats", "artificial intelligence and statistics",
    "eacl", "european chapter",
    "findings",  # ACL Findings 子刊
    "corl", "conference on robot learning",
    "iros", "intelligent robots and systems",
    "icra", "international conference on robotics",
]

# arXiv 预印本（weight = 0.9）—— 未经同行评审，适当降权
_ARXIV_MARKERS = ["arxiv", "corr", "preprint"]


def _get_venue_weight(venue: str) -> float:
    """
    根据期刊/会议名称返回影响力权重。

    匹配策略：将 venue 转为小写后做子串匹配，兼容 Semantic Scholar
    返回的各种写法（全称、缩写、带年份等）。

    Args:
        venue: Semantic Scholar 返回的 venue 字段字符串

    Returns:
        float: 1.5（顶会）/ 1.2（次顶）/ 0.9（arXiv）/ 1.0（其他）
    """
    if not venue:
        return 1.0

    v = venue.lower()

    for marker in _TIER1_VENUES:
        if marker in v:
            return 1.5

    for marker in _TIER2_VENUES:
        if marker in v:
            return 1.2

    for marker in _ARXIV_MARKERS:
        if marker in v:
            return 0.9

    return 1.0


def _composite_score(citation_count: int, venue: str) -> float:
    """
    计算论文综合学术影响力分数。

    公式：log(1 + citations) × venue_weight
    - log 压缩引用量级差异，使期刊权重有实质影响
    - venue_weight 来自 _get_venue_weight()
    """
    return math.log1p(citation_count) * _get_venue_weight(venue)


def create_search_papers_ranked_tool():

    @tool
    def search_papers_ranked(
        query: str,
        max_results: int = 8,
        min_year: int = None,
        min_citations: int = 0,
    ) -> str:
        """Search academic papers and rank by comprehensive academic influence.

        Unlike search_papers (which ranks by text relevance), this tool scores each paper
        by combining citation count and publication venue prestige, then returns the
        top results ranked by that composite score.

        Scoring formula: log(1 + citations) × venue_weight
          - venue_weight: 1.5 for top venues (NeurIPS/ICML/ICLR/ACL/CVPR/KDD/WWW...),
                          1.2 for major venues (AAAI/IJCAI/SIGIR/WSDM...),
                          0.9 for arXiv preprints, 1.0 for others
          - log compression: reduces the gap between 100-citation and 10000-citation papers,
            so a NeurIPS paper with 100 citations can outrank an unknown-venue paper with 500.

        Use this when you want high-quality, influential papers on a topic,
        especially when looking for seminal works or benchmarking the field.
        Only works with Semantic Scholar backend (not Tavily).

        Args:
            query: Search keywords in English
            max_results: Number of top-ranked results to return (default 8, max 20)
            min_year: Only include papers published >= this year (e.g. 2020). Optional.
            min_citations: Only include papers with >= this many citations (default 0).
        """
        max_results = max(1, min(max_results, 20))
        min_citations = max(0, min_citations)
        return _search_ranked(query, max_results, min_year, min_citations)

    return search_papers_ranked


def _search_ranked(
    query: str,
    max_results: int,
    min_year: int,
    min_citations: int,
) -> str:
    """
    Semantic Scholar 检索 + 综合打分 + 重排。

    流程：
    1. 多拉一倍结果（max_results * 2），为过滤和重排留余量
    2. 按 min_year / min_citations 过滤
    3. 对每篇论文算 composite_score
    4. 按分数降序排列，取前 max_results 条
    5. 格式化输出，附带分数和场馆标签
    """
    import requests

    # 多拉一倍用于过滤后重排
    fetch_limit = min(max_results * 2, 100)

    api_url = f"{config.SEMANTIC_SCHOLAR_API_URL}/paper/search"
    params = {
        "query": query,
        "limit": fetch_limit,
        # venue：论文发表的期刊/会议名称
        # publicationVenue：更规范的 venue 对象（含 type: journal/conference）
        "fields": (
            "title,authors,year,abstract,citationCount,"
            "openAccessPdf,externalIds,url,venue,publicationVenue"
        ),
    }
    headers = {}
    if config.SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = config.SEMANTIC_SCHOLAR_API_KEY

    try:
        resp = requests.get(api_url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        papers = resp.json().get("data", [])
    except requests.exceptions.Timeout:
        return "[检索超时] Semantic Scholar 响应超时，建议稍后重试。"
    except requests.exceptions.HTTPError as e:
        if resp.status_code == 429:
            return "[检索限流] 请求过于频繁，请等待几秒后重试。"
        return f"[检索失败] HTTP {resp.status_code}: {e}"
    except Exception as e:
        return f"[检索失败] {e}"

    if not papers:
        return f"未找到与 '{query}' 相关的论文。建议尝试不同关键词。"

    # ── 过滤 ──────────────────────────────────
    if min_year:
        papers = [p for p in papers if (p.get("year") or 0) >= min_year]
    if min_citations > 0:
        papers = [p for p in papers if (p.get("citationCount") or 0) >= min_citations]

    if not papers:
        return (
            f"检索到结果但过滤条件（min_year={min_year}, "
            f"min_citations={min_citations}）过严，无剩余论文。建议放宽条件。"
        )

    # ── 打分 & 排序 ────────────────────────────
    def get_venue_str(p: dict) -> str:
        """提取最准确的 venue 名称（优先 publicationVenue，回退 venue 字段）"""
        pub_venue = p.get("publicationVenue") or {}
        return pub_venue.get("name") or p.get("venue") or ""

    scored = []
    for p in papers:
        venue_str = get_venue_str(p)
        citations = p.get("citationCount") or 0
        score = _composite_score(citations, venue_str)
        scored.append((score, venue_str, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max_results]

    # ── 格式化输出 ─────────────────────────────
    from tools.search_papers import _get_best_url  # 复用已有的 URL 优先级逻辑

    lines = []
    for rank, (score, venue_str, p) in enumerate(top, 1):
        authors_list = p.get("authors", [])
        authors = ", ".join(a.get("name", "") for a in authors_list[:3])
        if len(authors_list) > 3:
            authors += " et al."

        citations = p.get("citationCount") or 0
        year = p.get("year", "N/A")
        abstract = (p.get("abstract") or "无摘要")[:280]
        paper_url = _get_best_url(p)

        venue_weight = _get_venue_weight(venue_str)
        # 给 venue 加标签，便于 Agent 理解为什么这篇排在前面
        if venue_weight == 1.5:
            venue_tag = f"[顶会/顶刊] {venue_str}" if venue_str else "[顶会/顶刊]"
        elif venue_weight == 1.2:
            venue_tag = f"[重要会议] {venue_str}" if venue_str else "[重要会议]"
        elif venue_weight == 0.9:
            venue_tag = "[arXiv 预印本]"
        else:
            venue_tag = venue_str or "未知来源"

        lines.append(
            f"[{rank}] 标题: {p.get('title', 'N/A')}\n"
            f"    作者: {authors}\n"
            f"    年份: {year} | 引用数: {citations} | 来源: {venue_tag}\n"
            f"    综合分: {score:.2f}\n"
            f"    链接: {paper_url}\n"
            f"    摘要: {abstract}\n"
        )

    filter_info = ""
    if min_year:
        filter_info += f"，年份 ≥ {min_year}"
    if min_citations > 0:
        filter_info += f"，引用 ≥ {min_citations}"

    header = (
        f"检索关键词: \"{query}\"{filter_info}\n"
        f"返回 {len(top)} 篇（按综合影响力排序，顶会/顶刊加权）:\n\n"
    )
    return header + "\n".join(lines)
