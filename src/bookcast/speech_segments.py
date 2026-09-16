"""Bounded dialogue synthesis; no vendor protocol or implicit requests in Core."""
import json
import re

from .audio import concat_wav, wav_seconds
from .provider_api import SpeechTurn, SpeechSegment, SegmentSpeechInfo, ProviderError, ErrorKind
from .storage import atomic_target, sha256_file, write_json

SEGMENT_VERSION = "speech-segment-v1:pcm24k"
MAX_CHARS = 600
MAX_TURNS = 24


def speech_segments(script):
    """Never cross an episode topic; pack complete turns, split only oversized turns.

    Preserve all characters and speaker order. Punctuation is preferred only when
    a single turn exceeds the bound, keeping ordinary question/answer pairs intact.
    """
    pending, size, number = [], 0, 1
    for turn in script.turns:
        text = turn.text
        while text:
            cut = min(len(text), MAX_CHARS)
            if len(text) > MAX_CHARS:
                boundaries = list(re.finditer(r'[。！？!?；;\n]|\s+', text[:MAX_CHARS]))
                if boundaries:
                    cut = boundaries[-1].end()
            part, text = text[:cut], text[cut:]
            if pending and (size + len(part) > MAX_CHARS or len(pending) == MAX_TURNS):
                yield f"{number:04}", SpeechSegment(turns=pending)
                pending, size, number = [], 0, number + 1
            pending.append(SpeechTurn(speaker=turn.speaker, text=part))
            size += len(part)
    if pending:
        yield f"{number:04}", SpeechSegment(turns=pending)


def render_segments(r, script):
    from .speech import check_audio
    parts = list(speech_segments(script))
    r.register([f"tts_segment:{script.chapter_id}:{index}" for index, _ in parts])
    audio, reports = [], []
    for index, segment in parts:
        task = f"tts_segment:{script.chapter_id}:{index}"
        name = f"audio/segments/{script.chapter_id}-{index}.wav"
        sidecar = f"audio/segments/{script.chapter_id}-{index}.json"
        inputs = {"segment": segment.model_dump(), "contract": SEGMENT_VERSION}

        def invoke(provider):
            caps = provider.capabilities()
            if not caps.speech_segments or caps.mock:
                raise ProviderError(ErrorKind.INPUT)
            if len({t.speaker for t in segment.turns}) > 1 and not caps.multi_speaker:
                raise ProviderError(ErrorKind.INPUT)
            with atomic_target(r.path(name)) as temporary:
                info = SegmentSpeechInfo.model_validate(provider.synthesize_segment(segment, temporary))
                if set(info.voices) != {t.speaker for t in segment.turns}:
                    raise ProviderError(ErrorKind.SCHEMA)
                check_audio(temporary)
            write_json(r.path(sidecar), {"audio_kind": info.audio_kind, "voices": [
                {"provider": provider.name, "model": provider.model, "speaker": speaker, "voice": voice}
                for speaker, voice in info.voices.items()], "duration_seconds": wav_seconds(r.path(name)),
                "contract": SEGMENT_VERSION})
            return [name, sidecar]

        r.step(task, inputs, lambda: r.ai_operation(task, "tts", SEGMENT_VERSION, inputs, invoke))
        audio.append(name)
        reports.append(sidecar)

    def assemble():
        output, report = f"audio/{script.chapter_id}.wav", f"audio/{script.chapter_id}.json"
        with atomic_target(r.path(output)) as temporary:
            concat_wav([r.path(p) for p in audio], temporary)
        values = [json.loads(r.path(p).read_text()) for p in reports]
        voices = [v for record in values for v in record['voices']]
        write_json(r.path(report), {"audio_kind": "speech", "duration_seconds": wav_seconds(r.path(output)),
            "segments": reports, "voices": list({(v['provider'], v['model'], v['speaker'], v['voice']): v for v in voices}.values())})
        return [output, report]

    r.step(f"tts:{script.chapter_id}", {"segments": [(p, sha256_file(r.path(p))) for p in [*audio, *reports]],
        "assembly": "pcm24k:pause-0.18-v1"}, assemble)
