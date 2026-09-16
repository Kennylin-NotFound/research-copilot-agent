"""
配置加载测试 —— 不调用任何 API，纯本地验证
"""
import sys, os, subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config


def test_required_keys_present():
    assert config.LLM_API_KEY, "LLM_API_KEY 未配置（ZHIPUAI_API_KEY 或 OPENAI_API_KEY）"
    assert config.LLM_API_BASE, "LLM_API_BASE 未配置"
    assert config.LLM_MODEL, "LLM_MODEL 未配置"
    assert config.EXTRACTION_MODEL, "EXTRACTION_MODEL 未配置"
    assert config.EMBEDDING_MODEL, "EMBEDDING_MODEL 未配置"
    assert config.EMBEDDING_API_KEY, "EMBEDDING_API_KEY 未配置"
    assert config.EMBEDDING_API_BASE, "EMBEDDING_API_BASE 未配置"
    assert config.LLM_PROVIDER in {"zhipu", "deepseek", "openai"}
    print(f"  LLM_PROVIDER     = {config.LLM_PROVIDER}")
    print(f"  LLM_MODEL        = {config.LLM_MODEL}")
    print(f"  EXTRACTION_MODEL = {config.EXTRACTION_MODEL}")
    print(f"  EMBEDDING_MODEL  = {config.EMBEDDING_MODEL}")
    print(f"  SEARCH_PROVIDER  = {config.SEARCH_PROVIDER}")
    print(f"  LangSmith        = {config.LANGSMITH_TRACING}")


def test_no_double_hyphen_in_models():
    """检测常见的模型名称拼写错误：双横线"""
    assert "--" not in config.LLM_MODEL, f"LLM_MODEL 含双横线: {config.LLM_MODEL!r}"
    assert "--" not in config.EXTRACTION_MODEL, f"EXTRACTION_MODEL 含双横线: {config.EXTRACTION_MODEL!r}"


def test_agent_v2_run_limits_are_positive():
    assert config.AGENT_V2_MAX_ITERATIONS > 0
    assert config.AGENT_V2_MAX_SEARCHES > 0
    assert config.AGENT_V2_MAX_READS > 0
    assert config.AGENT_V2_TIMEOUT_SECONDS >= 10
    assert config.AGENT_V2_MAX_NO_PROGRESS > 0
    assert config.AGENT_V2_CHECKPOINT_DB
    assert config.DEEPSEEK_THINKING in {"enabled", "disabled"}


def test_deepseek_provider_selection_in_fresh_process():
    """使用假 key 验证 provider 路由，不发起网络请求。"""
    env = os.environ.copy()
    env.update(
        {
            "LLM_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "test-only-placeholder",
            "DEEPSEEK_MODEL": "deepseek-v4-pro",
            "DEEPSEEK_THINKING": "disabled",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import config; print(config.LLM_PROVIDER, config.LLM_MODEL, config.LLM_API_BASE, config.DEEPSEEK_THINKING)",
        ],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert result.stdout.strip() == "deepseek deepseek-v4-pro https://api.deepseek.com disabled"


def test_tavily_key_present():
    if config.SEARCH_PROVIDER == "tavily":
        assert config.TAVILY_API_KEY, "SEARCH_PROVIDER=tavily 但 TAVILY_API_KEY 未配置"
        print("  TAVILY_API_KEY   = <configured>")


def test_langsmith_key_if_tracing_enabled():
    if config.LANGSMITH_TRACING:
        langsmith_key = os.getenv("LANGCHAIN_API_KEY", "")
        assert langsmith_key, "LANGCHAIN_TRACING_V2=true 但 LANGCHAIN_API_KEY 未配置"
        print("  LANGCHAIN_API_KEY = <configured>")


if __name__ == "__main__":
    tests = [
        test_required_keys_present,
        test_no_double_hyphen_in_models,
        test_agent_v2_run_limits_are_positive,
        test_deepseek_provider_selection_in_fresh_process,
        test_tavily_key_present,
        test_langsmith_key_if_tracing_enabled,
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
            print(f"  [ERROR] {t.__name__}: {e}")
            failed.append(t.__name__)
    print()
    if failed:
        print(f"失败: {failed}")
        sys.exit(1)
