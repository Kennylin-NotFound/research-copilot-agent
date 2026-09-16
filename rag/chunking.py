"""
文档切分模块

切分策略（优先级递减）：
1. 按语义 section 切分：识别 read_paper 提取的 **研究问题** / **核心方法** 等标记
2. 按段落切分：以空行为分隔符
3. 按固定长度切分：fallback，带重叠

section 切分的优势：
- 每个 chunk 是一个完整语义单元
- metadata 中记录 section_type，支持精准过滤检索
- 搜 "方法" 只命中 method chunk，而非在整篇内容中模糊匹配
"""

import re
import config


# read_paper 提取输出中的 section 标识（匹配 Markdown 加粗格式）
SECTION_PATTERNS = [
    ("problem",     r"\*\*研究问题\*\*[：:]\s*(.+?)(?=\n\*\*|\Z)"),
    ("method",      r"\*\*核心方法\*\*[：:]\s*(.+?)(?=\n\*\*|\Z)"),
    ("findings",    r"\*\*关键发现\*\*[：:]\s*(.+?)(?=\n\*\*|\Z)"),
    ("limitations", r"\*\*局限性\*\*[：:]\s*(.+?)(?=\n\*\*|\Z)"),
    ("references",  r"\*\*重要引用\*\*[：:]\s*(.+?)(?=\n\*\*|\Z)"),
]


def chunk_paper_content(content: str, paper_title: str) -> list[dict]:
    """
    将论文结构化信息切分为 chunk 列表。

    Returns:
        list[dict]: 每个 chunk 包含 "text" 和 "metadata"
    """
    chunks = []

    # 策略 1：按 section 标识切分
    for section_type, pattern in SECTION_PATTERNS:
        match = re.search(pattern, content, re.DOTALL)
        if not match:
            continue

        section_text = match.group(1).strip()
        if len(section_text) < 20:
            continue

        # section 过长时做二次切分
        if len(section_text) > config.CHUNK_SIZE:
            for sub in _split_by_length(section_text):
                chunks.append(_make_chunk(sub, paper_title, section_type))
        else:
            chunks.append(_make_chunk(section_text, paper_title, section_type))

    # 策略 2：如果 section 切分无结果，按段落切分
    if not chunks:
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        for p in paragraphs:
            if len(p) < 20:
                continue
            if len(p) > config.CHUNK_SIZE:
                for sub in _split_by_length(p):
                    chunks.append(_make_chunk(sub, paper_title, "unknown"))
            else:
                chunks.append(_make_chunk(p, paper_title, "unknown"))

    # 策略 3：仍然为空，整段按长度切分
    if not chunks:
        for sub in _split_by_length(content):
            chunks.append(_make_chunk(sub, paper_title, "unknown"))

    return chunks


def _make_chunk(text: str, paper_title: str, section_type: str) -> dict:
    return {
        "text": text,
        "metadata": {
            "paper_title": paper_title,
            "section_type": section_type,
        },
    }


def _split_by_length(text: str) -> list[str]:
    """按固定长度切分，带重叠。Fallback 策略。"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + config.CHUNK_SIZE
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - config.CHUNK_OVERLAP
    return chunks if chunks else [text.strip()]
