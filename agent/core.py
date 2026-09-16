"""
Agent 核心模块 —— ResearchCopilotAgent

LangChain 1.x API（基于 LangGraph）：
  - 使用 create_agent()，返回 CompiledStateGraph
  - 状态基于消息列表（messages），Tool Calling 原生支持
  - 通过 graph.stream() 打印推理过程，graph.invoke() 获取最终结果
  - 最大迭代次数通过 config={"recursion_limit": N} 传入
"""

import logging
import os
import re
import time
import uuid
from collections import Counter

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from agent.prompts import AGENT_SYSTEM_PROMPT, DEEP_DIVE_PROMPT
from tools import get_all_tools
from rag.knowledge_base import KnowledgeBase
from utils.llm import make_chat_llm
import config

logger = logging.getLogger(__name__)


class ResearchCopilotAgent:
    """Research Copilot 的核心 Agent 类"""

    def __init__(self, knowledge_base=None, llm=None, tools=None, recorder=None):
        self._log_langsmith_status()

        # 1. 初始化 LLM
        self.llm = llm if llm is not None else make_chat_llm()

        # 2. 初始化知识库（通过依赖注入传递给工具）
        self.knowledge_base = (
            knowledge_base if knowledge_base is not None else KnowledgeBase()
        )

        # 3. 初始化工具集
        self.tools = (
            tools if tools is not None
            else get_all_tools(knowledge_base=self.knowledge_base)
        )
        self.recorder = recorder

        # 4. 创建 Agent（LangChain 1.x API）
        #    system_prompt 直接传入，不再需要手动构建 ChatPromptTemplate
        self.graph = create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=AGENT_SYSTEM_PROMPT.format(
                min_papers=config.MIN_PAPERS_BEFORE_SUMMARY,
            ),
        )

        # 调用配置（recursion_limit 对应旧版的 max_iterations）
        self._invoke_config = {
            "recursion_limit": config.MAX_AGENT_ITERATIONS,
        }

        logger.info(
            f"Agent initialized: model={config.LLM_MODEL}, "
            f"tools={[t.name for t in self.tools]}"
        )

    def run(self, research_topic: str) -> dict:
        """
        执行一次完整的文献调研任务。

        使用 stream() 逐步打印推理过程（替代旧版的 verbose=True），
        最终返回消息列表中最后一条 AIMessage 的内容。
        """
        logger.info(f"Starting research: {research_topic}")
        run_id = uuid.uuid4().hex
        started_at = time.perf_counter()
        termination_reason = "completed"
        recursion_reached = False
        session = self._start_recording("survey", research_topic, run_id)

        input_state = {"messages": [HumanMessage(research_topic)]}
        all_messages = []
        step_count = 0

        # stream() 每步返回当前节点产生的状态增量
        try:
            for chunk in self.graph.stream(
                input_state,
                config=self._invoke_config,
                stream_mode="updates",
            ):
                for node_name, node_output in chunk.items():
                    msgs = node_output.get("messages", [])
                    all_messages.extend(msgs)
                    step_count += 1
                    self._print_step(node_name, msgs)
                    if session is not None:
                        session.record_event(
                            "graph_step",
                            self._summarize_messages(node_name, msgs, step_count),
                        )
        except Exception as e:
            err_name = type(e).__name__
            err_msg = str(e).lower()
            if "recursion" in err_name.lower() or "recursion" in err_msg:
                recursion_reached = True
                termination_reason = "recursion_limit"
                papers_so_far = self.knowledge_base.get_document_count()
                logger.warning(
                    f"Recursion limit reached after {step_count} steps / {papers_so_far} papers. "
                    f"Generating summary from collected data."
                )
                print(f"\n[Agent] 已达到最大步数限制，基于已收集的 {papers_so_far} 篇论文生成综述...")
            else:
                if session is not None:
                    session.fail(err_name, str(e))
                raise

        # 最终输出：messages 列表中最后一条 AI 消息
        output = self._extract_final_output(all_messages)

        # 如果没有最终 AI 文本输出（递归中断、Agent 还未生成综述），
        # 从知识库收集的内容合成一份简要综述
        papers_count = self.knowledge_base.get_document_count()
        # 达到递归上限时，最后一条 AIMessage 可能只是中间思考而非最终综述。
        # 因此只要已有论文，就强制走 KB fallback，避免把 thought 误当最终结果。
        if recursion_reached and papers_count > 0:
            output = self._generate_fallback_summary(research_topic)
        elif output == "（Agent 未产生文本输出）" and papers_count > 0:
            termination_reason = "fallback_no_final_output"
            output = self._generate_fallback_summary(research_topic)
        elif output == "（Agent 未产生文本输出）":
            termination_reason = "no_output"

        # 中间步骤（工具调用记录），用于评估
        intermediate_steps = self._extract_tool_steps(all_messages)
        tool_call_counts = Counter(step[0] for step in intermediate_steps)
        duration_ms = round((time.perf_counter() - started_at) * 1000)
        trace = {
            "run_id": run_id,
            "termination_reason": termination_reason,
            "duration_ms": duration_ms,
            "graph_steps": step_count,
            "tool_calls": len(intermediate_steps),
            "tool_call_counts": dict(tool_call_counts),
            "papers_count": papers_count,
            "prompt_version": config.PROMPT_VERSION,
            "tool_schema_version": config.TOOL_SCHEMA_VERSION,
        }

        if session is not None:
            session.finish(
                {
                    **trace,
                    "output_chars": len(output),
                    "tool_error_count": sum(
                        1
                        for _, _, value in intermediate_steps
                        if str(value).startswith(("[错误]", "[失败]"))
                    ),
                }
            )

        logger.info(
            f"Research completed. Papers: {papers_count}, Steps: {step_count}"
        )

        return {
            "output": output,
            "intermediate_steps": intermediate_steps,
            "papers_count": papers_count,
            "trace": trace,
        }

    def _generate_fallback_summary(self, topic: str) -> str:
        """
        当 Agent 未能在递归限制内完成综述时，直接查询知识库生成摘要。
        """
        titles = self.knowledge_base.get_all_titles()
        docs = self.knowledge_base.query(topic, top_k=8)

        context_parts = []
        for content, meta in docs:
            title = meta.get("paper_title", "Unknown")
            context_parts.append(f"【{title}】\n{content[:400]}")

        context = "\n\n".join(context_parts)

        prompt = (
            f"请根据以下已读论文内容，为主题'{topic}'生成一份中文综述摘要（500字以内）。\n\n"
            f"已读论文：{', '.join(titles)}\n\n"
            f"论文内容摘录：\n{context}"
        )

        try:
            response = self.llm.invoke(prompt)
            return response.content
        except Exception as e:
            logger.error(f"Fallback summary failed: {e}")
            titles_str = "\n".join(f"- {t}" for t in titles)
            return f"已收集 {len(titles)} 篇论文：\n{titles_str}\n\n（综述生成失败：{e}）"

    # ─────────────────────────────────────────────────────────
    # S2: 单篇精读（Deep-Dive）
    # ─────────────────────────────────────────────────────────

    def run_deep_dive(self, source: str, is_local: bool = False) -> dict:
        """
        对单篇论文做深度精读，生成结构化报告。

        与 run() 的根本区别：
        - run()：Agent 循环，多轮工具调用，适合广度调研
        - run_deep_dive()：顺序流水线，无工具调用循环，适合单篇深度分析

        流水线：
          URL  → 下载 PDF bytes → PyMuPDF 提取文本 → LLM DEEP_DIVE_PROMPT → 报告
          本地 → 读取 PDF bytes → PyMuPDF 提取文本 → LLM DEEP_DIVE_PROMPT → 报告

          两条路径在 PyMuPDF 之后完全一致（无 HTML fallback），保证提取质量的一致性。

        Args:
            source:   URL（arXiv、直链 PDF 等）或本地文件路径
            is_local: True 表示 source 是本地路径，False 表示是 URL

        Returns:
            dict 含 output（报告正文）、title（推断的标题）、source
        """
        logger.info(f"Deep-dive: {'local' if is_local else 'url'} = {source}")
        run_id = uuid.uuid4().hex
        started_at = time.perf_counter()
        session = self._start_recording("deep_dive", source, run_id)

        # ── Step 1: 获取 PDF bytes ────────────────────────────
        if is_local:
            pdf_bytes, err = _read_local_pdf_bytes(source)
        else:
            pdf_bytes, err = _download_pdf_bytes(source)

        if err:
            trace = self._finish_deep_dive_session(
                session, started_at, "input_error", 0, output_chars=len(err)
            )
            return {"output": err, "title": "", "source": source, "trace": trace}
        if session is not None:
            session.record_event(
                "pdf_acquired",
                {"source_type": "local" if is_local else "url", "bytes": len(pdf_bytes)},
            )

        # ── Step 2: PyMuPDF 提取文本 ─────────────────────────
        from tools.read_paper import _extract_from_pdf
        paper_text = _extract_from_pdf(pdf_bytes)

        if not paper_text or len(paper_text.strip()) < 200:
            output = (
                "[错误] 从 PDF 提取的文本内容过少，可能是扫描版或加密 PDF。\n"
                "建议：确认 PDF 文件中含有可复制的文字。"
            )
            trace = self._finish_deep_dive_session(
                session, started_at, "extraction_error", 0, output_chars=len(output)
            )
            return {
                "output": output,
                "title": "",
                "source": source,
                "trace": trace,
            }
        if session is not None:
            session.record_event(
                "pdf_extracted", {"text_chars": len(paper_text), "word_count": len(paper_text.split())}
            )

        # 截断超长文本（与 read_paper 使用相同的词数上限）
        words = paper_text.split()
        if len(words) > config.MAX_PAPER_WORDS:
            paper_text = " ".join(words[:config.MAX_PAPER_WORDS])
            logger.info(
                f"Deep-dive: text truncated to {config.MAX_PAPER_WORDS} words"
            )

        # ── Step 3: LLM 深度分析 ──────────────────────────────
        prompt = DEEP_DIVE_PROMPT.format(paper_text=paper_text)

        print("\n[Deep-Dive] 正在生成精读报告，请稍候...")
        try:
            response = self.llm.invoke(prompt)
            report = response.content
        except Exception as e:
            logger.error(f"Deep-dive LLM failed: {e}")
            output = f"[错误] LLM 分析失败: {e}"
            trace = self._finish_deep_dive_session(
                session, started_at, "model_error", 0, output_chars=len(output)
            )
            return {
                "output": output,
                "title": "",
                "source": source,
                "trace": trace,
            }
        if session is not None:
            session.record_event("report_generated", {"output_chars": len(report)})

        # ── Step 4: 推断标题（从报告第一个 ## 标题行或 source 中提取）──
        title = _infer_title(report, source)

        # ── Step 5: 存入知识库（供后续 S3 对比 / S5 问答使用）────
        kb_saved = False
        try:
            if not self.knowledge_base.has_document(title):
                self.knowledge_base.add_document(title=title, content=report)
                self.knowledge_base.flush()
                kb_saved = True
                logger.info(f"Deep-dive: saved to KB: {title}")
        except Exception as e:
            logger.warning(f"Deep-dive: failed to save to KB: {e}")

        if session is not None:
            session.record_event("knowledge_base_write", {"saved": kb_saved})
        in_kb = kb_saved
        if not in_kb:
            try:
                in_kb = self.knowledge_base.has_document(title)
            except Exception:
                in_kb = False
        trace = self._finish_deep_dive_session(
            session,
            started_at,
            "completed",
            1 if in_kb else 0,
            output_chars=len(report),
        )
        return {"output": report, "title": title, "source": source, "trace": trace}

    def chat(self, question: str) -> str:
        """
        调研完成后的追问接口。
        基于知识库中已积累的知识回答后续问题。
        """
        follow_up = (
            f"用户在调研完成后追问：{question}\n"
            f"请基于知识库中已有的论文信息回答。如果知识库中没有相关信息，请明确告知。"
        )
        all_messages = []
        for chunk in self.graph.stream(
            {"messages": [HumanMessage(follow_up)]},
            config=self._invoke_config,
            stream_mode="updates",
        ):
            for _, node_output in chunk.items():
                all_messages.extend(node_output.get("messages", []))

        return self._extract_final_output(all_messages)

    def reset(self):
        """重置 Agent 状态，清空知识库。"""
        self.knowledge_base.clear()
        logger.info("Agent reset: knowledge base cleared.")

    # ─────────────────────────────────────────────────────────
    # 内部辅助方法
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _print_step(node_name: str, messages: list):
        """打印推理步骤，替代旧版 AgentExecutor 的 verbose 输出。"""
        for msg in messages:
            if isinstance(msg, AIMessage):
                # 打印工具调用
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_name = tc.get("name", "unknown")
                        args = tc.get("args", {})
                        # 截断过长的参数显示
                        args_str = str(args)[:120] + "..." if len(str(args)) > 120 else str(args)
                        print(f"\n[{node_name}] Tool Call: {tool_name}({args_str})")
                elif msg.content:
                    print(f"\n[{node_name}] Thought: {str(msg.content)[:200]}")
            elif isinstance(msg, ToolMessage):
                content_preview = str(msg.content)[:200] + "..." if len(str(msg.content)) > 200 else str(msg.content)
                print(f"[Tool Result] {content_preview}")

    @staticmethod
    def _extract_final_output(messages: list) -> str:
        """从消息列表中提取最终 AI 输出（最后一条非工具调用的 AI 消息）。"""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                # 跳过只含工具调用、content 为空的 AI 消息
                if not (hasattr(msg, "tool_calls") and msg.tool_calls and not msg.content):
                    return msg.content if isinstance(msg.content, str) else str(msg.content)
        return "（Agent 未产生文本输出）"

    @staticmethod
    def _extract_tool_steps(messages: list) -> list:
        """提取所有工具调用记录，格式化为 (tool_name, input, output) 列表。"""
        steps = []
        pending_calls = {}  # tool_call_id → (name, args)

        for msg in messages:
            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    pending_calls[tc.get("id", "")] = (
                        tc.get("name", ""), tc.get("args", {})
                    )
            elif isinstance(msg, ToolMessage):
                call_id = getattr(msg, "tool_call_id", "")
                if call_id in pending_calls:
                    name, args = pending_calls.pop(call_id)
                    steps.append((name, args, msg.content))

        return steps

    def _start_recording(self, run_type: str, input_text: str, run_id: str):
        # 部分纯离线单测通过 __new__ 构造最小 Agent；此时 recorder 属性不存在。
        recorder = getattr(self, "recorder", None)
        if recorder is None:
            return None
        return recorder.start_run(
            run_type=run_type,
            input_text=input_text,
            run_id=run_id,
            versions={
                "prompt": config.PROMPT_VERSION,
                "tool_schema": config.TOOL_SCHEMA_VERSION,
                "model": config.LLM_MODEL,
                "embedding_model": config.EMBEDDING_MODEL,
            },
        )

    @staticmethod
    def _summarize_messages(node_name: str, messages: list, step_count: int) -> dict:
        tool_names = []
        tool_results = 0
        tool_errors = 0
        ai_messages = 0
        for msg in messages:
            if isinstance(msg, AIMessage):
                ai_messages += 1
                for call in getattr(msg, "tool_calls", []) or []:
                    tool_names.append(call.get("name", "unknown"))
            elif isinstance(msg, ToolMessage):
                tool_results += 1
                if str(msg.content).startswith(("[错误]", "[失败]")):
                    tool_errors += 1
        return {
            "step": step_count,
            "node": node_name,
            "ai_messages": ai_messages,
            "tool_names": tool_names,
            "tool_results": tool_results,
            "tool_errors": tool_errors,
        }

    @staticmethod
    def _finish_deep_dive_session(
        session, started_at: float, termination_reason: str, papers_count: int,
        output_chars: int,
    ) -> dict:
        trace = {
            "run_id": session.run_id if session is not None else "",
            "termination_reason": termination_reason,
            "duration_ms": round((time.perf_counter() - started_at) * 1000),
            "graph_steps": 0,
            "tool_calls": 0,
            "tool_call_counts": {},
            "papers_count": papers_count,
            "prompt_version": config.PROMPT_VERSION,
            "tool_schema_version": config.TOOL_SCHEMA_VERSION,
        }
        if session is not None:
            status = "completed" if termination_reason == "completed" else "failed"
            session.finish({**trace, "output_chars": output_chars}, status=status)
        return trace

    @staticmethod
    def _log_langsmith_status():
        if config.LANGSMITH_TRACING:
            logger.info(
                f"LangSmith tracing ON, project: {config.LANGSMITH_PROJECT}"
            )
        else:
            logger.info("LangSmith tracing OFF.")


# ─────────────────────────────────────────────────────────────
# 模块级辅助函数（供 run_deep_dive 使用，不依赖 Agent 实例）
# ─────────────────────────────────────────────────────────────

def _download_pdf_bytes(url: str) -> tuple[bytes | None, str | None]:
    """
    从 URL 下载 PDF 并返回原始 bytes。

    与 read_paper._fetch_paper_text() 的区别：
    - 此函数只接受 PDF（不做 HTML fallback），确保 deep-dive 路径的一致性
    - 返回 (bytes, None) 表示成功，(None, error_str) 表示失败

    URL 预处理：
    - arXiv abstract 页（/abs/xxx）→ /pdf/xxx.pdf
    - HTML 域名（arxiv.org/html/xxx）→ /pdf/xxx.pdf（尝试）
    """
    import requests

    # arXiv abs → pdf
    url = re.sub(r"arxiv\.org/abs/(.+?)(\?.*)?$", r"arxiv.org/pdf/\1.pdf", url)
    # arXiv html → pdf
    url = re.sub(r"arxiv\.org/html/(.+?)(\?.*)?$", r"arxiv.org/pdf/\1.pdf", url)
    # 补全协议
    if not url.startswith("http"):
        url = "https://" + url

    logger.info(f"Deep-dive download: {url}")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ResearchCopilot/1.0)"}

    try:
        resp = requests.get(url, timeout=30, allow_redirects=True, headers=headers)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        return None, f"[错误] 下载超时: {url}。建议：检查网络或稍后重试。"
    except requests.exceptions.HTTPError as e:
        return None, f"[错误] HTTP {resp.status_code}: {url}。建议：确认链接有效，或改用本地 PDF（--file）。"
    except Exception as e:
        return None, f"[错误] 下载失败: {e}"

    content_type = resp.headers.get("content-type", "").lower()
    if "pdf" not in content_type and not url.endswith(".pdf"):
        # 非 PDF 内容（如返回了 HTML 登录页）
        return None, (
            f"[错误] 下载的内容不是 PDF（Content-Type: {content_type}）。\n"
            f"可能原因：论文需要登录/付费获取，或链接指向 HTML 页面。\n"
            f"建议：手动下载 PDF 后使用 --file 参数传入。"
        )

    size_mb = len(resp.content) / (1024 * 1024)
    if size_mb > config.MAX_PDF_SIZE_MB:
        return None, (
            f"[错误] 远程 PDF 过大（{size_mb:.1f} MB），"
            f"超过 {config.MAX_PDF_SIZE_MB} MB 上限。"
        )

    return resp.content, None


def _read_local_pdf_bytes(file_path: str) -> tuple[bytes | None, str | None]:
    """
    读取本地 PDF 文件返回原始 bytes。

    Args:
        file_path: 绝对或相对路径

    Returns:
        (bytes, None) 成功，(None, error_str) 失败
    """
    if not os.path.isabs(file_path):
        file_path = os.path.abspath(file_path)

    if not os.path.exists(file_path):
        return None, (
            f"[错误] 文件不存在: {file_path}\n"
            f"建议：检查路径是否正确。"
        )

    ext = os.path.splitext(file_path)[1].lower()
    if ext != ".pdf":
        return None, f"[错误] 不支持的格式: {ext}，仅支持 .pdf。"

    size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if size_mb > config.MAX_PDF_SIZE_MB:
        return None, (
            f"[错误] 文件过大（{size_mb:.1f} MB），"
            f"超过 {config.MAX_PDF_SIZE_MB} MB 上限。"
        )

    try:
        with open(file_path, "rb") as f:
            return f.read(), None
    except PermissionError:
        return None, f"[错误] 无权限读取: {file_path}"
    except Exception as e:
        return None, f"[错误] 读取失败: {e}"


def _infer_title(report: str, source: str) -> str:
    """
    从报告内容或来源路径中推断论文标题，用于知识库存储的 key。

    策略（按优先级）：
    1. 报告中的一级标题（必须是“精读报告：论文标题”或其他非通用标题）
    2. arXiv ID（如 2210.03629）
    3. 文件名去掉扩展名
    4. source 字符串截断
    """
    generic_sections = {
        "研究背景与问题场景", "问题建模", "核心贡献", "方法详解",
        "实验设计与结论", "局限性",
    }

    # 1. 只接受一级标题，避免把“## 研究背景与问题场景”误当论文标题。
    for line in report.splitlines():
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            # 去掉 "论文精读：" 前缀等中文说明
            title = re.sub(r"^(论文精读|精读报告|Deep.?Dive)[：:\s]*", "", title).strip()
            if title and title not in generic_sections and len(title) > 5:
                return title[:120]

    # 2. arXiv ID
    m = re.search(r"(\d{4}\.\d{4,5})", source)
    if m:
        return f"arXiv:{m.group(1)}"

    # 3. 文件名
    basename = os.path.basename(source)
    if basename:
        return os.path.splitext(basename)[0][:120]

    return source[:80]
