"""Run one frozen positive case through the production consistency entry only.

This is an explicitly opt-in live harness. It never generates dialogue or audio.
"""

import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from bookcast.canary_consistency import canary_summary
from bookcast.composition import settings_snapshot
from bookcast.content import ContentFlow
from bookcast.content_models import ConsistencyReview, ContentOptions, Segment, SegmentScript
from bookcast.cost import make_cost_snapshot
from bookcast.models import Manifest
from bookcast.pipeline import _Runner
from bookcast.provider_api import ErrorKind, ProviderError
from bookcast.provider_config import load_config
from bookcast.provider_registry import default_registry
from bookcast.shadow_consistency import tier2_review_prompt, validate_two_tier_providers
from bookcast.storage import fingerprint, sha256_file, write_json


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/phase19_3b_consistency.json'
OUTPUT = ROOT / 'output/acceptance-canary-phase19-3d-positive'
CASE_ID = 'case_4_numerical_error'


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit('Positive-path output already exists; refusing to repeat a live sample')
    for name in ('DEEPSEEK_API_KEY', 'DASHSCOPE_API_KEY'):
        value = os.environ.get(name, '')
        print(f'{name}: configured={str(bool(value)).lower()} length={len(value)}')
        if not value:
            raise SystemExit('Required provider configuration is missing')

    source = json.loads(FIXTURE.read_text(encoding='utf-8'))
    cases = source['fixtures'] if isinstance(source, dict) else source
    case = next(row for row in cases if row['id'] == CASE_ID)
    script = SegmentScript.model_validate(case['script'])
    claims = case['claims']
    expected = case['ground_truth']['flawed_turn_indices']
    if expected != [4] or script.turns[4].attribution != 'source':
        raise SystemExit('Frozen positive fixture contract changed')
    segment = Segment(id=case['segment_id'], title=script.title, chapter_ids=['0001'],
                      claim_ids=list(claims), seconds=180, target_chars=600,
                      previous_topic='', next_topic='')
    options = ContentOptions(consistency_mode='two_tier', consistency_canary_audit=True)
    settings = load_config(ROOT / 'examples/model-routing-cloud.toml')
    registry = default_registry()
    llm = registry.chain(settings, 'llm')
    tts = registry.chain(settings, 'tts')  # Constructed for _Runner only; never invoked.
    validate_two_tier_providers(llm)
    provider_settings = settings_snapshot(settings, 'auto', 'auto')
    manifest = Manifest(job_id=uuid4().hex, output_id=OUTPUT.name,
                        book_id=fingerprint({'fixture': CASE_ID})[:24],
                        source_sha256=sha256_file(FIXTURE), source_name=FIXTURE.name,
                        source_format='txt', config={'llm': llm.cache_key, 'tts': tts.cache_key},
                        provider_settings=provider_settings,
                        cost_snapshot=make_cost_snapshot(provider_settings),
                        pipeline_version='2', content_options=options.model_dump())
    OUTPUT.mkdir(parents=True)
    (OUTPUT / 'source').mkdir()
    shutil.copyfile(FIXTURE, OUTPUT / 'source/input.txt')
    runner = _Runner(OUTPUT, manifest, llm, tts)
    runner.save()
    flow = ContentFlow.__new__(ContentFlow)
    flow.r, flow.options = runner, options

    source_turns = {i for i, turn in enumerate(script.turns) if turn.attribution == 'source'}

    def validate_review(review: ConsistencyReview) -> None:
        if review.segment_id != segment.id or {c.turn_index for c in review.checks} != source_turns:
            raise ProviderError(ErrorKind.SCHEMA)

    try:
        production = flow._consistency_review(segment, script, claims, 0, validate_review)
        # The audit is sidecar-only, exactly as in the production ContentFlow.
        flow._run_canary_audit(segment, script, claims, 0, production)
    finally:
        runner.update_usage_views_safely()
        runner.save()

    tier1 = json.loads((OUTPUT / 'evaluation/two_tier/tier1/0001.json').read_text())
    audit_record = json.loads((OUTPUT / 'evaluation/canary/0001.json').read_text())
    summary = canary_summary(manifest, [audit_record], OUTPUT / 'usage/physical_requests.jsonl')
    write_json(OUTPUT / 'evaluation/canary_comparison.json', summary)
    targeted = [call for call in manifest.ai_calls if call.task == 'consistency:tier2:0001']
    audit = [call for call in manifest.ai_calls if call.task == 'consistency:audit:0001']
    suspicious = tier1['suspicious_turn_ids']
    targeted_wire = json.loads(tier2_review_prompt(segment.id, script, claims, suspicious, 0)) if suspicious else None
    payload_scoped = bool(targeted_wire and
                          [turn['turn_index'] for turn in targeted_wire['script']['turns']] == suspicious and
                          set(targeted_wire['claims']) == {
                              cid for index in suspicious for cid in script.turns[index].claim_ids if cid in claims})
    risk = {check.turn_index for check in production.checks if check.verdict in {'contradicted', 'unverifiable'}}
    accepted = (tier1['status'] == 'REVIEW' and suspicious == expected and len(targeted) == 1 and
                targeted[0].status == 'completed' and payload_scoped and 4 in risk and
                len(audit) == 1 and audit[0].status == 'completed' and
                summary['audit_status'] == 'completed' and
                summary['potential_false_negative'] is False and
                audit_record['verdict_agreement'] and audit_record['flagged_turn_agreement'] and
                all(row['pricing_status'] == 'known' for row in
                    (summary['two_tier_production']['qwen'],
                     summary['two_tier_production']['targeted_deepseek'], summary['full_audit'])))
    print(json.dumps({'decision': 'POSITIVE_PATH_ACCEPTED' if accepted else 'POSITIVE_PATH_REJECTED',
                      'fixture': CASE_ID, 'fixture_sha256': sha256_file(FIXTURE),
                      'tier1_status': tier1['status'], 'suspicious_turn_ids': suspicious,
                      'targeted_calls': len(targeted), 'targeted_payload_scoped': payload_scoped,
                      'production_risk_turns': sorted(risk), 'audit_status': summary['audit_status'],
                      'potential_false_negative': summary['potential_false_negative'],
                      'physical_requests': len((OUTPUT / 'usage/physical_requests.jsonl').read_text().splitlines()),
                      'output': str(OUTPUT)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
