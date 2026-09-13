"""Vendor-independent contracts plus deterministic, offline demonstration providers."""

from array import array
import math
from pathlib import Path
import sys
from typing import Protocol
import wave

from .models import Chapter, ChapterAnalysis, DialogueTurn, PodcastScript


class LLMProvider(Protocol):
    @property
    def cache_key(self) -> str:
        """Stable identity including model, configuration and prompt revision."""
        ...

    def analyze(self, chapter: Chapter) -> ChapterAnalysis: ...

    def script(self, chapter: Chapter, analysis: ChapterAnalysis) -> PodcastScript: ...


class TTSProvider(Protocol):
    @property
    def cache_key(self) -> str: ...

    def synthesize(self, script: PodcastScript, destination: Path) -> None:
        """Write a complete 24 kHz mono PCM16 WAV at destination or raise."""
        ...


class MockLLMProvider:
    cache_key = "mock-llm:v1"

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

    cache_key = "mock-tts:tones-v1:24000"

    def synthesize(self, script: PodcastScript, destination: Path) -> None:
        rate = 24_000
        with wave.open(str(destination), "wb") as audio:
            audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            for turn in script.turns:
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
