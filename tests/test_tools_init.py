"""
工具初始化测试 —— 验证所有工具能正常创建，不触发实际 API 调用
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_tool_creation():
    """所有工具应能成功创建，工厂函数不应在初始化时调用 API"""
    from tools.search_papers import create_search_papers_tool
    from tools.search_papers_ranked import create_search_papers_ranked_tool
    from tools.read_paper import create_read_paper_tool
    from tools.read_local_pdf import create_read_local_pdf_tool
    from tools.save_to_kb import create_save_to_kb_tool
    from tools.query_knowledge import create_query_knowledge_tool
    from tools.assess_coverage import create_assess_coverage_tool

    class MockKB:
        def has_document(self, title): return False
        def add_document(self, title, content): pass
        def flush(self): pass
        def query(self, query_text, top_k=None, section_filter=None): return []
        def get_all_titles(self): return []
        def get_document_count(self): return 0

    kb = MockKB()
    tools = [
        create_search_papers_tool(),
        create_search_papers_ranked_tool(),
        create_read_paper_tool(),
        create_read_local_pdf_tool(),
        create_save_to_kb_tool(kb),
        create_query_knowledge_tool(kb),
        create_assess_coverage_tool(kb),
    ]
    assert len(tools) == 7, f"应有 7 个工具，实际 {len(tools)}"

    names = [t.name for t in tools]
    expected = {
        "search_papers", "search_papers_ranked",
        "read_paper", "read_local_pdf",
        "save_to_kb", "query_knowledge", "assess_coverage",
    }
    assert set(names) == expected, f"工具名称不匹配: {set(names) ^ expected}"
    print(f"  已创建工具: {names}")


def test_get_all_tools():
    """get_all_tools() 应返回 7 个工具列表"""
    from tools import get_all_tools
    from unittest.mock import MagicMock

    kb = MagicMock()
    kb.has_document.return_value = False
    kb.get_document_count.return_value = 0

    tools = get_all_tools(kb)
    assert len(tools) == 7, f"应有 7 个工具，实际 {len(tools)}"
    print(f"  get_all_tools() 返回 {len(tools)} 个工具")


def test_venue_weight():
    """验证期刊/会议权重映射逻辑"""
    from tools.search_papers_ranked import _get_venue_weight, _composite_score

    assert _get_venue_weight("NeurIPS 2023") == 1.5, "NeurIPS 应为 Tier 1"
    assert _get_venue_weight("ICLR 2024") == 1.5, "ICLR 应为 Tier 1"
    assert _get_venue_weight("ACL Findings") == 1.5, "ACL 应为 Tier 1"
    assert _get_venue_weight("AAAI 2023") == 1.2, "AAAI 应为 Tier 2"
    assert _get_venue_weight("arXiv preprint") == 0.9, "arXiv 应降权"
    assert _get_venue_weight("") == 1.0, "未知 venue 应为中性权重"
    assert _get_venue_weight(None) == 1.0, "None venue 应为中性权重"

    # 综合分：顶会少引用 vs 未知场馆多引用
    score_top = _composite_score(100, "NeurIPS 2023")     # log(101) * 1.5 ≈ 6.9
    score_unknown = _composite_score(500, "unknown conf") # log(501) * 1.0 ≈ 6.2
    assert score_top > score_unknown, (
        f"100 引 NeurIPS ({score_top:.2f}) 应高于 500 引 unknown ({score_unknown:.2f})"
    )
    print(f"  Venue 权重验证通过 | NeurIPS-100引={score_top:.2f} > unknown-500引={score_unknown:.2f}")


if __name__ == "__main__":
    tests = [test_tool_creation, test_get_all_tools, test_venue_weight]
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
