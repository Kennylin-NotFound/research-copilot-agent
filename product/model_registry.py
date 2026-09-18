"""Small explicit model and official-price registry used by product runs."""
from datetime import datetime, timezone
from decimal import Decimal

import config


DEEPSEEK_PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"
DEEPSEEK_PRICING_CHECKED_AT = "2026-09-18"
DEEPSEEK_ALIASES = {
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-v4-flash-vision-exp": "deepseek-flash",
}
# Official global API rates in USD per one million tokens.
DEEPSEEK_USD_PER_MILLION = {
    "deepseek-flash": {
        "off_peak": {"input_cache_hit": "0.003", "input_cache_miss": "0.15", "output": "0.60"},
        "peak": {"input_cache_hit": "0.006", "input_cache_miss": "0.30", "output": "1.20"},
    },
    "deepseek-v4-pro": {
        "off_peak": {"input_cache_hit": "0.022", "input_cache_miss": "0.66", "output": "1.98"},
        "peak": {"input_cache_hit": "0.044", "input_cache_miss": "1.32", "output": "3.96"},
    },
}


def available_models():
    values = [
        {"id": "default", "name": config.LLM_MODEL, "purpose": "质量优先"},
        {"id": "fast", "name": config.EXTRACTION_MODEL, "purpose": "速度与成本优先"},
    ]
    seen = set()
    return [item for item in values if not (item["name"] in seen or seen.add(item["name"]))]


def resolve_model(identity: str):
    choices = {item["id"]: item["name"] for item in available_models()}
    if identity not in choices:
        raise ValueError("unknown_model_profile")
    return choices[identity]


def pricing_period(at: datetime | None = None) -> str:
    at = at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    utc = at.astimezone(timezone.utc)
    peak = utc.weekday() < 5 and (1 <= utc.hour < 4 or 6 <= utc.hour < 10)
    return "peak" if peak else "off_peak"


def estimate_cost(model: str, usage: dict | None, at: datetime | None = None) -> dict:
    base = {"currency": "USD", "scope": "LLM token usage only"}
    if model.startswith("mock-"):
        return base | {"status": "not_billable", "display": "开发替身未产生模型费用"}
    canonical = DEEPSEEK_ALIASES.get(model, model)
    if canonical not in DEEPSEEK_USD_PER_MILLION:
        return base | {"status": "unknown_model", "display": "当前模型没有价格配置"}
    base |= {"source_url": DEEPSEEK_PRICING_SOURCE, "pricing_checked_at": DEEPSEEK_PRICING_CHECKED_AT}
    if not usage:
        return base | {"status": "no_usage", "model": canonical, "display": "尚无可计费的 token 用量"}

    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = details.get("cached_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    if hit is None and miss is None:
        hit, miss = 0, prompt
        assumption = "输入 token 按缓存未命中保守估算"
    else:
        hit = max(0, int(hit or 0))
        miss = max(0, int(miss if miss is not None else prompt - hit))
        assumption = "按接口返回的缓存命中信息估算"
    period = pricing_period(at)
    rates = DEEPSEEK_USD_PER_MILLION[canonical][period]
    amount = (
        Decimal(hit) * Decimal(rates["input_cache_hit"])
        + Decimal(miss) * Decimal(rates["input_cache_miss"])
        + Decimal(completion) * Decimal(rates["output"])
    ) / Decimal(1_000_000)
    return base | {
        "status": "estimated",
        "model": canonical,
        "pricing_period": period,
        "estimated_usd": float(amount),
        "input_cache_hit_tokens": hit,
        "input_cache_miss_tokens": miss,
        "output_tokens": completion,
        "assumption": assumption,
        "display": f"${amount:.6f} 估算（{'高峰' if period == 'peak' else '低谷'}；{assumption}）",
    }
