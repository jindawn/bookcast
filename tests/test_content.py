"""Content hierarchy, evidence contracts, evaluation and resumability, entirely offline."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner
from bookcast.cli import app
from bookcast.content import CHUNK_CHARS, FAN_IN, MAX_PROMPT_CHARS, chunks, planner
from bookcast.content_models import (CATEGORIES, ContentOptions, EpisodePlan, RichAnalysis,
                                    SegmentScript, Synthesis, Theme)
from bookcast.errors import BookCastError
from bookcast.models import Chapter
from bookcast.pipeline import Pipeline, load_manifest, job_status
from bookcast.provider_api import ErrorKind, ProviderError
from bookcast.provider_chain import ProviderChain
from bookcast.providers import MockLLMProvider, MockTTSProvider
from bookcast.quality import evaluate
from bookcast.storage import sha256_file

DEMO = Path(__file__).resolve().parents[1] / 'examples/content-demo.txt'


class Recording(MockLLMProvider):
    def __init__(self, name='a', fail=None, mutation=None):
        self.name, self.fail, self.mutation, self.calls = name, fail, mutation, []

    def generate_structured(self, prompt, response_model):
        data = json.loads(prompt)
        self.calls.append((data, len(prompt)))
        if self.fail:
            self.fail(data)
        result = super().generate_structured(prompt, response_model)
        return self.mutation(data, result) if self.mutation else result


def snapshot(root):
    return {str(p): (p.stat().st_mtime_ns, sha256_file(p)) for p in root.rglob('*') if p.is_file() and p.name != '.lock'}


def unchanged(before):
    assert all((Path(p).stat().st_mtime_ns, sha256_file(Path(p))) == value for p, value in before.items())


@pytest.mark.parametrize('mode', ['summary', 'deep_read', 'two_host'])
def test_modes_plan_before_writing_and_quality_before_tts(tmp_path, mode):
    llm = Recording()
    class TTS(MockTTSProvider):
        def synthesize(self, script, destination):
            job = next(tmp_path.glob('*/manifest.json')).parent
            report = json.loads((job/'evaluation/quality.json').read_text())
            assert not report['blocking_issues']
            assert len(list((job/'evaluation/segments').glob('*.json'))) == len(list((job/'scripts').glob('*.json')))
            return super().synthesize(script, destination)
    pipeline = Pipeline(llm, TTS(), tmp_path)
    job = pipeline.generate(DEMO, mode=mode, minutes=3)
    manifest = load_manifest(job/'manifest.json')
    plan = EpisodePlan.model_validate_json((job/'plans/episode.json').read_text())
    assert plan.mode == mode and sum(s.seconds for s in plan.segments) == 180
    assert plan.covered_chapters == ['0001', '0002', '0003']
    report = json.loads((job/'evaluation/quality.json').read_text())
    assert report['chapter_coverage'] == 1 and report['is_mock'] and report['status'] == 'needs_review'
    assert all(len(d['chapter']['text']) <= CHUNK_CHARS for d, _ in llm.calls if d['operation']=='analysis')
    script_calls = [d for d, _ in llm.calls if d['operation']=='dialogue']
    assert script_calls[0]['previous_ending'] == []
    for prior, following in zip(plan.segments, script_calls[1:]):
        actual = SegmentScript.model_validate_json((job/f'scripts/{prior.id}.json').read_text())
        assert following['previous_ending'] == [{'speaker': t.speaker, 'text': t.text[-240:]} for t in actual.turns[-2:]]
    for p in (job/'analysis/chunks').glob('*.json'):
        result = RichAnalysis.model_validate_json(p.read_text())
        assert all(hasattr(result, category) for category in CATEGORIES)
        chapter = Chapter.model_validate_json((job/f'chapters/{result.chapter_id}.json').read_text())
        for category in CATEGORIES:
            assert all(chapter.text[f.start:f.end] == f.quote for f in getattr(result, category))
    assert manifest.pipeline_version == '2'
    before, count = snapshot(job), len(llm.calls)
    pipeline.generate(DEMO, resume=True)  # omitted mode/budget retain stored options
    unchanged(before)
    assert len(llm.calls) == count


def test_long_chapter_and_book_use_bounded_hierarchy(tmp_path):
    source = tmp_path/'long.txt'
    source.write_text('Chapter 1\n' + ('Repeated input needs bounded chunks.\n'*1300) +
                      '\n'.join(f'Chapter {i}\nUnique topic {i}.' for i in range(2, 19)))
    llm = Recording()
    job = Pipeline(llm, MockTTSProvider(), tmp_path/'out').generate(source, mode='summary', minutes=10)
    analyses = [d for d, _ in llm.calls if d['operation']=='analysis']
    parts = [d for d in analyses if d['chapter']['id']=='0001']
    chapter = Chapter.model_validate_json((job/'chapters/0001.json').read_text())
    assert ''.join(d['chapter']['text'] for d in parts) == chapter.text
    assert len(parts)>FAN_IN and len(analyses)>18
    assert max(n for _, n in llm.calls)<=MAX_PROMPT_CHARS
    assert all(len(d['themes'])<=FAN_IN for d, _ in llm.calls if d['operation']=='synthesis')
    assert all('chapter' not in d for d, _ in llm.calls if d['operation']!='analysis')
    assert (job/'synthesis/book/02-0000.json').exists()
    assert list((job/'synthesis/chapters/0001').glob('01-*.json'))
    assert job_status(str(job))['integrity']=='ok'


@pytest.mark.parametrize('operation', ['analysis', 'synthesis', 'dialogue', 'consistency'])
def test_failover_and_resume_never_repeat_completed_calls(tmp_path, operation):
    count = 0
    def fail(data):
        nonlocal count
        if data['operation'] == operation:
            count += 1
            if count == 2:
                raise ProviderError(ErrorKind.QUOTA)
    a, b = Recording(fail=fail), Recording('b')
    job = Pipeline(ProviderChain([a,b]), MockTTSProvider(), tmp_path).generate(DEMO)
    assert b.calls
    manifest = load_manifest(job/'manifest.json')
    failures = [c for c in manifest.ai_calls if c.status=='failed_retryable']
    assert len(failures)==1 and failures[0].error=='quota_exhausted'
    completed = [c for c in manifest.ai_calls if c.status=='completed']
    assert len({c.task for c in completed})==len(completed)
    before = snapshot(job)
    c = Recording('c')
    Pipeline(c, MockTTSProvider(), tmp_path).generate(DEMO,resume=True)
    unchanged({p:v for p,v in before.items() if not p.endswith('manifest.json')})
    assert not c.calls


def test_chunk_interruption_resumes_next_chunk(tmp_path):
    source = tmp_path/'long.txt'
    source.write_text('Chapter 1\n' + '一个独立论点需要证据。'*1000)
    def fail(data):
        if data['operation']=='analysis' and data['chunk_id']=='0002':
            raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        Pipeline(Recording(fail=fail),MockTTSProvider(),tmp_path/'out').generate(source)
    job = next((tmp_path/'out').iterdir())
    before=snapshot(job/'analysis/chunks')
    b=Recording('b')
    Pipeline(b,MockTTSProvider(),tmp_path/'out').generate(source,resume=True)
    unchanged(before)
    assert not any(d['operation']=='analysis' and d['chunk_id']=='0001' for d,_ in b.calls)


@pytest.mark.parametrize('bad', ['offset','schema','synthesis_ref','review_index'])
def test_invalid_structured_data_is_permanent(tmp_path,bad):
    def mutation(data,value):
        if bad=='offset' and data['operation']=='analysis':
            value.core_ideas[0].end+=1
        if bad=='schema' and data['operation']=='analysis':
            return {'no':'contract'}
        if bad=='synthesis_ref' and data['operation']=='synthesis':
            value.themes[0].claim_ids=['unknown']
        if bad=='review_index' and data['operation']=='consistency':
            value.checks[0].turn_index=99
        return value
    b=Recording('backup')
    with pytest.raises(BookCastError):
        Pipeline(ProviderChain([Recording(mutation=mutation),b]),MockTTSProvider(),tmp_path).generate(DEMO)
    assert not b.calls
    manifest=load_manifest(next(tmp_path.glob('*/manifest.json')))
    assert manifest.ai_calls[-1].status=='failed_permanent'


@pytest.mark.parametrize('bad', ['unknown_ref','no_ref','number','quote','roles','contradiction'])
def test_quality_blocks_before_tts_and_persists_report(tmp_path,bad):
    def mutation(data,value):
        if data['operation']=='dialogue':
            turn=next(t for t in value.turns if t.attribution=='source')
            if bad=='unknown_ref': turn.claim_ids=['unknown']
            if bad=='no_ref': turn.claim_ids=[]
            if bad=='number': turn.text='作者认为利润是9999元。'
            if bad=='quote':
                # Same source, split over turns to exercise cross-turn quotation detection.
                raw=DEMO.read_text().split('第二章')[0].replace('\n','')
                value.turns[0].text=raw[:50]
                value.turns[1].text=raw[50:]
            if bad=='roles':
                for t in value.turns: t.speaker='主持人'
        if data['operation']=='consistency' and bad=='contradiction':
            value.checks[0].verdict='contradicted'
        return value
    with patch.object(MockTTSProvider,'synthesize') as tts:
        with pytest.raises(BookCastError,match='质量检查'):
            Pipeline(Recording(mutation=mutation),MockTTSProvider(),tmp_path).generate(DEMO)
        tts.assert_not_called()
    job=next(tmp_path.iterdir())
    assert json.loads((job/'evaluation/quality.json').read_text())['blocking_issues']
    assert not (job/'podcast.mp3').exists()


def test_targeted_revision_retains_analysis_and_repairs_quality_failure(tmp_path):
    def corrupt(data,value):
        if data['operation']=='dialogue' and data['segment']['id']=='0002':
            next(t for t in value.turns if t.attribution=='source').claim_ids=[]
        return value
    with pytest.raises(BookCastError):
        Pipeline(Recording(mutation=corrupt),MockTTSProvider(),tmp_path).generate(DEMO)
    job=next(tmp_path.iterdir())
    before={**snapshot(job/'analysis'),**snapshot(job/'synthesis'),**snapshot(job/'plans')}
    b=Recording('b')
    Pipeline(b,MockTTSProvider(),tmp_path).generate(DEMO,resume=True,revise_segment='0002')
    unchanged(before)
    assert [d['segment']['id'] for d,_ in b.calls if d['operation']=='dialogue']==['0002']
    assert not json.loads((job/'evaluation/quality.json').read_text())['blocking_issues']
    assert load_manifest(job/'manifest.json').segment_revisions=={'0002':1}


def test_plan_importance_dedup_budget_and_omissions():
    ts=[Theme(title='t'+str(i),summary='topic'+str(i),claim_ids=[str(i)],importance=i) for i in range(1,6)]
    pairs=[(str(i),[t]) for i,t in enumerate(ts,1)]
    plan=planner(pairs,Synthesis(themes=[ts[-1]]),{str(i):{} for i in range(1,6)},ContentOptions(minutes=1))
    assert len(plan.segments)==2 and plan.covered_chapters==['4','5']
    assert plan.omitted_chapters==['1','2','3'] and sum(s.seconds for s in plan.segments)==60
    duplicate=planner([('1',[ts[0]]),('2',[ts[0]])],Synthesis(themes=[ts[0]]),{'1':{}},ContentOptions())
    assert len(duplicate.segments)==1 and duplicate.deduplicated_themes==1


def test_cli_modes_invalid_config_and_acquire_bridge(tmp_path):
    runner=CliRunner()
    result=runner.invoke(app,['acquire','Content Demo','--file',str(DEMO),'--generate','--mode','deep_read',
        '--minutes','3','--output-dir',str(tmp_path/'import'),'--pipeline-output-dir',str(tmp_path/'audio')])
    assert result.exit_code==0,result.output
    job=Path(json.loads(result.stdout)['pipeline_job'])
    assert load_manifest(job/'manifest.json').content_options['mode']=='deep_read'
    for args in (['--mode','wrong'],['--minutes','0'],['--revise-segment','0001']):
        result=runner.invoke(app,['generate',str(DEMO),'--output-dir',str(tmp_path/'invalid'),*args])
        assert result.exit_code!=0
    pipeline=Pipeline(MockLLMProvider(),MockTTSProvider(),tmp_path/'out')
    job=pipeline.generate(DEMO,mode='summary',minutes=2)
    before=snapshot(job)
    for options in ({'mode':'two_host'},{'minutes':3},{'revise_segment':'9999'}):
        with pytest.raises(BookCastError): pipeline.generate(DEMO,resume=True,**options)
    unchanged(before)


def test_quality_metrics_detect_repetition_length_vague_language_and_real_coverage(tmp_path):
    job=Pipeline(MockLLMProvider(),MockTTSProvider(),tmp_path).generate(DEMO,minutes=3)
    plan=EpisodePlan.model_validate_json((job/'plans/episode.json').read_text())
    scripts=[SegmentScript.model_validate_json((job/f'scripts/{s.id}.json').read_text()) for s in plan.segments]
    claims=json.loads((job/'analysis/claims.json').read_text())
    chapters=[Chapter.model_validate_json(p.read_text()) for p in sorted((job/'chapters').glob('*.json'))]
    for script in scripts:
        for turn in script.turns:
            turn.text='众所周知，这是一个值得深思的问题。'*30
            turn.attribution='discussion'
            turn.claim_ids=[]
            turn.speaker='主持人'
    report=evaluate(plan,scripts,claims,chapters)
    assert report['repetition_rate']>.8 and report['chapter_coverage']==0
    assert report['host_b_ratio']==0 and report['vague_expressions']['众所周知']>1
    assert report['script_chars']>report['target_chars']*1.35
    assert report['blocking_issues'] and len(report['warnings'])>=4


def test_chunk_boundaries_are_contiguous_and_quote_offsets_survive_unicode():
    chapter=Chapter(id='0001',title='标题',text=('😀中文 abc\n'*1500),source_locator='lines:1-1500')
    parts=list(chunks(chapter))
    assert len(parts)>1
    assert ''.join(p['chapter']['text'] for p in parts)==chapter.text
    assert [p['start'] for p in parts]==list(range(0,len(chapter.text),CHUNK_CHARS))


def test_revision_propagates_actual_ending_changes_without_reanalyzing(tmp_path):
    job=Pipeline(MockLLMProvider(),MockTTSProvider(),tmp_path).generate(DEMO)
    before={**snapshot(job/'analysis'),**snapshot(job/'synthesis'),**snapshot(job/'plans')}
    def revise(data,value):
        if data['operation']=='dialogue' and data['segment']['id']=='0002':
            value.turns[-1].text='接下来换一个角度：安排何时需要重新调整？'
        return value
    llm=Recording(mutation=revise)
    Pipeline(llm,MockTTSProvider(),tmp_path).generate(DEMO,resume=True,revise_segment='0002')
    unchanged(before)
    calls=[d for d,_ in llm.calls if d['operation']=='dialogue']
    assert [d['segment']['id'] for d in calls]==['0002','0003']
    assert calls[-1]['previous_ending'][-1]['text']=='接下来换一个角度：安排何时需要重新调整？'


def test_non_core_finding_change_refreshes_claims_cache(tmp_path):
    job=Pipeline(MockLLMProvider(),MockTTSProvider(),tmp_path).generate(DEMO)
    original=json.loads((job/'analysis/claims.json').read_text())
    (job/'analysis/chunks/0001-0001.json').write_text('damaged')
    def revise(data,value):
        if data['operation']=='analysis' and data['chapter']['id']=='0001':
            value.key_passages[0].text='不同的证据转述，引用位置保持不变。'
        return value
    llm=Recording(mutation=revise)
    Pipeline(llm,MockTTSProvider(),tmp_path).generate(DEMO,resume=True)
    now=json.loads((job/'analysis/claims.json').read_text())
    assert now['0001:0001:key_passages:0']['text']!=original['0001:0001:key_passages:0']['text']
    assert [d['chapter']['id'] for d,_ in llm.calls if d['operation']=='analysis']==['0001']
    assert job_status(str(job))['integrity']=='ok'


def test_prompt_and_script_bounds_are_enforced():
    from bookcast.content import prompt
    from pydantic import ValidationError
    with pytest.raises(BookCastError,match='上下文'):
        prompt('synthesis',themes=['x'*MAX_PROMPT_CHARS])
    with pytest.raises(ValidationError):
        SegmentScript(segment_id='0001',title='t',turns=[{'speaker':'主持人','intent':'explain',
            'attribution':'discussion','claim_ids':[],'text':'x'*2400} for _ in range(6)])
