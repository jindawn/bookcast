"""Bounded, source-linked contracts for hierarchical content generation."""
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .models import Model

Mode = Literal['summary', 'deep_read', 'two_host']
ShortText = Annotated[str, Field(min_length=1, max_length=240)]


class ContentOptions(Model):
    mode: Mode = 'two_host'
    minutes: int = Field(default=10, ge=1, le=120)


class Finding(Model):
    text: ShortText  # paraphrase; quote is private evidence, never a script instruction
    quote: str = Field(min_length=1, max_length=160)
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class RichAnalysis(Model):
    chapter_id: str
    chunk_id: str
    core_ideas: list[Finding] = Field(min_length=1, max_length=6)
    arguments: list[Finding] = Field(max_length=6)
    evidence: list[Finding] = Field(max_length=6)
    examples: list[Finding] = Field(max_length=6)
    people: list[Finding] = Field(max_length=6)
    concepts: list[Finding] = Field(max_length=6)
    counter_arguments: list[Finding] = Field(max_length=6)
    connections: list[Finding] = Field(max_length=6)
    key_passages: list[Finding] = Field(max_length=6)
    is_mock: bool = False


CATEGORIES = ('core_ideas', 'arguments', 'evidence', 'examples', 'people', 'concepts',
              'counter_arguments', 'connections', 'key_passages')


class EvidenceFinding(Model):
    text: ShortText
    evidence_id: str = Field(pattern=r'^e[0-9]{4}$')


class EvidenceAnalysis(Model):
    """Wire contract: the model selects evidence, the Core owns coordinates."""
    chapter_id: str
    chunk_id: str
    core_ideas: list[EvidenceFinding] = Field(min_length=1, max_length=6)
    arguments: list[EvidenceFinding] = Field(max_length=6)
    evidence: list[EvidenceFinding] = Field(max_length=6)
    examples: list[EvidenceFinding] = Field(max_length=6)
    people: list[EvidenceFinding] = Field(max_length=6)
    concepts: list[EvidenceFinding] = Field(max_length=6)
    counter_arguments: list[EvidenceFinding] = Field(max_length=6)
    connections: list[EvidenceFinding] = Field(max_length=6)
    key_passages: list[EvidenceFinding] = Field(max_length=6)
    is_mock: bool = False


class Theme(Model):
    title: ShortText
    summary: ShortText
    claim_ids: list[Annotated[str, Field(max_length=80)]] = Field(min_length=1, max_length=8)
    importance: int = Field(ge=1, le=5)


class Synthesis(Model):
    themes: list[Theme] = Field(min_length=1, max_length=8)
    is_mock: bool = False


class Segment(Model):
    id: str = Field(pattern=r'^[0-9]{4,}$')
    title: ShortText
    chapter_ids: list[str]
    claim_ids: list[str] = Field(min_length=1, max_length=8)
    seconds: int = Field(ge=1)
    target_chars: int = Field(ge=1)
    previous_topic: str
    next_topic: str


class EpisodePlan(Model):
    mode: Mode
    budget_seconds: int
    segments: list[Segment] = Field(min_length=1, max_length=24)
    covered_chapters: list[str]
    omitted_chapters: list[str]
    deduplicated_themes: int
    words_per_minute: int = 240  # Chinese character equivalent, explicitly an estimate


class ContentTurn(Model):
    speaker: Literal['主持人', '嘉宾']
    intent: Literal['explain', 'question', 'counterexample', 'application', 'transition']
    text: str = Field(min_length=1, max_length=2400)
    claim_ids: list[str] = Field(max_length=8)
    attribution: Literal['source', 'discussion', 'hypothetical']


class SegmentScript(Model):
    segment_id: str
    title: ShortText
    turns: list[ContentTurn] = Field(min_length=2, max_length=40)
    is_mock: bool = False

    @model_validator(mode='after')
    def bounded_script(self):
        if sum(len(t.text) for t in self.turns) > 12000:
            raise ValueError('segment script exceeds bounded review context')
        return self


class ClaimReview(Model):
    turn_index: int = Field(ge=0)
    verdict: Literal['supported', 'contradicted', 'unverifiable']
    reason: ShortText


class ConsistencyReview(Model):
    segment_id: str
    checks: list[ClaimReview] = Field(max_length=40)
    is_mock: bool = False
