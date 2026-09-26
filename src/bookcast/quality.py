"""Deterministic diagnostics, not a semantic truth or copyright oracle."""
from collections import Counter
import re

from .source_sanitation import script_contamination_rule


def normalize(text):
    return ''.join(c.lower() for c in text if c.isalnum())


def evaluate(plan, scripts, claims, chapters, reviews=()):
    turns = [turn for script in scripts for turn in script.turns]
    spoken = ''.join(t.text for t in turns)
    compact = normalize(spoken)
    counts = Counter(compact[i:i+20] for i in range(max(0, len(compact)-19)))
    repetition = sum(n-1 for n in counts.values()) / max(1, sum(counts.values()))
    blocking, warnings, source_issues, covered = [], [], [], set()
    contamination = []
    role_chars = Counter()
    for segment, script in zip(plan.segments, scripts, strict=True):
        for turn_index, turn in enumerate(script.turns):
            role_chars[turn.speaker] += len(turn.text)
            contamination_rule = script_contamination_rule(turn.text)
            if contamination_rule:
                contamination.append({'segment': segment.id, 'turn_index': turn_index,
                                      'reason': 'source_contamination', 'matched_rule': contamination_rule})
            invalid = set(turn.claim_ids) - set(segment.claim_ids)
            if invalid:
                source_issues.append({'segment': segment.id, 'issue': 'unknown_or_out_of_segment_claim'})
            if turn.attribution == 'source' and not turn.claim_ids:
                source_issues.append({'segment': segment.id, 'issue': 'source_statement_without_evidence'})
            valid = [claims[c] for c in turn.claim_ids if c in claims and c in segment.claim_ids]
            if turn.attribution == 'source':
                covered.update(c['chapter_id'] for c in valid)
                allowed_numbers = set(re.findall(r'\d+(?:\.\d+)?', ' '.join(c['quote'] for c in valid)))
                if set(re.findall(r'\d+(?:\.\d+)?', turn.text)) - allowed_numbers:
                    source_issues.append({'segment': segment.id, 'issue': 'unsupported_numeric_claim'})
            if turn.attribution == 'hypothetical' and not any(w in turn.text for w in (
                '假设', '假如', '设想', '比如说', '比如', '譬如', '假使', '假若', '如果',
                '假設', '設想', '比如說', '假若', '假使'
            )):
                source_issues.append({'segment': segment.id, 'issue': 'unlabelled_hypothetical'})
        if plan.mode == 'two_host':
            if {t.speaker for t in script.turns} != {'主持人', '嘉宾'} or not any(
                    t.speaker == '嘉宾' and t.intent in {'question', 'counterexample', 'application'} for t in script.turns):
                blocking.append(f'{segment.id}: two_host 缺少双人角色或实质追问')
    semantic_issues = [{'segment': review.segment_id, **check.model_dump()} for review in reviews
                       for check in review.checks if check.verdict != 'supported']
    if any(c['verdict'] == 'contradicted' for c in semantic_issues):
        blocking.append('逐段一致性复核发现与来源矛盾的陈述')
    if any(c['verdict'] == 'unverifiable' for c in semantic_issues):
        warnings.append('部分来源陈述未完成语义核验；Mock 必须由人工复核。')
    if source_issues:
        blocking.append('来源引用或事实形式检查失败')
    if contamination:
        blocking.append('脚本包含来源推广内容；请检查 source_contamination。')
    # Across turn boundaries too. Fixed engineering guard, not a legal safe-harbor threshold.
    windows = {compact[i:i+80] for i in range(max(0, len(compact)-79))}
    overlap = False
    for chapter in chapters:
        text = normalize(chapter.text)
        if any(text[i:i+80] in windows for i in range(max(0, len(text)-79))):
            overlap = True
            break
    if overlap:
        blocking.append('检测到与源文本连续至少80个归一化字符相同的片段；请改为转述')
    all_chapters = {c.id for c in chapters}
    coverage = len(covered & all_chapters) / max(1, len(all_chapters))
    if coverage < 1:
        warnings.append('脚本未用来源发言覆盖全部章节；预算取舍见 omitted_chapters。')
    if repetition > .25:
        warnings.append('20字符窗口重复率超过25%。')
    length_ratio = len(spoken) / (plan.budget_seconds * 4)
    if not .65 <= length_ratio <= 1.35:
        warnings.append('脚本长度偏离预算估计超过35%；应人工扩写或删改。')
    b_ratio = role_chars['嘉宾'] / max(1, len(spoken))
    if plan.mode == 'two_host' and not .25 <= b_ratio <= .65:
        warnings.append('Host B 字符占比不在25%～65%之间。')
    vague = {w: spoken.count(w) for w in ('值得深思', '非常重要', '受益匪浅', '众所周知', '不言而喻') if w in spoken}
    if vague:
        warnings.append('检测到需人工检查的空泛表达。')
    return {'schema_version': 1, 'status': 'blocked' if blocking else 'needs_review' if warnings else 'checks_passed',
            'blocking_issues': blocking, 'warnings': warnings,
            'repetition_rate': round(repetition, 4), 'chapter_coverage': round(coverage, 4),
            'covered_chapters': sorted(covered), 'omitted_chapters': sorted(all_chapters-covered),
            'script_chars': len(spoken), 'target_chars': plan.budget_seconds*4,
            'estimated_seconds': round(len(spoken)/4, 1), 'length_ratio': round(length_ratio, 4),
            'role_chars': dict(role_chars), 'host_b_ratio': round(b_ratio, 4), 'vague_expressions': vague,
            'factual_consistency': {'reference_issues': source_issues, 'semantic_reviews': semantic_issues,
                                    'semantic_status': 'requires_human_review',
                                    'checks': ['evidence offsets checked during analysis', 'allowed claim IDs',
                                               'source attribution', 'numeric support', 'hypothetical labels']},
            'source_contamination': contamination,
            'verbatim_overlap_detected': overlap, 'is_mock': any(s.is_mock for s in scripts),
            'limitations': ['引用存在不证明推论正确；不检测所有语义矛盾、事实错误或抄袭。',
                            '80字符是保守产品拦截阈值，不是版权法律许可界线。',
                            '时长按240中文字符/分钟估计；真实语速和 Mock 音调时长不同。']}
