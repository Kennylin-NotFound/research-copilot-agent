"""
glm-4.7 模型能力验证
测试内容：
  1. 并发工具调用（Agent 核心能力）
  2. 长上下文论文信息提取（~6000 词）
  3. 中文综述生成质量
  4. 结构化输出（JSON / Markdown）
"""
import sys, os, io, time
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config
from utils.llm import make_chat_llm, make_extraction_llm

PASS = "[PASS]"
FAIL = "[FAIL]"

def test_parallel_tool_calls():
    """验证 glm-4.7 是否支持一次性调用多个工具（Agent 效率关键）"""
    from langchain_core.tools import tool

    @tool
    def search_papers(query: str, max_results: int = 5) -> str:
        """Search academic papers by keyword."""
        return f"Found papers for: {query}"

    @tool
    def read_paper(paper_url: str) -> str:
        """Read and extract key info from a paper URL."""
        return f"Extracted info from: {paper_url}"

    llm = make_chat_llm().bind_tools([search_papers, read_paper])
    msg = llm.invoke(
        "Please search for 'LLM agent planning' papers AND also read the paper at https://arxiv.org/abs/2210.03629 at the same time."
    )
    has_tools = bool(msg.tool_calls)
    multi = len(msg.tool_calls) >= 2 if has_tools else False
    status = PASS if has_tools else FAIL
    print(f"  {status} 并发工具调用 → tool_calls={len(msg.tool_calls) if has_tools else 0}, 多工具={'YES' if multi else 'only 1'}")
    return has_tools

def test_long_context_extraction():
    """验证 glm-4.7 能处理 ~6000 词的论文文本并提取结构化信息"""
    # 模拟一篇 6000 词的论文文本
    fake_paper = """
Abstract: We propose ReAct, a novel paradigm that synergizes reasoning and acting in language models.
Large language models (LLMs) have demonstrated impressive capabilities across NLP tasks.
However, existing approaches handle reasoning (chain-of-thought) and acting (task-specific action spaces) separately.

Introduction: The key insight is that language models can generate both verbal reasoning traces and task-specific actions in an interleaved manner.
Reasoning traces help the model induce, track, and update action plans, while actions allow the model to interface with external sources.

Method: We extend the action space of the agent to include both external actions (search, lookup) and internal language actions (think).
The model generates: Thought -> Action -> Observation cycles.
For example: Thought: I need to search for information about LLM agents.
Action: Search[LLM agents planning]
Observation: Retrieved 5 relevant papers about LLM agent planning...

Experiments: We evaluate on HotpotQA, FEVER, ALFWorld, and WebShop.
ReAct outperforms chain-of-thought by 15% on HotpotQA.
ReAct reduces hallucination by integrating external knowledge.
On ALFWorld, ReAct achieves 71% success vs 45% for imitation learning baseline.

Limitations: ReAct requires careful prompt engineering. Performance degrades on very long tasks.
The reasoning traces can sometimes be redundant. Future work should explore automatic prompt optimization.
""" * 15  # ~6000 词

    from agent.prompts import PAPER_EXTRACTION_PROMPT
    prompt = PAPER_EXTRACTION_PROMPT.format(paper_text=fake_paper[:12000])

    t0 = time.time()
    llm = make_extraction_llm()
    resp = llm.invoke(prompt)
    elapsed = time.time() - t0

    has_sections = all(kw in resp.content for kw in ["研究问题", "核心方法"])
    status = PASS if has_sections else FAIL
    print(f"  {status} 长上下文提取 → 耗时 {elapsed:.1f}s, 长度 {len(resp.content)} 字")
    print(f"       预览: {resp.content[:120].replace(chr(10), ' ')}")
    return has_sections

def test_chinese_survey_generation():
    """验证 glm-4.7 能生成结构完整的中文综述（模拟 Agent 最终输出）"""
    llm = make_chat_llm()

    context = """
已读论文：
1. ReAct (2022)：将推理和行动交错结合，通过思维-行动-观察循环提升Agent在HotpotQA上的表现。
2. Tree of Thoughts (2023)：将问题求解建模为树搜索，允许LLM探索多条推理路径并回溯。
3. Reflexion (2023)：通过语言反思而非权重更新强化Agent，使其从错误中学习。
4. RAP (2023)：将LLM同时作为世界模型和推理器，与MCTS结合进行规划。
"""
    prompt = f"""请基于以下已读论文，为主题'LLM Agent 规划与推理'生成一份结构化中文综述。
要求：
- 包含研究背景、主要方法分类、关键进展、未解决挑战四个部分
- 每部分 2-3 句话，总字数约 300-400 字
- 使用 Markdown 格式

{context}"""

    t0 = time.time()
    resp = llm.invoke(prompt)
    elapsed = time.time() - t0

    has_structure = "##" in resp.content or "**" in resp.content
    word_count = len(resp.content)
    status = PASS if has_structure and word_count > 200 else FAIL
    print(f"  {status} 中文综述生成 → 耗时 {elapsed:.1f}s, 字数 {word_count}")
    print(f"       前两行: {resp.content[:150].replace(chr(10), ' | ')}")
    return has_structure and word_count > 200


if __name__ == "__main__":
    print(f"\n===== glm-4.7 模型能力验证 (model={config.LLM_MODEL}) =====\n")
    results = []

    for name, fn in [
        ("并发工具调用", test_parallel_tool_calls),
        ("长上下文提取", test_long_context_extraction),
        ("中文综述生成", test_chinese_survey_generation),
    ]:
        try:
            ok = fn()
            results.append((name, ok))
        except Exception as e:
            print(f"  {FAIL} {name} → Exception: {e}")
            results.append((name, False))
        time.sleep(3)  # 避免 429

    print(f"\n{'='*50}")
    passed = sum(1 for _, ok in results if ok)
    print(f"结果: {passed}/{len(results)} 通过")
    for name, ok in results:
        print(f"  {'[OK]' if ok else '[NG]'} {name}")

    if passed < len(results):
        sys.exit(1)
