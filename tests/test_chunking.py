"""
Chunking 逻辑测试 —— 纯本地，无 API 调用
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rag.chunking import chunk_paper_content


SAMPLE_STRUCTURED = """**研究问题**：如何利用检索增强生成（RAG）方法提升大语言模型在知识密集型任务上的性能，同时解决参数化知识的时效性问题。

**核心方法**：提出 RAG 框架，将预训练 seq2seq 模型与密集段落检索器（DPR）结合。检索阶段用 DPR 从 Wikipedia 检索相关段落，生成阶段将检索结果作为上下文输入 LLM。

**关键发现**：在 Open-domain QA 任务上超过了纯参数化模型，F1 提升约 12%。生成的答案更具体、更多样化、更符合事实。

**局限性**：检索质量直接影响生成质量；检索器与生成器联合训练计算开销大。

**重要引用**：Dense Passage Retrieval (Karpukhin et al. 2020)、REALM (Guu et al. 2020)。"""

SAMPLE_PLAIN = """This paper proposes a new method for document retrieval using neural networks.
The approach combines dense and sparse retrieval signals.
Experiments on standard benchmarks show significant improvement over baselines."""


def test_section_chunking():
    """结构化文本应按 section 切分，每个 chunk 有正确的 section_type"""
    chunks = chunk_paper_content(SAMPLE_STRUCTURED, "RAG Paper")
    assert len(chunks) >= 4, f"结构化文本应产生至少 4 个 chunk，实际: {len(chunks)}"
    section_types = {c["metadata"]["section_type"] for c in chunks}
    expected = {"problem", "method", "findings", "limitations"}
    assert expected.issubset(section_types), f"缺少 section: {expected - section_types}"
    for c in chunks:
        assert c["text"].strip(), "存在空 chunk"
        assert "paper_title" in c["metadata"]
        assert c["metadata"]["paper_title"] == "RAG Paper"
    print(f"  生成 {len(chunks)} 个 chunk，sections: {sorted(section_types)}")


def test_fallback_chunking():
    """无结构标记的文本应 fallback 到段落/长度切分"""
    chunks = chunk_paper_content(SAMPLE_PLAIN, "Plain Paper")
    assert len(chunks) >= 1, "应至少产生 1 个 chunk"
    assert all(c["text"].strip() for c in chunks), "存在空 chunk"
    print(f"  Fallback 产生 {len(chunks)} 个 chunk")


def test_long_section_split():
    """超长 section 应被二次切分"""
    long_method = "**核心方法**：" + "这是一个很长的方法描述。" * 100
    chunks = chunk_paper_content(long_method, "Long Paper")
    assert len(chunks) >= 2, "超长 section 应被切分为多个 chunk"
    print(f"  超长 section 切分为 {len(chunks)} 个 chunk")


def test_deduplication_metadata():
    """同一论文的 chunk 应都带有相同的 paper_title"""
    chunks = chunk_paper_content(SAMPLE_STRUCTURED, "My Paper")
    titles = {c["metadata"]["paper_title"] for c in chunks}
    assert titles == {"My Paper"}, f"paper_title 不一致: {titles}"


if __name__ == "__main__":
    tests = [
        test_section_chunking,
        test_fallback_chunking,
        test_long_section_split,
        test_deduplication_metadata,
    ]
    failed = []
    for t in tests:
        try:
            t()
            print(f"  [PASS] {t.__name__}")
        except AssertionError as e:
            print(f"  [FAIL] {t.__name__}: {e}")
            failed.append(t.__name__)
        except Exception as e:
            print(f"  [ERROR] {t.__name__}: {type(e).__name__}: {e}")
            failed.append(t.__name__)
    print()
    if failed:
        sys.exit(1)
