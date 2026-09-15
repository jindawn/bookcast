"""Vendor-independent contracts plus deterministic, offline demonstration providers."""

from array import array
import math
import json
from pathlib import Path
import sys
import wave

from .models import Chapter, ChapterAnalysis, DialogueTurn, PodcastScript
from .provider_api import (LLMProvider, TTSProvider, ProviderCapabilities, ProviderStatus,
                           ProviderError, ErrorKind, SpeechInfo, T)


class MockLLMProvider:
    name = "mock"
    model = "mock-llm-v1"
    cache_key = "mock-llm:v1"

    def health_check(self) -> ProviderStatus:
        return ProviderStatus(provider=self.name, model=self.model, availability="available")

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(text=True, structured=True, local=True, mock=True)

    def generate(self, prompt: str) -> str:
        return f"[Mock] {prompt[:160]}"

    def generate_structured(self, prompt: str, response_model: type[T]) -> T:
        payload = json.loads(prompt)
        if payload.get("prompt_version") == "content-v1":
            from .content_mock import generate
            return response_model.model_validate(generate(payload).model_dump())
        chapter = Chapter.model_validate(payload["chapter"])
        if payload["operation"] == "analysis":
            result = self.analyze(chapter)
        elif payload["operation"] == "script":
            result = self.script(chapter, ChapterAnalysis.model_validate(payload["analysis"]))
        else:
            raise ProviderError(ErrorKind.INPUT)
        return response_model.model_validate(result.model_dump())

    def analyze(self, chapter: Chapter) -> ChapterAnalysis:
        excerpt = " ".join(chapter.text.split())[:160]
        return ChapterAnalysis(
            chapter_id=chapter.id,
            summary=f"[Mock 摘要]《{chapter.title}》的正文节选：{excerpt}",
            key_points=[f"本章共 {len(chapter.text)} 个字符。", "此摘要为测试模板，不代表真实 AI 理解。"],
            source_locator=chapter.source_locator,
        )

    def script(self, chapter: Chapter, analysis: ChapterAnalysis) -> PodcastScript:
        return PodcastScript(
            chapter_id=chapter.id, title=chapter.title, source_locator=chapter.source_locator,
            turns=[
                DialogueTurn(speaker="主持人", text=f"欢迎来到 BookCast Mock 播客。今天讨论《{chapter.title}》。"),
                DialogueTurn(speaker="嘉宾", text=analysis.summary),
                DialogueTurn(speaker="主持人", text="有哪些值得关注的线索？"),
                DialogueTurn(speaker="嘉宾", text=" ".join(analysis.key_points)),
            ],
        )


class MockTTSProvider:
    """Audible speaker-specific test tones, deliberately not synthesized speech."""

    name = "mock"
    model = "mock-tones-v1"
    cache_key = "mock-tts:tones-v1:24000"

    def health_check(self) -> ProviderStatus:
        return ProviderStatus(provider=self.name, model=self.model, availability="available")

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(speech=True, speech_units=True, local=True, mock=True)

    def synthesize(self, script: PodcastScript, destination: Path) -> None:
        self._synthesize_turns(script.turns, destination)

    def synthesize_unit(self, unit, destination):
        self._synthesize_turns([unit], destination)
        return SpeechInfo(audio_kind="mock", voice="tone-440" if unit.speaker == "主持人" else "tone-660")

    def _synthesize_turns(self, turns, destination):
        rate = 24_000
        with wave.open(str(destination), "wb") as audio:
            audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            for turn in turns:
                frequency = 440 if turn.speaker == "主持人" else 660
                length = int(rate * min(1.5, max(0.25, len(turn.text) * 0.01)))
                samples = array("h", (
                    int(3500 * min(1, index / 240, (length - index) / 240)
                        * math.sin(2 * math.pi * frequency * index / rate))
                    for index in range(length)
                ))
                if sys.byteorder != "little":
                    samples.byteswap()
                audio.writeframes(samples.tobytes())
                audio.writeframes(b"\0\0" * (rate // 10))
