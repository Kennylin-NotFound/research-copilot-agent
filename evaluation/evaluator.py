"""
评估模块

职责：运行测试用例，评估 Agent 的调研质量。

三个评估维度：
1. 方向覆盖率：Agent 输出的综述覆盖了多少预期子方向
2. 关键论文命中率：知识库中是否包含预期的关键论文
3. 综述结构完整性：输出是否包含完整的综述结构

输出评估报告，用于指导 Prompt 和工具策略的迭代优化。
"""

import tempfile

from agent.core import ResearchCopilotAgent
from evaluation.test_cases import TEST_CASES
from rag.knowledge_base import KnowledgeBase
from evaluation.metrics import evaluate_snapshot
from evaluation.regression import evaluate_gate


class Evaluator:
    """Agent 调研质量评估器"""

    def __init__(self, agent=None):
        """创建隔离的评估器。

        默认把知识库放在临时目录。每个 case 仍会 reset，但只会清理评估数据，
        不再删除用户在 ``./data/faiss_index`` 中积累的真实知识库。

        Args:
            agent: 可选注入的 Agent，便于测试或自定义评估环境。
        """
        self._temp_dir = None
        if agent is not None:
            self.agent = agent
        else:
            self._temp_dir = tempfile.TemporaryDirectory(prefix="research-copilot-eval-")
            isolated_kb = KnowledgeBase(data_dir=self._temp_dir.name)
            self.agent = ResearchCopilotAgent(knowledge_base=isolated_kb)

    def close(self):
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def run_single_case(self, test_case: dict) -> dict:
        """
        运行单个测试用例并评估结果。

        流程：
        1. 重置 Agent（清空知识库，确保每个 case 独立）
        2. 运行 Agent 完成调研
        3. 评估三个维度的得分
        4. 返回评估结果

        Returns:
            dict: {
                "case_id": str,
                "direction_coverage": float,   # 方向覆盖率 (0-1)
                "paper_hit_rate": float,        # 关键论文命中率 (0-1)
                "structure_score": float,       # 结构完整性评分 (0-1)
                "overall_score": float,         # 综合评分
                "details": dict,                # 详细评估信息
            }
        """
        # Step 1: 重置
        self.agent.reset()

        # Step 2: 运行调研
        result = self.agent.run(test_case["topic"])
        output = result["output"]

        # Step 3: 使用与离线 replay 相同的分层指标和门禁。
        snapshot = {
            "output": output,
            "stored_titles": self.agent.knowledge_base.get_all_titles(),
            "intermediate_steps": result["intermediate_steps"],
            "trace": result.get("trace", {}),
        }
        metrics = evaluate_snapshot(test_case, snapshot)
        gate = evaluate_gate(metrics, thresholds=test_case.get("thresholds"))

        return {
            "case_id": test_case["id"],
            "direction_coverage": metrics["direction_coverage"],
            "paper_hit_rate": metrics["paper_hit_rate"],
            "structure_score": metrics["structure_score"],
            "tool_coverage": metrics["tool_coverage"],
            "tool_success_rate": metrics["tool_success_rate"],
            "task_success": metrics["task_success"],
            "efficiency_score": metrics["efficiency_score"],
            "overall_score": metrics["overall_score"],
            "gate": gate,
            "papers_count": result["papers_count"],
            "steps_count": len(result["intermediate_steps"]),
        }

    def _eval_direction_coverage(self, output: str, expected_directions: list) -> float:
        """
        评估方向覆盖率。

        方法：检查 Agent 输出的综述中是否提及了各预期子方向。
        简单实现：关键词匹配。
        进阶实现：可以用 LLM 做语义层面的覆盖度判断（判断同义表述）。
        """
        output_lower = output.lower()
        hits = sum(
            1 for d in expected_directions
            if d.lower() in output_lower
        )
        return hits / len(expected_directions) if expected_directions else 0

    def _eval_paper_hits(self, key_papers: list) -> float:
        """
        评估关键论文命中率。

        方法：检查知识库中存储的论文标题是否包含关键论文。
        使用模糊匹配（关键词包含），而非精确匹配。
        """
        stored_titles = self.agent.knowledge_base.get_all_titles()
        stored_titles_text = " ".join(stored_titles).lower()

        hits = sum(
            1 for p in key_papers
            if p.lower() in stored_titles_text
        )
        return hits / len(key_papers) if key_papers else 0

    def _eval_structure(self, output: str) -> float:
        """
        评估综述结构完整性。

        检查输出是否包含预期的结构要素：
        - 主题概述
        - 方向分类
        - 方法对比
        - 趋势展望
        - 参考文献

        每包含一个要素得 0.2 分，满分 1.0。
        """
        structure_markers = [
            ["概述", "背景", "overview"],
            ["方向", "分类", "category"],
            ["对比", "比较", "comparison"],
            ["趋势", "展望", "future"],
            ["参考", "引用", "reference"],
        ]
        output_lower = output.lower()
        score = 0
        for markers in structure_markers:
            if any(m in output_lower for m in markers):
                score += 0.2
        return score

    def run_all_cases(self) -> list[dict]:
        """运行所有测试用例并汇总结果。"""
        results = []
        for case in TEST_CASES:
            print(f"\n{'='*60}")
            print(f"Running: {case['id']} - {case['topic']}")
            print(f"{'='*60}")

            result = self.run_single_case(case)
            results.append(result)

            print(f"  方向覆盖: {result['direction_coverage']:.0%}")
            print(f"  论文命中: {result['paper_hit_rate']:.0%}")
            print(f"  结构完整: {result['structure_score']:.0%}")
            print(f"  综合评分: {result['overall_score']:.0%}")
            print(f"  回归门禁: {'PASS' if result['gate']['passed'] else 'REJECT'}")
            print(f"  涉及论文: {result['papers_count']} 篇")
            print(f"  推理步数: {result['steps_count']} 步")

        # 汇总统计
        avg_overall = sum(r["overall_score"] for r in results) / len(results)
        print(f"\n{'='*60}")
        print(f"总体评分: {avg_overall:.0%}")
        print(f"{'='*60}")

        return results


# 直接运行评估
if __name__ == "__main__":
    with Evaluator() as evaluator:
        evaluator.run_all_cases()
