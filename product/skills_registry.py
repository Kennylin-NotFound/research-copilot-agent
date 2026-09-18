"""Only repository-owned Skills are loadable; uploaded SKILL.md remains document data."""
import hashlib
import json
from pathlib import Path
from product.contracts import SkillRuntime

AVAILABLE = ('paper-search','evidence-qa','paper-review','evidence-survey')
DISPLAY_NAMES = {
    None: '研究对话',
    'paper-search': '联网论文检索',
    'evidence-qa': '资料证据问答',
    'paper-review': '单篇论文评议',
    'evidence-survey': '多篇证据综述',
}


def display_name(identity):
    return DISPLAY_NAMES.get(identity, identity or DISPLAY_NAMES[None])


def load_skill(identity):
    if identity not in AVAILABLE:
        raise ValueError('skill_not_available')
    folder=Path(__file__).parent/'skills'/identity
    body=(folder/'SKILL.md').read_text(encoding='utf-8')
    raw=json.loads((folder/'runtime.json').read_text(encoding='utf-8'))
    runtime=SkillRuntime.model_validate({**raw,'body_sha256':hashlib.sha256(body.encode()).hexdigest()})
    return {**runtime.model_dump(mode='json'),'body':body}
