"""Vendor-neutral bounded speech tasks, journaled by the existing Core runner."""

import json
import re

from .audio import concat_wav, validate_wav, wav_seconds
from .errors import BookCastError
from .provider_api import SpeechUnit, SpeechInfo, ProviderError, ErrorKind
from .storage import atomic_target, fingerprint, sha256_file, write_json

UNIT_VERSION = "speech-unit-v1:pcm24k"


def normalize_tts_text(text: str) -> str:
    import re
    # 1. Remove Markdown formatting
    text = re.sub(r'[*_]{1,2}(.*?)[*_]{1,2}', r'\1', text)
    # 2. Remove Markdown links
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', text)
    # 3. Remove raw URLs
    text = re.sub(r'https?://[^\s]+', '', text)
    # 4. Remove parenthesis and their content
    text = re.sub(r'（[^）]*）|\([^)]*\)', '', text)
    # 5. Remove some punctuation that TTS struggles with or is unnecessary
    text = re.sub(r'[《》“”‘’"\'\']', '', text)
    # 6. Normalize spaces
    return re.sub(r'\s+', ' ', text).strip()

def split_text(text: str, limit: int = 200) -> list[str]:
    """Normalize text and split by punctuation up to a reasonable limit."""
    import re
    text = normalize_tts_text(text)
    result = []
    while text:
        if len(text) <= limit:
            result.append(text)
            break
        
        # Prefer strong punctuation
        matches = list(re.finditer(r'[。！？!?；;\n]', text[:limit]))
        if matches:
            cut = matches[-1].end()
        else:
            # Fallback to weak punctuation
            matches = list(re.finditer(r'[，,、：:——]\s*|\s+', text[:limit]))
            cut = matches[-1].end() if matches else limit
            
        # Avoid splitting numbers/English words
        if cut < len(text) and text[cut-1].isalnum() and text[cut].isalnum():
            # Backtrack to the last non-alnum character
            last_word = next((i for i in range(cut - 1, 0, -1) if not text[i].isalnum()), cut)
            if last_word > 0:
                cut = last_word

        result.append(text[:cut].strip())
        text = text[cut:].strip()
        
    return [r for r in result if r]


def speech_units(script):
    for turn_index, turn in enumerate(script.turns, 1):
        for chunk_index, text in enumerate(split_text(turn.text), 1):
            if not text.strip():
                continue
            yield f"{turn_index:04}-{chunk_index:04}", SpeechUnit(speaker=turn.speaker, text=text)


def wants_units(r, script, inputs):
    if any(name.startswith(f"tts_segment:{script.chapter_id}:") for name in r.manifest.steps):
        return False
    prefix = f"tts:{script.chapter_id}:"
    if any(name.startswith(prefix) for name in r.manifest.steps):
        return True
    old = r.manifest.steps.get(f"tts:{script.chapter_id}")
    # Respect completed artifacts from a provider removed from the chain (D-014).
    if (old and old.status == "completed" and old.input_hash == fingerprint(inputs)
            and r.step_config_valid(f"tts:{script.chapter_id}", old)
            and all(r.path(p).is_file() and sha256_file(r.path(p)) == h for p, h in old.artifacts.items())):
        return False
    return any(p.capabilities().speech_units and not p.capabilities().mock for p in r.tts.providers)


def wants_segments(r, script, inputs):
    if any(name.startswith(f"tts_segment:{script.chapter_id}:") for name in r.manifest.steps):
        return True
    if any(name.startswith(f"tts:{script.chapter_id}:") for name in r.manifest.steps):
        return False  # Existing Kokoro work keeps its unit checkpoints.
    old = r.manifest.steps.get(f"tts:{script.chapter_id}")
    if (old and old.status == "completed" and old.input_hash == fingerprint(inputs)
            and r.step_config_valid(f"tts:{script.chapter_id}", old)
            and all(r.path(p).is_file() and sha256_file(r.path(p)) == h for p, h in old.artifacts.items())):
        return False
    return any(p.capabilities().speech_segments for p in r.tts.providers)


def speech_tasks(r, script, inputs):
    if wants_segments(r, script, inputs):
        from .speech_segments import speech_segments
        return [f"tts_segment:{script.chapter_id}:{index}" for index, _ in speech_segments(script)]
    if wants_units(r, script, inputs):
        return [f"tts:{script.chapter_id}:{index}" for index, _ in speech_units(script)]
    return []


def validate_tts_chain(chain):
    capabilities = [p.capabilities() for p in chain.providers]
    if any(c.mock for c in capabilities) and not all(c.mock for c in capabilities):
        raise BookCastError("真实 TTS 链禁止回退 Mock 音调；请使用独立的显式 Mock 配置。")
    if any(c.speech_segments for c in capabilities) and not all(c.speech_segments for c in capabilities):
        raise BookCastError("片段 TTS 链要求所有成员支持 speech_segments；逐句和片段链请分别配置。")


def render_speech(r, script, inputs, version, legacy_inputs=None):
    if wants_segments(r, script, inputs):
        from .speech_segments import render_segments
        return render_segments(r, script)
    segment = script.chapter_id
    audio_name = f"audio/{segment}.wav"
    if not wants_units(r, script, inputs):
        def whole(provider):
            if not provider.capabilities().speech:
                raise ProviderError(ErrorKind.INPUT)
            with atomic_target(r.path(audio_name)) as temporary:
                provider.synthesize(script, temporary)
                check_audio(temporary)
            info_name = f"audio/{segment}.json"
            write_json(r.path(info_name), {"audio_kind": "mock" if provider.capabilities().mock else "speech",
                "duration_seconds": wav_seconds(r.path(audio_name)), "voices": [], "units": []})
            return [audio_name, info_name]
        r.step(f"tts:{segment}", inputs,
               lambda: r.ai_operation(f"tts:{segment}", "tts", version, inputs, whole), legacy_inputs=legacy_inputs)
        return

    units = list(speech_units(script))
    r.register([f"tts:{segment}:{index}" for index, _ in units])
    audio, reports = [], []
    for index, unit in units:
        task = f"tts:{segment}:{index}"
        name = f"audio/units/{segment}-{index}.wav"
        info_name = f"audio/units/{segment}-{index}.json"
        # Changes to other sentences do not invalidate an already completed unit.
        unit_inputs = {"unit": unit.model_dump(), "contract": UNIT_VERSION}
        def invoke(provider):
            if not provider.capabilities().speech_units:
                raise ProviderError(ErrorKind.INPUT)
            with atomic_target(r.path(name)) as temporary:
                info = SpeechInfo.model_validate(provider.synthesize_unit(unit, temporary))
                if (info.audio_kind == "mock") != provider.capabilities().mock:
                    raise ProviderError(ErrorKind.SCHEMA)
                check_audio(temporary)
            write_json(r.path(info_name), {**info.model_dump(), "provider": provider.name, "model": provider.model,
                "speaker": unit.speaker, "duration_seconds": wav_seconds(r.path(name))})
            return [name, info_name]
        r.step(task, unit_inputs, lambda: r.ai_operation(task, "tts", UNIT_VERSION, unit_inputs, invoke))
        audio.append(name)
        reports.append(info_name)

    def assemble():
        pauses = []
        for i in range(1, len(audio)):
            prev_turn = int(audio[i-1].split('-')[-2])
            curr_turn = int(audio[i].split('-')[-2])
            if prev_turn != curr_turn:
                # Different turn (usually speaker change)
                pauses.append(0.5)
            else:
                # Same turn
                pauses.append(0.2)
                
        with atomic_target(r.path(audio_name)) as temporary:
            concat_wav([r.path(name) for name in audio], temporary, pause_seconds=pauses if pauses else 0.18)
            
        values = [json.loads(r.path(name).read_text(encoding="utf-8")) for name in reports]
        kinds = {value["audio_kind"] for value in values}
        info_name = f"audio/{segment}.json"
        write_json(r.path(info_name), {"audio_kind": next(iter(kinds)) if len(kinds) == 1 else "mixed",
            "duration_seconds": wav_seconds(r.path(audio_name)), "units": reports,
            "voices": list({(v['provider'], v['model'], v['voice'], v['speaker']):
                           {k: v[k] for k in ('provider', 'model', 'voice', 'speaker')} for v in values}.values())})
        return [audio_name, info_name]
    r.step(f"tts:{segment}", {"units": [(name, sha256_file(r.path(name))) for name in [*audio, *reports]],
                              "assembly": "pcm24k:pause-dynamic-v1"}, assemble)


def check_audio(path):
    try:
        validate_wav(path)
    except BookCastError:
        raise ProviderError(ErrorKind.SCHEMA) from None


def audio_summary(r, ids):
    kinds, voices = set(), []
    for segment in ids:
        sidecar = f"audio/{segment}.json"
        record = r.manifest.steps[f"tts:{segment}"]
        if sidecar in record.artifacts:
            value = json.loads(r.path(sidecar).read_text(encoding="utf-8"))
            kinds.add(value["audio_kind"])
            voices.extend(value["voices"])
        else:
            # Historical data has no explicit audio type; do not infer from today's config.
            kinds.add("unknown")
    kind = next(iter(kinds)) if len(kinds) == 1 else "mixed"
    return {"audio_kind": kind, "voices": list({fingerprint(v): v for v in voices}.values()),
            "duration_seconds": round(sum(wav_seconds(r.path(f"audio/{s}.wav")) for s in ids), 3),
            "note": {"speech": "本地/已配置 TTS 合成人声；脚本质量与音频时长需独立检查。",
                     "mixed": "音频包含不同类型或历史来源，详见逐句语音记录。",
                     "unknown": "旧式整段音频未记录类型；内置 Mock TTS 生成测试音调（非人声）。",
                     "mock": "内置 Mock TTS 生成测试音调（非人声）。"}[kind]}


def validate_completed_speech(r, ids):
    """A finished job must match the selected speech chain and every segment."""
    expected = 'mock' if all(p.capabilities().mock for p in r.tts.providers) else 'speech'
    allowed = {p.name for p in r.tts.providers}
    for segment in ids:
        name = f'tts:{segment}'
        record = r.manifest.steps.get(name)
        sidecar = r.path(f'audio/{segment}.json')
        if not record or record.status != 'completed' or not sidecar.is_file():
            raise BookCastError(f'语音片段 {segment} 未完整生成；任务不能标记完成。')
        try:
            kind = json.loads(sidecar.read_text(encoding='utf-8'))['audio_kind']
        except (OSError, ValueError, KeyError, TypeError):
            raise BookCastError(f'语音片段 {segment} 来源记录无效；任务不能标记完成。') from None
        if kind != expected:
            raise BookCastError(f'语音片段 {segment} 来源与当前 TTS 配置不符；任务不能标记完成。')
        tasks = [task for task in r.manifest.steps if task == name or task.startswith(name + ':')
                 or task.startswith(f'tts_segment:{segment}:')]
        sources = []
        for task in tasks:
            active = r.manifest.steps[task]
            matching = next((call for call in reversed(r.manifest.ai_calls)
                             if call.kind == 'tts' and call.task == task and call.status == 'completed'
                             and call.artifacts == active.artifacts), None)
            if matching:
                sources.append(matching)
            elif task != name and active.status == 'completed':
                raise BookCastError(f'语音片段 {segment} 缺少 Provider 来源；任务不能标记完成。')
        if not sources and r.manifest.legacy_config and r.manifest.provider_settings is None:
            # Pre-v3 manifests can have verified audio without an Attempt journal.
            # Their configured chain and sidecar kind still have to agree.
            continue
        if not sources or any(call.provider not in allowed or not r.config_valid(call) for call in sources):
            raise BookCastError(f'语音片段 {segment} Provider 记录与当前配置不符；任务不能标记完成。')
