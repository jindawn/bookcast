"""Experimental native MPS TTS. Core owns units, persistence, recovery and merge."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tempfile
import wave

from ..audio import concat_wav
from ..errors import BookCastError
from ..provider_api import ErrorKind, ProviderCapabilities, ProviderError, ProviderStatus, SpeechInfo, SpeechUnit
from ..storage import fingerprint
from .qwen_assets import REVISION, verify_assets


class QwenTTSProvider:
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
            self._assets = verify_assets(self.root)
        try:
            runtime = {name: version(name) for name in ("qwen-tts", "torch", "torchaudio", "transformers", "numpy")}
        except PackageNotFoundError:
            raise BookCastError("缺少Qwen实验运行时；请按TTS指南在独立环境安装qwen extra。") from None
        return fingerprint({"adapter": "qwen-mps-units-v1", "revision": REVISION, "assets": self._assets,
                            "runtime": runtime, "audio_contract": "pcm-s16le-24000-mono-v1",
                            "device": "mps", "dtype": "bfloat16", "attention": "sdpa", "max_new_tokens": 2048,
                            "settings": self.settings.model_dump(exclude={"model_dir"})})

    def health_check(self):
        try:
            _ = self.cache_key
            import torch
            if not torch.backends.mps.is_available():
                raise BookCastError("MPS unavailable")
            self.last_status = ProviderStatus(provider=self.name, model=self.model, availability="available")
        except (ImportError, OSError, RuntimeError, BookCastError):
            self.last_status = ProviderStatus.from_error(self.name, self.model, ProviderError(ErrorKind.INPUT))
        return self.last_status

    def _load(self):
        if self._engine is not None:
            return self._engine
        _ = self.cache_key
        try:
            import torch
            from qwen_tts import Qwen3TTSModel
            if not torch.backends.mps.is_available():
                raise ProviderError(ErrorKind.INPUT)
            torch.set_num_threads(self.settings.threads)
            torch.mps.set_per_process_memory_fraction(0.5)
            self._engine = Qwen3TTSModel.from_pretrained(
                str(self.root.resolve()), device_map="mps", dtype=torch.bfloat16,
                attn_implementation="sdpa", local_files_only=True,
                trust_remote_code=False, use_safetensors=True,
            )
            return self._engine
        except (ImportError, OSError, RuntimeError, ValueError):
            # Missing runtime/device or local resource failure needs intervention.
            # Never persist the upstream exception or silently substitute Mock.
            raise ProviderError(ErrorKind.INPUT) from None

    def synthesize_unit(self, unit: SpeechUnit, destination: Path) -> SpeechInfo:
        unit = SpeechUnit.model_validate(unit)
        if not any(c.isalnum() for c in unit.text):
            raise ProviderError(ErrorKind.INPUT)
        voice = self.settings.host_voice if unit.speaker == "主持人" else self.settings.guest_voice
        try:
            import numpy as np
            import torch
            engine = self._load()
            torch.manual_seed(self.settings.seed)
            wavs, rate = engine.generate_custom_voice(
                text=unit.text, language="Chinese", speaker=voice,
                instruct=self.settings.style_instruction, non_streaming_mode=True, max_new_tokens=2048,
            )
            if not isinstance(wavs, (list, tuple)) or len(wavs) != 1:
                raise ProviderError(ErrorKind.SCHEMA)
            try:
                samples = np.asarray(wavs[0], dtype=np.float32)
            except (TypeError, ValueError):
                raise ProviderError(ErrorKind.SCHEMA) from None
            if (rate != 24000 or samples.ndim != 1 or not 0 < samples.size <= 24000 * 180
                    or not np.isfinite(samples).all() or not np.any(samples)):
                raise ProviderError(ErrorKind.SCHEMA)
            pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
            if not np.any(pcm):
                raise ProviderError(ErrorKind.SCHEMA)
            with wave.open(str(destination), "wb") as output:
                output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                output.writeframes(pcm.tobytes())
            torch.mps.empty_cache()
        except ProviderError as error:
            self.last_status = ProviderStatus.from_error(self.name, self.model, error)
            raise
        except (ImportError, OSError, RuntimeError, ValueError):
            error = ProviderError(ErrorKind.INPUT)
            self.last_status = ProviderStatus.from_error(self.name, self.model, error)
            raise error from None
        self.last_status = ProviderStatus(provider=self.name, model=self.model, availability="available")
        return SpeechInfo(audio_kind="speech", voice=voice)

    def synthesize(self, script, destination):
        from ..speech import speech_units
        with tempfile.TemporaryDirectory(prefix="bookcast-qwen-") as directory:
            parts = []
            for index, (_, unit) in enumerate(speech_units(script)):
                part = Path(directory) / f"{index}.wav"
                self.synthesize_unit(unit, part)
                parts.append(part)
            concat_wav(parts, destination)
