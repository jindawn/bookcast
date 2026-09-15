"""Offline contract demonstration. Rule-based paraphrases are not model understanding."""
import re
from .content_models import CATEGORIES, ConsistencyReview, ClaimReview, Finding, RichAnalysis, EvidenceAnalysis, EvidenceFinding, SegmentScript, Synthesis, Theme, ContentTurn


def generate(payload):
    operation = payload['operation']
    if operation == 'consistency':
        return ConsistencyReview(segment_id=payload['script']['segment_id'], is_mock=True,
            checks=[ClaimReview(turn_index=i, verdict='unverifiable', reason='Mock 不具备语义核验能力；需人工对照引用证据。')
                    for i, t in enumerate(payload['script']['turns']) if t['attribution'] == 'source'])
    if operation == 'analysis':
        chapter, offset = payload['chapter'], payload['start']
        text = chapter['text']
        sentences = list(re.finditer(r'[^。！？\n.!?]+[。！？.!?]?', text))
        body = [m for m in sentences if m.group().strip() and m.group().strip() != chapter['title']]
        selected = (body or sentences)[:3]
        findings = []
        for match in selected:
            quote = match.group()[:120]
            # Short clauses, with explicit rephrasing; never reproduce a long source passage.
            clauses = [c.strip().rstrip('。！？.!?') for c in re.split('[，,；;：:]', quote) if c.strip()]
            core = clauses[0][:28]
            rest = clauses[1][:28] if len(clauses) > 1 else ''
            paraphrase = f'这里的讨论起点是{core}；' + (f'还需联系这一点：{rest}。' if rest else '具体适用范围仍要结合上下文判断。')
            if len(clauses) > 2:
                paraphrase += f'文中随后指出{clauses[2][:28]}。'
            findings.append(Finding(text=paraphrase[:240], quote=quote,
                                    start=offset+match.start(), end=offset+match.start()+len(quote)))
        if not findings:  # Chunk can contain only whitespace.
            findings = [Finding(text='此块没有可辨认的论证，需结合相邻文本核对。', quote=text[:1], start=offset, end=offset+1)]
        fields = {cat: [] for cat in CATEGORIES}
        fields['core_ideas'] = findings
        # Only classify what lexical evidence supports; absent facts stay empty.
        for finding in findings:
            if any(x in finding.quote for x in ('因为', '因此', '只有', '如果')):
                fields['arguments'].append(finding)
            if any(x in finding.quote for x in ('例如', '比如')):
                fields['examples'].append(finding)
            if any(x in finding.quote for x in ('但是', '然而', '并不', '不代表')):
                fields['counter_arguments'].append(finding)
        fields['key_passages'] = findings[:1]
        if payload['prompt_version'] == 'content-analysis-v3':
            selections = {cat: [EvidenceFinding(text=f.text, evidence_id=next(
                span['evidence_id'] for span in payload['evidence_spans'] if span['start'] <= f.start < span['end']))
                for f in items] for cat, items in fields.items()}
            return EvidenceAnalysis(chapter_id=chapter['id'], chunk_id=payload['chunk_id'], is_mock=True, **selections)
        return RichAnalysis(chapter_id=chapter['id'], chunk_id=payload['chunk_id'], is_mock=True, **fields)
    if operation == 'synthesis':
        unique = {}
        for raw in payload['themes']:
            theme = Theme.model_validate(raw)
            key = ''.join(theme.summary.split()).lower()
            if key not in unique:
                unique[key] = theme
            else:
                old = unique[key]
                old.claim_ids = list(dict.fromkeys(old.claim_ids + theme.claim_ids))[:8]
                old.importance = max(old.importance, theme.importance)
        return Synthesis(themes=list(unique.values())[:8], is_mock=True)
    if operation != 'dialogue':
        raise ValueError('unsupported content operation')
    segment, claims, mode = payload['segment'], payload['claims'], payload['mode']
    cid = segment['claim_ids'][0]
    claim = claims[cid]
    focus = claim['text']
    # A source-based explanation is separated from host interpretation and hypothetical cases.
    variant = (int(segment['id']) - 1) % 3
    turns = []
    def add(speaker, intent, text, attribution='discussion', ids=None):
        turns.append(ContentTurn(speaker=speaker, intent=intent, text=text, attribution=attribution, claim_ids=ids or []))
    if segment['previous_topic']:
        add('主持人', 'transition', f'刚才谈到{segment["previous_topic"]}，现在把问题推进到{segment["title"]}。')
    else:
        add('主持人', 'transition', f'我们从{segment["title"]}切入。这是离线规则示例，重点展示观点、追问和证据的关系。')
    add('主持人', 'explain', focus, 'source', [cid])
    if mode == 'two_host':
        add('嘉宾', 'question', f'你刚才把“{claim["quote"].split("，")[0][:18]}”作为起点。这个结论依赖哪些条件？如果条件不成立，是否还说得通？')
        supplemental = next(((key, value) for key, value in claims.items()
            if value['category'] in ('examples', 'arguments') and value['text'] != focus), None)
        if supplemental:
            extra_id, extra = supplemental
            add('主持人', 'explain', '本章还提供了一条相关线索：' + extra['text'], 'source', [extra_id])
        add('主持人', 'explain', [
            '需要先区分文中陈述和我们推出来的结果。已有片段能定位讨论起点，但不能据此断言所有场景都适用。这里保留条件，比给出一个无条件结论更准确。',
            '这个追问把焦点从结论拉回了论证。我们可以逐项列出成立所需的条件，再查看作者给出的材料究竟支持哪一步。缺少证据的部分，应当暂时悬置判断。',
            '先别急着把它当成行动指令。描述某种现象，并不自动说明每个人都该采取同一种办法。我们还需要比较环境、参与者和目标，确认这条推论是否越过了原文的范围。'][variant])
        add('嘉宾', 'counterexample', [
            '假设换一个参与者目标不同、可用资源也不同的场景，同样的做法可能得出另一种结果。这是检验适用边界的假设反例，不是书中已经发生的事实。',
            '假设参与者只看见自己这一步的成果，却看不到对别人造成的负担，表面上的改善还能算整体进步吗？这是我们为检验论证提出的情境，不能当作作者记载的案例。',
            '假设过去有效的安排碰上新的需求，继续照做反而产生损失，我们会怎样识别那个转折点？这个现实应用问题需要新的观察材料；原书的片段不能替我们预先给出答案。'][variant], 'hypothetical')
        add('主持人', 'explain', [
            '这个反例提醒我们：判断一个观点时，既要看它解释了什么，也要检查它没有覆盖什么。回到引用位置核对作者是否限定了条件，才能继续往下推。',
            '所以要把评价单位说清楚：个人、环节和整体可能指向不同结果。阅读时把这些层次分开，再追问代价落在谁身上，会比重复一个简短结论更接近问题本身。',
            '可以先把哪些条件变了记录下来，再寻找支持或反驳原判断的材料。这样的检验保留了修正观点的可能，也避免把作者的讨论误读成永远不变的答案。'][variant])
    elif mode == 'deep_read':
        for extra_id in segment['claim_ids'][1:3]:
            add('主持人', 'explain', claims[extra_id]['text'], 'source', [extra_id])
        add('主持人', 'question', '精读时先标出前提，再找从前提到结论的推理步骤。哪一步依赖例子，哪一步需要额外证据？不要把一则例子直接推广成普遍规律。')
        add('主持人', 'application', '假设把相同观点放进条件改变的场景，可以逐项检查原论证还剩哪些支持。这只是读者的检验方法，并非作者提供的额外事实。', 'hypothetical')
    else:
        add('主持人', 'explain', '抓住这个讨论起点之后，还应保留它的适用条件。压缩篇幅会舍去细节，因此这段概览应与对应章节的证据一起阅读。')
    if segment['next_topic']:
        add('主持人', 'transition', f'带着这个边界，下一段我们继续看{segment["next_topic"]}。')
    else:
        add('主持人', 'transition', '到这里可以回看各段的来源，区分作者的主张、已有证据和我们尚未验证的推论。')
    return SegmentScript(segment_id=segment['id'], title=segment['title'], turns=turns, is_mock=True)
