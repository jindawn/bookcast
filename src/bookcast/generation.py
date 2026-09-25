"""Bounded generation contracts and one explicit, vendor-neutral task policy.

No prompts, secrets, transport headers or arbitrary request fields belong here.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TaskType = Literal['extraction', 'chapter_synthesis', 'book_synthesis', 'dialogue', 'consistency', 'other']
PolicyName = Literal['bookcast-v1']


class GenerationConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    thinking: Literal['enabled', 'disabled'] | None = None
    reasoning_effort: Literal['low', 'high', 'max'] | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=65536)

    @model_validator(mode='after')
    def coherent_thinking(self):
        if self.thinking == 'disabled' and self.reasoning_effort is not None:
            raise ValueError('disabled thinking cannot specify reasoning effort')
        return self

    def request_fields(self) -> dict:
        fields = self.model_dump(exclude_none=True)
        if self.thinking is not None:
            fields['thinking'] = {'type': self.thinking}
        return fields


class GenerationAudit(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    policy: PolicyName | None = None
    task_type: TaskType = 'other'
    options: GenerationConfig


class ProviderUsage(BaseModel):
    """Only server-reported counts; omitted/invalid counts remain unknown."""
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    input_tokens: int | None = Field(default=None, ge=0, le=2**63-1)
    output_tokens: int | None = Field(default=None, ge=0, le=2**63-1)
    reasoning_tokens: int | None = Field(default=None, ge=0, le=2**63-1)
    cache_hit_tokens: int | None = Field(default=None, ge=0, le=2**63-1)

    @classmethod
    def from_response(cls, data):
        if not isinstance(data, dict):
            return None
        details = data.get('completion_tokens_details')
        details = details if isinstance(details, dict) else {}
        prompt_details = data.get('prompt_tokens_details')
        prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
        values = {'input_tokens': data.get('prompt_tokens'), 'output_tokens': data.get('completion_tokens'),
                  'reasoning_tokens': details.get('reasoning_tokens'),
                  'cache_hit_tokens': data.get('prompt_cache_hit_tokens', prompt_details.get('cached_tokens'))}
        return cls(**{k: v if type(v) is int and 0 <= v <= 2**63-1 else None for k, v in values.items()})


def task_type(task: str) -> TaskType:
    for prefix, kind in (('analysis:', 'extraction'), ('synthesis/chapters/', 'chapter_synthesis'),
                         ('synthesis/book/', 'book_synthesis'), ('script:', 'dialogue'),
                         ('consistency:', 'consistency')):
        if task.startswith(prefix):
            return kind
    return 'other'


# Explicit opt-in preset. Live quality evaluation is required before calling it optimal.
POLICY: dict[TaskType, GenerationConfig] = {
    # Keep extraction's historical ceiling so existing evidence checkpoints
    # remain valid when a job is resumed with the same provider configuration.
    'extraction': GenerationConfig(thinking='disabled', max_tokens=16384),
    'chapter_synthesis': GenerationConfig(thinking='disabled', max_tokens=4096),
    'book_synthesis': GenerationConfig(thinking='enabled', reasoning_effort='high', max_tokens=16384),
    'dialogue': GenerationConfig(thinking='enabled', reasoning_effort='low', max_tokens=12000),
    'consistency': GenerationConfig(thinking='enabled', reasoning_effort='low', max_tokens=12000),
    'other': GenerationConfig(),
}


def resolve_generation(task: str, policy: PolicyName | None, override: GenerationConfig | None) -> GenerationAudit:
    kind = task_type(task)
    fields = POLICY[kind].model_dump(exclude_none=True) if policy else {}
    if override:
        updates = override.model_dump(exclude_none=True)
        if override.thinking == 'disabled':
            fields.pop('reasoning_effort', None)
        elif override.reasoning_effort is not None:
            fields['thinking'] = 'enabled'
        fields.update(updates)
    # The policy budget is a ceiling; a user may request a smaller limit.
    ceiling = POLICY[kind].max_tokens if policy else None
    if ceiling is not None:
        fields['max_tokens'] = min(fields.get('max_tokens', ceiling), ceiling)
    return GenerationAudit(policy=policy, task_type=kind, options=GenerationConfig(**fields))
