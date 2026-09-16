"""
Tool: read_local_pdf
职责：从本地 PDF 文件提取结构化学术信息

与 read_paper 的区别：
- read_paper：输入 URL，先下载 PDF（或抓 HTML），再提取
- read_local_pdf：输入本地文件路径，跳过下载步骤，直接读取用户上传/已下载的 PDF

适用场景（对应 SCENARIOS.md S2 单篇精读）：
- 用户本地已有 PDF，无法通过公开 URL 获取（如付费论文、内部文档）
- 用户想精读一篇具体论文，通过 --file 参数传入路径

复用逻辑：
- 文本提取：复用 read_paper._extract_from_pdf()（基于 PyMuPDF）
- 信息提取：复用 read_paper._extract_key_info()（基于 LLM + PAPER_EXTRACTION_PROMPT）
- 保持两处逻辑同步的策略：直接 import，不重复实现
"""

import logging
import os

from langchain_core.tools import tool

import config

logger = logging.getLogger(__name__)

# 支持的文件扩展名
_SUPPORTED_EXTENSIONS = {".pdf"}

# 单文件文本长度上限（词数），与 read_paper 保持一致
_MAX_WORDS = config.MAX_PAPER_WORDS


def create_read_local_pdf_tool():

    @tool
    def read_local_pdf(file_path: str) -> str:
        """Read a local PDF file and extract structured key information.

        Use this when the user has provided a local PDF file path (e.g. via --file argument),
        rather than a URL. Extracts the same structured information as read_paper:
        research problem, core method, key findings, limitations, and references.

        Args:
            file_path: Absolute or relative path to a local PDF file.
                       Examples: "C:/Users/user/papers/react.pdf"
                                 "./downloads/attention_is_all_you_need.pdf"
        """
        # ── Step 1: 路径校验 ──────────────────────────────────
        # 展开相对路径（相对于项目根目录）
        if not os.path.isabs(file_path):
            file_path = os.path.abspath(file_path)

        if not os.path.exists(file_path):
            return (
                f"[错误] 文件不存在: {file_path}\n"
                f"建议：检查路径是否正确，注意 Windows 路径分隔符（可用 / 或 \\\\）。"
            )

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in _SUPPORTED_EXTENSIONS:
            return (
                f"[错误] 不支持的文件格式: {ext}。"
                f"当前仅支持 PDF 文件（{', '.join(_SUPPORTED_EXTENSIONS)}）。"
            )

        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if file_size_mb > config.MAX_PDF_SIZE_MB:
            return (
                f"[错误] 文件过大（{file_size_mb:.1f} MB），"
                f"超过 {config.MAX_PDF_SIZE_MB} MB 上限。"
                f"建议：检查文件是否为扫描版大图 PDF，或手动提取前几页。"
            )

        logger.info(f"Reading local PDF: {file_path} ({file_size_mb:.1f} MB)")

        # ── Step 2: 提取文本（复用 read_paper 的 PyMuPDF 逻辑）────
        try:
            with open(file_path, "rb") as f:
                pdf_bytes = f.read()
        except PermissionError:
            return f"[错误] 无权限读取文件: {file_path}。建议：检查文件权限。"
        except Exception as e:
            return f"[错误] 读取文件失败: {e}"

        # 直接复用 read_paper 中的 PDF 提取函数，保证逻辑一致
        from tools.read_paper import _extract_from_pdf, _extract_key_info

        paper_text = _extract_from_pdf(pdf_bytes)

        if not paper_text or len(paper_text.strip()) < 200:
            return (
                f"[错误] 从 PDF 提取的文本内容过少（{len(paper_text.strip())} 字符）。\n"
                f"可能原因：① 扫描版 PDF（图片而非文字），② 加密 PDF，③ 文件损坏。\n"
                f"建议：尝试用 PDF 阅读器确认文件可正常显示文字。"
            )

        # 截断超长文本（与 read_paper 一致的处理方式）
        words = paper_text.split()
        if len(words) > _MAX_WORDS:
            paper_text = " ".join(words[:_MAX_WORDS])
            logger.info(
                f"Text truncated from {len(words)} to {_MAX_WORDS} words "
                f"(file: {os.path.basename(file_path)})"
            )

        # ── Step 3: LLM 结构化信息提取 ────────────────────────
        filename = os.path.basename(file_path)
        logger.info(f"Extracting key info from: {filename}")

        extracted = _extract_key_info(paper_text)

        # 在输出头部附加文件来源，便于 Agent / 用户追溯
        source_note = f"[来源：本地文件 {filename}]\n\n"
        return source_note + extracted

    return read_local_pdf
