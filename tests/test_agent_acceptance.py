"""12-case 单 Agent 核心验收套件的回归入口。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_agent_v2_core_acceptance_12_cases_pass():
    from evaluation.agent_v2_acceptance import run_acceptance_suite

    report = run_acceptance_suite()
    assert report["case_count"] == 12
    assert report["passed_count"] == 12
    assert report["all_passed"] is True
    categories = {item["category"] for item in report["results"]}
    assert {"A1", "A2", "A3", "guard", "safety", "HITL"} <= categories
    assert all(item["passed"] for item in report["results"])


if __name__ == "__main__":
    tests = [test_agent_v2_core_acceptance_12_cases_pass]
    failures = []
    for test in tests:
        try:
            test()
            print(f"  [PASS] {test.__name__}")
        except Exception as exc:
            failures.append(test.__name__)
            print(f"  [FAIL] {test.__name__}: {type(exc).__name__}: {exc}")
    if failures:
        print(f"失败: {failures}")
        raise SystemExit(1)
