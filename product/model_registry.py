"""Small explicit model registry used by runs and controlled comparisons."""
import config


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
