"""One bounded embedding probe, including sanitized provider error classification."""
from pathlib import Path
import sys
import json
from datetime import datetime, timezone
from urllib.parse import urlparse
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config
from openai import OpenAI

record = {"time": datetime.now(timezone.utc).isoformat(), "host": urlparse(config.EMBEDDING_API_BASE).hostname,
          "model": config.EMBEDDING_MODEL, "mode": "live", "attempts": 1}
try:
    client = OpenAI(api_key=config.EMBEDDING_API_KEY, base_url=config.EMBEDDING_API_BASE, timeout=30, max_retries=0)
    result = client.embeddings.create(model=config.EMBEDDING_MODEL, input=["Research agent evidence retrieval."])
    record.update(passed=True, dimension=len(result.data[0].embedding), usage=result.usage.model_dump())
except Exception as exc:
    body = getattr(exc, "body", None)
    error = body.get("error", body) if isinstance(body, dict) else {}
    message = str(error.get("message", "")).lower()
    category = "quota_or_balance" if any(t in message for t in ("balance", "insufficient", "余额", "充值", "quota")) else "rate_limit_or_other"
    code = str(error.get("code", "unknown"))
    record.update(passed=False, error_type=type(exc).__name__, http_status=getattr(exc, "status_code", None),
                  provider_code=code if len(code) < 40 and code.replace("_", "").isalnum() else "redacted",
                  classification=category)
path = ROOT / "artifacts/product/M0/embedding_retry.json"
path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record))
raise SystemExit(0 if record["passed"] else 1)
