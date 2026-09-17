"""Explicit natural-language project revisions with structured, auditable output."""
import json
import re
from typing import Literal

import config
from openai import OpenAI
from pydantic import Field, model_validator
from psycopg.types.json import Jsonb

from product.contracts import Contract, ProjectState, StatePatch, apply_state_patch
from product.db import connect
from product.project_context import save_revision, snapshot
from product.rag import span

PROMPT_VERSION='request-intent-2026-09-17.1'


class RequestIntent(Contract):
    kind: Literal['execute','revise']
    objective: str | None=Field(default=None,max_length=4000)
    dimensions: list[str] | None=Field(default=None,max_length=12)
    allow_network: bool | None=None
    reason_summary: str=Field(min_length=1,max_length=300)

    @model_validator(mode='after')
    def revision_has_change(self):
        changes=(self.objective,self.dimensions,self.allow_network)
        if self.kind=='revise' and all(value is None for value in changes):
            raise ValueError('revision_without_change')
        if self.kind=='execute' and any(value is not None for value in changes):
            raise ValueError('execute_with_change')
        return self


def may_request_revision(text):
    patterns=(r'修改.{0,12}(目标|维度|范围|要求)',r'(目标|维度|范围|要求).{0,12}(改为|调整为|更新为|新增|删除|去掉)',
              r'\b(change|update|revise|set|add|remove)\b.{0,40}\b(objective|dimension|scope|requirement)s?\b')
    return any(re.search(pattern,text,re.I) for pattern in patterns)


def classify_message(settings,run,text,parent):
    if not may_request_revision(text):
        return RequestIntent(kind='execute',reason_summary='No explicit persistent project revision requested.'),None
    if settings.mode=='mock':
        return RequestIntent(kind='execute',reason_summary='Mock mode requires an injected revision decision.'),None
    current=(run.get('project_snapshot') or {}).get('state',{})
    instructions=('Classify whether the latest user message explicitly asks to persistently revise the project objective, research dimensions, or network permission. Normal task instructions and requested answer structure are execute, not a persistent revision. For revise, return only explicitly requested fields; dimensions must be the complete replacement list after applying additions/removals to current_state. Never infer memory preferences or file IDs. reason_summary is a short audit explanation, not hidden reasoning.')
    payload={'latest_message':text,'current_state':current}
    client=OpenAI(api_key=config.LLM_API_KEY,base_url=config.LLM_API_BASE,timeout=30,max_retries=0)
    feedback=None;usage={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}
    for attempt in range(2):
        with span(settings,run,parent,'model','classify_request_intent',{'prompt_version':PROMPT_VERSION,'attempt':attempt+1}) as details:
            messages=[{'role':'system','content':instructions},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]
            if feedback:messages.append({'role':'user','content':feedback})
            response=client.chat.completions.create(model=run['model'],messages=messages,
                tools=[{'type':'function','function':{'name':'classify_request','description':'Classify and structure an explicit project revision','parameters':RequestIntent.model_json_schema()}}],
                tool_choice={'type':'function','function':{'name':'classify_request'}},max_tokens=600,
                extra_body={'thinking':{'type':config.DEEPSEEK_THINKING}} if config.LLM_PROVIDER=='deepseek' else None)
            calls=response.choices[0].message.tool_calls or []
            details['response_shape']={'finish_reason':response.choices[0].finish_reason,'tool_call_count':len(calls),'tool_names':[c.function.name for c in calls]}
            if response.usage:
                raw=response.usage.model_dump();details['usage']=raw
                for key in usage:usage[key]+=raw.get(key,0)
            try:
                if len(calls)!=1 or calls[0].function.name!='classify_request':
                    raise ValueError('invalid_intent_tool_call')
                intent=RequestIntent.model_validate_json(calls[0].function.arguments)
                details['intent']=intent.model_dump(mode='json')
                return intent,usage
            except ValueError:
                if attempt:raise ValueError('invalid_intent_tool_call')
                feedback='Return exactly one classify_request call. Use revise only for an explicit persistent project-state change and include at least one changed field.'
    raise ValueError('invalid_intent_tool_call')


def apply_revision(settings,run,intent,parent):
    if intent.kind!='revise':
        return run
    changes={key:getattr(intent,key) for key in ('objective','dimensions','allow_network') if getattr(intent,key) is not None}
    with span(settings,run,parent,'state','apply_state_patch',{'source':'natural_language','fields':sorted(changes)}) as details:
        with connect(settings) as db:
            project=db.execute('SELECT * FROM projects WHERE id=%s AND owner_id=%s FOR UPDATE',(run['project_id'],run['owner_id'])).fetchone()
            if not project or project['revision']!=run['project_revision']:
                raise ValueError('project_changed_before_state_patch')
            patch=StatePatch(expected_revision=project['revision'],**changes)
            updated=apply_state_patch(ProjectState.model_validate(project['state']),project['revision'],patch)
            previous=ProjectState.model_validate(project['state'])
            changed=[key for key in changes if getattr(previous,key)!=getattr(updated,key)]
            if changed:
                save_revision(db,project,{'kind':'baseline'})
                project=db.execute('UPDATE projects SET state=%s,revision=revision+1,updated_at=now() WHERE id=%s RETURNING *',(Jsonb(updated.model_dump(mode='json')),project['id'])).fetchone()
                current=save_revision(db,project,{'kind':'state_patch','fields':changed,'source':'natural_language','source_message_id':str(run['user_message_id'])})
            else:
                current=snapshot(db,project)
            run=db.execute('UPDATE runs SET project_revision=%s,project_snapshot=%s WHERE id=%s RETURNING *',(project['revision'],Jsonb(current),run['id'])).fetchone()
            details.update(previous_revision=patch.expected_revision,new_revision=project['revision'],changed_fields=changed)
            return run
