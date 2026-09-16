"""Vendor-neutral bounded speech tasks, journaled by the existing Core runner."""

import json
import re

from .audio import concat_wav, validate_wav, wav_seconds
from .errors import BookCastError
from .provider_api import SpeechUnit, SpeechInfo, ProviderError, ErrorKind
from .storage import atomic_target, fingerprint, sha256_file, write_json

UNIT_VERSION = "speech-unit-v1:pcm24k"


def split_text(text: str, limit: int = 80) -> list[str]:
    """Preserve every character; prefer sentence boundaries without splitting decimals."""
    if not 1 <= limit <= 80:
        raise ValueError("speech unit limit must be between 1 and 80")
    result = []
    while text:
        if len(text) <= limit:
            result.append(text)
            break
        matches = list(re.finditer(r'[。！？!?；;\n]|[，,、]\s*|\s+', text[:limit]))
        cut = matches[-1].end() if matches else limit
        if len(text) - cut < limit and not any(c.isalnum() for c in text[cut:]):
            # Keep a final quotation mark/period attached to spoken text.
            last_word = next((i for i in range(cut - 1, 0, -1) if text[i].isalnum()), cut)
            if len(text) - last_word <= limit:
                cut = last_word
        result.append(text[:cut])
        text = text[cut:]
    return result


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
    if any(c.cloud for c in capabilities) and any(c.mock for c in capabilities):
        raise BookCastError("云端 TTS 链禁止回退 Mock 音调；请配置真实语音 Provider。")
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
        with atomic_target(r.path(audio_name)) as temporary:
            concat_wav([r.path(name) for name in audio], temporary)
        values = [json.loads(r.path(name).read_text(encoding="utf-8")) for name in reports]
        kinds = {value["audio_kind"] for value in values}
        info_name = f"audio/{segment}.json"
        write_json(r.path(info_name), {"audio_kind": next(iter(kinds)) if len(kinds) == 1 else "mixed",
            "duration_seconds": wav_seconds(r.path(audio_name)), "units": reports,
            "voices": list({(v['provider'], v['model'], v['voice'], v['speaker']):
                           {k: v[k] for k in ('provider', 'model', 'voice', 'speaker')} for v in values}.values())})
        return [audio_name, info_name]
    r.step(f"tts:{segment}", {"units": [(name, sha256_file(r.path(name))) for name in [*audio, *reports]],
                              "assembly": "pcm24k:pause-0.18-v1"}, assemble)


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
