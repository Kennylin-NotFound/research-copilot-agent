"""
统一测试入口
运行方式：python tests/run_tests.py [--live]
  --live  同时运行 API 连通性测试（会消耗少量 API 配额）
"""
import sys, os, subprocess, argparse
# Windows: 强制 stdout 使用 utf-8，避免中文乱码
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.join(os.path.dirname(__file__), "..")
PYTHON = sys.executable

OFFLINE_TESTS = [
    ("配置检查",     "tests/test_config.py"),
    ("Chunking 逻辑", "tests/test_chunking.py"),
    ("工具初始化",   "tests/test_tools_init.py"),
    ("可靠性与隔离", "tests/test_hardening.py"),
    ("AgentOps 回放与维护", "tests/test_agent_ops.py"),
    ("Agent v2 状态与编排", "tests/test_agent_state.py"),
    ("Agent v2 12-case 验收", "tests/test_agent_acceptance.py"),
]

LIVE_TESTS = [
    ("Live API 连通性", "tests/test_live_apis.py"),
]


def run(label, path):
    print(f"\n{'-'*50}")
    print(f">>  {label}")
    print(f"{'-'*50}")
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [PYTHON, path],
        cwd=ROOT,
        capture_output=False,
        env=child_env,
    )
    return result.returncode == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="同时运行 API 连通性测试")
    args = parser.parse_args()

    suites = OFFLINE_TESTS + (LIVE_TESTS if args.live else [])
    results = []

    for label, path in suites:
        ok = run(label, path)
        results.append((label, ok))

    print(f"\n{'='*50}")
    print("测试结果汇总")
    print(f"{'='*50}")
    all_pass = True
    for label, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  {status}  {label}")
        if not ok:
            all_pass = False

    print()
    if not args.live:
        print("  提示：运行 `python tests/run_tests.py --live` 同时验证 API 连通性")

    sys.exit(0 if all_pass else 1)
