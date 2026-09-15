"""Optional CPU adapter; no downloads, credentials, SDK imports in Core, or network."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tempfile
import wave

from ..audio import concat_wav
from ..errors import BookCastError
from ..provider_api import (ErrorKind, ProviderError, ProviderCapabilities, ProviderStatus, SpeechInfo, SpeechUnit)
from ..storage import fingerprint
from ..tts_setup import verify_model

VOICES = {45: "zf_xiaobei", 46: "zf_xiaoni", 47: "zf_xiaoxiao", 48: "zf_xiaoyi",
          49: "zm_yunjian", 50: "zm_yunxi", 51: "zm_yunxia", 52: "zm_yunyang"}


class KokoroTTSProvider:
    def __init__(self, spec):
        self.spec, self.name, self.model = spec, spec.name, spec.model
        self.settings = spec.local_tts
        self.root = Path(self.settings.model_dir)
        self._engine = None
        self._assets = None
        self.last_status = ProviderStatus(provider=self.name, model=self.model)

    def capabilities(self):
        return ProviderCapabilities(speech=True, speech_units=True, local=True)

    @property
    def cache_key(self):
        if self._assets is None:
            self._assets = verify_model(self.root)
        try:
            runtime = {name: version(name) for name in ("sherpa-onnx", "sherpa-onnx-core", "numpy")}
        except PackageNotFoundError:
            raise BookCastError("缺少本地 TTS 依赖；运行 uv sync --extra tts（Web 用户同时加 --extra web）。") from None
        return fingerprint({"adapter": "kokoro-cpu-v1", "assets": self._assets, "runtime": runtime,
                            "settings": self.settings.model_dump(exclude={"model_dir"})})

    def health_check(self):
        try:
            import sherpa_onnx  # noqa: F401 — also checks native library availability
            import numpy  # noqa: F401
            _ = self.cache_key
            self.last_status = ProviderStatus(provider=self.name, model=self.model, availability="available")
        except (ImportError, OSError, BookCastError):
            self.last_status = ProviderStatus.from_error(self.name, self.model, ProviderError(ErrorKind.INPUT))
        return self.last_status

    def _load(self):
        if self._engine is not None:
            return self._engine
        try:
            import sherpa_onnx as sherpa
        except (ImportError, OSError):
            raise BookCastError("本地 TTS 运行时不可用；运行 uv sync --extra tts 安装完整依赖。") from None
        _ = self.cache_key
        path = lambda name: str(self.root / name)
        config = sherpa.OfflineTtsConfig(model=sherpa.OfflineTtsModelConfig(
            kokoro=sherpa.OfflineTtsKokoroModelConfig(
                model=path("model.onnx"), voices=path("voices.bin"), tokens=path("tokens.txt"),
                data_dir=path("espeak-ng-data"),
                lexicon=",".join(path(n) for n in ("lexicon-us-en.txt", "lexicon-zh.txt"))),
            num_threads=self.settings.threads, provider="cpu", debug=False),
            rule_fsts=",".join(path(n) for n in ("date-zh.fst", "phone-zh.fst", "number-zh.fst")),
            max_num_sentences=1)
        if not config.validate():
            raise ProviderError(ErrorKind.INPUT)
        self._engine = sherpa.OfflineTts(config)
        return self._engine

    def synthesize_unit(self, unit: SpeechUnit, destination: Path) -> SpeechInfo:
        unit = SpeechUnit.model_validate(unit)
        if not any(c.isalnum() for c in unit.text):
            raise ProviderError(ErrorKind.INPUT)
        import numpy as np
        voice = self.settings.host_voice if unit.speaker == "主持人" else self.settings.guest_voice
        try:
            audio = self._load().generate(unit.text, sid=voice, speed=self.settings.speed)
            samples = np.asarray(audio.samples, dtype=np.float32)
            if (audio.sample_rate != 24000 or samples.ndim != 1 or not 0 < samples.size <= 24000 * 180
                    or not np.isfinite(samples).all() or not np.any(samples)):
                raise ProviderError(ErrorKind.SCHEMA)
            pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
            if not np.any(pcm):
                raise ProviderError(ErrorKind.SCHEMA)
            with wave.open(str(destination), "wb") as output:
                output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                output.writeframes(pcm.tobytes())
        except ProviderError as exc:
            self.last_status = ProviderStatus.from_error(self.name, self.model, exc)
            raise
        self.last_status = ProviderStatus(provider=self.name, model=self.model, availability="available")
        return SpeechInfo(audio_kind="speech", voice=VOICES[voice])

    def synthesize(self, script, destination):
        # Compatibility for direct adapter callers. Core uses journaled synthesize_unit.
        from ..speech import speech_units
        with tempfile.TemporaryDirectory(prefix="bookcast-speech-") as directory:
            parts = []
            for index, (_, unit) in enumerate(speech_units(script)):
                part = Path(directory) / f"{index}.wav"
                self.synthesize_unit(unit, part)
                parts.append(part)
            concat_wav(parts, destination)
