"""
Tool: read_paper
职责：获取论文内容并提取结构化关键信息

核心设计："工具做重活，Agent 做决策"
- 工具内部：下载 PDF → 提取文本 → LLM 结构化抽取 → 返回压缩信息
- Agent 只看到约 200-300 词的结构化摘要，而非 5000+ 词的原始全文
- 这样保持 Agent 推理上下文简洁，决策质量更高

关键细节：
- arXiv abstract 页自动转换为 PDF 链接
- PDF 提取失败时回退到网页文本提取
- 下载文本先按词数限幅；快速结构化抽取再按字符预算截断，控制成本与上下文
"""

import logging
import re

from langchain_core.tools import tool

import config
from agent.prompts import PAPER_EXTRACTION_PROMPT
from utils.llm import make_extraction_llm

logger = logging.getLogger(__name__)


def create_read_paper_tool():

    @tool
    def read_paper(paper_url: str, target_claim: str = "") -> str:
        """Read a paper and extract structured key information.
        Use this when you want to deeply understand a specific paper found via search_papers.
        Returns: research problem, core method, key findings, limitations, and important references.

        Args:
            paper_url: URL of the paper (PDF link, arXiv page, or Semantic Scholar page)
            target_claim: 当前研究轴或待核查主张；用于输出显式证据判断
        """
        # Step 1: URL 标准化（arXiv abs → pdf）
        normalized_url = _normalize_url(paper_url)
        logger.info(f"Reading paper from: {normalized_url}")

        # Step 2: 获取论文文本
        paper_text = _fetch_paper_text(normalized_url)
        if paper_text.startswith("[错误]"):
            return paper_text

        # Step 3: 用 LLM 做结构化信息提取
        extracted = _extract_key_info(paper_text, target_claim=target_claim)
        return extracted

    return read_paper


def _normalize_url(url: str) -> str:
    """
    URL 标准化处理。

    arXiv 论文有多种 URL 格式：
    - https://arxiv.org/abs/2005.11401      → abstract 页面（HTML）
    - https://arxiv.org/pdf/2005.11401      → PDF 直链
    - https://arxiv.org/pdf/2005.11401v3    → 特定版本 PDF

    统一转换为 PDF 链接，因为 abstract 页面的文本提取质量远不如 PDF。
    """
    # arXiv abs → pdf
    arxiv_abs_match = re.match(r"https?://arxiv\.org/abs/(.+?)(?:\?.*)?$", url)
    if arxiv_abs_match:
        arxiv_id = arxiv_abs_match.group(1)
        return f"https://arxiv.org/pdf/{arxiv_id}.pdf"

    return url


def _fetch_paper_text(url: str) -> str:
    """
    从 URL 获取论文文本内容。

    策略：
    1. 下载内容，根据 Content-Type 判断是 PDF 还是 HTML
    2. PDF → PyMuPDF 提取文本
    3. HTML → 简单去标签提取纯文本
    4. 文本超过配置的词数上限时截断（通常保留 abstract + intro + method 前部）
    """
    import requests

    # 模拟浏览器 UA，避免被一些网站拦截
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ResearchCopilot/1.0)"
    }

    try:
        response = requests.get(url, timeout=20, allow_redirects=True, headers=headers)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        return f"[错误] 下载超时: {url}。建议：跳过此论文或稍后重试。"
    except requests.exceptions.HTTPError as e:
        return f"[错误] HTTP {response.status_code}: {url}。建议：检查链接或尝试其他论文。"
    except Exception as e:
        return f"[错误] 无法下载: {str(e)}。建议：跳过此论文。"

    content_type = response.headers.get("content-type", "").lower()

    # 根据内容类型选择提取策略
    if "pdf" in content_type or url.endswith(".pdf"):
        size_mb = len(response.content) / (1024 * 1024)
        if size_mb > config.MAX_PDF_SIZE_MB:
            return (
                f"[错误] 远程 PDF 过大（{size_mb:.1f} MB），"
                f"超过 {config.MAX_PDF_SIZE_MB} MB 上限。"
            )
        text = _extract_from_pdf(response.content)
    else:
        text = _extract_from_html(response.text)

    # 检查提取质量
    if len(text.strip()) < 200:
        return (
            f"[错误] 从 {url} 提取的文本内容过少（{len(text.strip())} 字符），"
            f"可能是访问受限或格式不支持。建议：跳过此论文或尝试其他链接。"
        )

    # 截断过长文本
    words = text.split()
    if len(words) > config.MAX_PAPER_WORDS:
        text = " ".join(words[:config.MAX_PAPER_WORDS])
        logger.info(
            f"Text truncated from {len(words)} to {config.MAX_PAPER_WORDS} words"
        )

    return text


def _extract_from_pdf(pdf_bytes: bytes) -> str:
    """
    从 PDF 二进制数据提取文本。

    使用 PyMuPDF（fitz）：轻量、快速、无需额外服务。
    逐页提取并拼接。
    """
    import fitz

    try:
        # 直接从内存打开，避免临时文件残留和路径竞争。
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text_parts = []
        try:
            for page in doc:
                text_parts.append(page.get_text())
        finally:
            doc.close()

        return "\n".join(text_parts)

    except Exception as e:
        logger.warning(f"PDF extraction failed: {e}")
        return ""



def _extract_from_html(html: str) -> str:
    """
    从 HTML 提取纯文本（简单去标签）。

    这是 fallback 策略：当 URL 不是 PDF 时使用。
    对于 arXiv abstract 页面、Semantic Scholar 页面等有基本效果。
    """
    from html.parser import HTMLParser

    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
            self._skip = False
            self._skip_tags = {"script", "style", "nav", "header", "footer"}

        def handle_starttag(self, tag, attrs):
            if tag in self._skip_tags:
                self._skip = True

        def handle_endtag(self, tag):
            if tag in self._skip_tags:
                self._skip = False

        def handle_data(self, data):
            if not self._skip:
                text = data.strip()
                if text:
                    self.parts.append(text)

    parser = TextExtractor()
    parser.feed(html)
    return " ".join(parser.parts)


def _extract_key_info(paper_text: str, target_claim: str = "") -> str:
    """
    用 LLM 从论文全文中提取结构化关键信息。

    设计要点：
    - 使用提取模型做结构化信息提取，temperature=0 保证确定性输出
    - 输出是自然语言结构化文本，不是 JSON（Agent 理解更自然）
    - 429 频率限制时自动重试（最多 3 次，间隔递增）
    """
    import time

    extraction_llm = make_extraction_llm()
    prompt = PAPER_EXTRACTION_PROMPT.format(
        paper_text=paper_text[:config.MAX_EXTRACTION_CHARS]
    )
    if target_claim.strip():
        prompt += f"""

当前需要核查的研究轴或主张：
{target_claim.strip()}

请在上述结构化信息之后追加以下两行：
**证据判断**：只能填写 supported、partial、contradicted、unknown 之一。
**判断依据**：用 1-2 句话说明论文原文为何支持、部分支持、反驳，或无法判断该主张。不得因为主题相关就自动判定 supported。
"""

    last_err = None
    for attempt in range(3):
        try:
            response = extraction_llm.invoke(prompt)
            return response.content
        except Exception as e:
            last_err = e
            err_str = str(e)
            # 429 频率限制 → 等待后重试
            if "429" in err_str or "1302" in err_str:
                wait = 20 * (attempt + 1)  # 20s, 40s, 60s
                logger.warning(f"Rate limit (429), retrying in {wait}s... (attempt {attempt+1}/3)")
                time.sleep(wait)
            else:
                break

    logger.error(f"LLM extraction failed: {last_err}")
    return f"[提取失败] 无法从论文中提取信息: {str(last_err)}。论文文本已获取但无法解析。"
