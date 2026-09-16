"""
Live API 连通性测试 —— 会实际调用外部 API，验证 Key 有效

覆盖：
  1. Tavily 检索一篇论文
  2. 旧 KB 使用的可选 Embedding provider 对一段文本向量化
  3. 当前 Extraction LLM 做一次简单推理
  4. 当前 Main LLM 做一次简单推理
  5. FAISS 本地写入和检索
"""
import sys, os, io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config


class SkipLiveTest(Exception):
    """外部可选依赖明确不可用时记录 SKIP，不吞掉其他异常。"""


def _is_optional_quota_error(exc: Exception) -> bool:
    message = str(exc)
    return "429" in message and ("1113" in message or "余额不足" in message)


# ─────────────────────────────────────────────
# 1. Tavily
# ─────────────────────────────────────────────
def test_tavily_search():
    from tavily import TavilyClient
    client = TavilyClient(api_key=config.TAVILY_API_KEY)
    resp = client.search(
        query="retrieval augmented generation paper",
        max_results=2,
        search_depth="basic",
    )
    results = resp.get("results", [])
    assert len(results) >= 1, f"Tavily 返回结果为空: {resp}"
    print(f"  Tavily OK → 返回 {len(results)} 条结果，第一条: {results[0].get('title','N/A')[:60]}")


# ─────────────────────────────────────────────
# 2. ZhipuAI Embedding
# ─────────────────────────────────────────────
def test_embedding_provider():
    from utils.llm import make_embeddings
    embeddings = make_embeddings()
    try:
        vec = embeddings.embed_query("test embedding")
    except Exception as exc:
        if _is_optional_quota_error(exc):
            raise SkipLiveTest("旧 KB embedding provider 额度不可用") from exc
        raise
    assert isinstance(vec, list) and len(vec) > 0, "Embedding 返回为空"
    print(f"  Embedding OK → 模型={config.EMBEDDING_MODEL}，维度={len(vec)}")


# ─────────────────────────────────────────────
# 3. ZhipuAI 提取模型（EXTRACTION_MODEL）
# ─────────────────────────────────────────────
def test_extraction_llm():
    from utils.llm import make_extraction_llm
    llm = make_extraction_llm()
    response = llm.invoke("请用一句话介绍 RAG 技术。")
    content = response.content
    assert content and len(content) > 5, f"LLM 返回内容异常: {content!r}"
    print(f"  Extraction LLM OK → 模型={config.EXTRACTION_MODEL}，回复: {content[:80]}")


# ─────────────────────────────────────────────
# 4. ZhipuAI 主模型（LLM_MODEL）
# ─────────────────────────────────────────────
def test_main_llm():
    from utils.llm import make_chat_llm
    llm = make_chat_llm()
    response = llm.invoke("你好，请回复 OK。")
    content = response.content
    assert content and len(content) > 0, f"LLM 返回内容异常: {content!r}"
    print(f"  Main LLM OK → 模型={config.LLM_MODEL}，回复: {content[:80]}")


# ─────────────────────────────────────────────
# 5. FAISS 本地读写（替代 Chroma，解决 Windows DLL 问题）
# ─────────────────────────────────────────────
def test_faiss_vectorstore():
    """
    验证 FAISS 向量存储可正常读写。
    使用 ZhipuAI embedding 做真实向量化。
    """
    from langchain_community.vectorstores import FAISS
    from utils.llm import make_embeddings

    emb = make_embeddings()
    try:
        vs = FAISS.from_texts(
            ["RAG retrieval augmented generation"],
            embedding=emb,
            metadatas=[{"section_type": "method"}],
        )
    except Exception as exc:
        if _is_optional_quota_error(exc):
            raise SkipLiveTest("旧 KB embedding provider 额度不可用，跳过在线 FAISS 写入") from exc
        raise
    docs = vs.similarity_search("retrieval", k=1)
    assert len(docs) >= 1, "FAISS 检索返回为空"
    assert "RAG" in docs[0].page_content, f"检索内容异常: {docs[0].page_content}"
    print(f"  FAISS OK → 存储 1 条，检索到: {docs[0].page_content[:60]}")


def test_search_papers_ranked():
    """验证 search_papers_ranked 能正常调用 Semantic Scholar 并返回排序结果。
    Semantic Scholar 免费额度有限，限流时跳过（不视为失败）。"""
    from tools.search_papers_ranked import create_search_papers_ranked_tool

    tool_fn = create_search_papers_ranked_tool()
    result = tool_fn.invoke({"query": "retrieval augmented generation", "max_results": 5})

    # 限流是预期的正常情况（免费额度），跳过断言
    if "[检索限流]" in result or "[检索超时]" in result:
        print(f"  search_papers_ranked SKIP（Semantic Scholar 限流/超时）→ {result[:80]}")
        return

    assert "[检索失败]" not in result, f"检索失败: {result[:200]}"
    assert "综合分" in result, f"输出中应包含综合分字段: {result[:200]}"
    has_venue_tag = any(tag in result for tag in ["顶会", "重要会议", "arXiv", "来源"])
    assert has_venue_tag, f"输出中应包含来源/场馆信息: {result[:200]}"
    print(f"  search_papers_ranked OK → 前100字: {result[:100].replace(chr(10), ' ')}")


def test_read_local_pdf_error_handling():
    """验证 read_local_pdf 对不存在文件的错误处理（离线可测，无需真实 PDF）"""
    from tools.read_local_pdf import create_read_local_pdf_tool

    tool_fn = create_read_local_pdf_tool()
    result = tool_fn.invoke({"file_path": "/nonexistent/path/paper.pdf"})

    assert "[错误]" in result, f"不存在的文件应返回错误信息: {result}"
    assert "不存在" in result, f"错误信息应说明文件不存在: {result}"
    print(f"  read_local_pdf 错误处理 OK → {result[:80]}")


if __name__ == "__main__":
    tests = [
        ("Tavily 检索",                   test_tavily_search),
        ("Embedding provider",           test_embedding_provider),
        ("Extraction LLM",               test_extraction_llm),
        ("Main LLM",                     test_main_llm),
        ("FAISS 向量存储",               test_faiss_vectorstore),
        ("search_papers_ranked",          test_search_papers_ranked),
        ("read_local_pdf 错误处理",       test_read_local_pdf_error_handling),
    ]
    failed = []
    for name, t in tests:
        try:
            t()
            print(f"  [PASS] {name}")
        except SkipLiveTest as e:
            print(f"  [SKIP] {name}: {e}")
        except AssertionError as e:
            print(f"  [FAIL] {name}: {e}")
            failed.append(name)
        except Exception as e:
            print(f"  [ERROR] {name}: {type(e).__name__}: {e}")
            failed.append(name)
    print()
    if failed:
        print(f"以下测试失败: {failed}")
        sys.exit(1)
    else:
        print("所有 API 连通性测试通过 [OK]")
