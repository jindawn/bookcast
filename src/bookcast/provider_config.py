"""Strict TOML configuration; only names of environment variables, never keys."""

import ipaddress
import re
from pathlib import Path
import tomllib
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .errors import BookCastError
from .models import Model
from .generation import GenerationConfig, PolicyName
from .provider_api import ErrorKind, FAILOVER_ERRORS


def is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


class LocalTTSConfig(Model):
    model_dir: str = Field(min_length=1)
    host_voice: int = Field(default=45, ge=45, le=52)
    guest_voice: int = Field(default=50, ge=45, le=52)
    speed: float = Field(default=1.0, ge=0.5, le=2.0, allow_inf_nan=False)
    threads: int = Field(default=2, ge=1, le=16)


class QwenTTSConfig(Model):
    model_dir: str = Field(min_length=1)
    experimental: bool = Field(strict=True)
    host_voice: Literal["Vivian", "Uncle_Fu"] = "Vivian"
    guest_voice: Literal["Vivian", "Uncle_Fu"] = "Uncle_Fu"
    style_instruction: str = Field(default="自然清晰地讲述，像播客主持人在交谈。", min_length=1, max_length=256)
    threads: int = Field(default=4, ge=1, le=8)
    seed: int = Field(default=42, ge=0, le=2147483647)

    @model_validator(mode="after")
    def explicit_experiment(self):
        if not self.experimental or self.host_voice == self.guest_voice:
            raise ValueError("Qwen requires explicit experimental opt-in and two distinct voices")
        return self


class CloudTTSConfig(Model):
    # Required acknowledgement; a key or an App subscription alone never opts in.
    send_text_to_cloud: bool = Field(strict=True)
    data_tier: Literal["free", "paid", "unknown"] = "unknown"
    host_voice: str = Field(default="Kore", pattern=r"^[A-Za-z]{1,32}$")
    guest_voice: str = Field(default="Puck", pattern=r"^[A-Za-z]{1,32}$")
    style_instruction: str | None = Field(default=None, max_length=512)
    host_style: str | None = Field(default=None, max_length=512)
    guest_style: str | None = Field(default=None, max_length=512)
    mode: Literal["conversational", "standard", "unknown"] = "unknown"

    @model_validator(mode="after")
    def distinct_voices(self):
        if not self.send_text_to_cloud:
            raise ValueError('cloud text transmission must be explicitly enabled')
        if self.host_voice == self.guest_voice:
            raise ValueError("two hosts require different voices")
        return self


class ProviderSpec(Model):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    kind: Literal["llm", "tts"]
    type: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=128)
    base_url: str | None = None
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    timeout_seconds: float = Field(default=30, ge=0.1, le=120, allow_inf_nan=False)
    local_tts: LocalTTSConfig | QwenTTSConfig | None = None
    cloud_tts: CloudTTSConfig | None = None
    generation: GenerationConfig | None = None
    reasoning_policy: PolicyName | None = None

    @model_validator(mode="after")
    def validate_endpoint(self):
        if self.type == "gemini-tts":
            if (self.kind != 'tts' or self.cloud_tts is None or not self.api_key_env
                    or self.base_url is not None or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', self.model)):
                raise ValueError('Gemini TTS requires explicit cloud consent, environment key and model; endpoint is official only')
        elif self.cloud_tts is not None:
            raise ValueError('cloud_tts is only valid for gemini-tts')
        if (self.generation is not None or self.reasoning_policy is not None) and (
                self.kind != 'llm' or self.type not in {'openai-compatible', 'local'}):
            raise ValueError('generation options require a compatible LLM adapter')
        if self.type == "kokoro-local":
            if (self.kind != "tts" or self.model != "kokoro-multi-lang-v1_0" or not isinstance(self.local_tts, LocalTTSConfig)
                    or self.base_url or self.api_key_env):
                raise ValueError("kokoro-local requires local TTS settings and the supported model")
        elif self.type == "qwen-local":
            if (self.kind != "tts" or self.model != "Qwen3-TTS-12Hz-1.7B-CustomVoice"
                    or not isinstance(self.local_tts, QwenTTSConfig) or self.base_url is not None
                    or self.api_key_env is not None):
                raise ValueError("qwen-local requires experimental local settings and the fixed supported model")
        elif self.local_tts is not None:
            raise ValueError("local_tts requires a supported local TTS adapter")
        if self.type in {"openai-compatible", "local"} and not self.base_url:
            raise ValueError("base_url required")
        if self.base_url:
            url = urlsplit(self.base_url)
            if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                    or url.query or url.fragment or any(c.isspace() for c in self.base_url)):
                raise ValueError("unsafe endpoint")
            _ = url.port
            if url.scheme == "http" and not is_loopback(url.hostname):
                raise ValueError("remote endpoint requires HTTPS")
            if self.type == "local" and not is_loopback(url.hostname):
                raise ValueError("local endpoint must be loopback")
        return self


class ProvidersConfig(Model):
    schema_version: Literal[1] = 1
    providers: list[ProviderSpec] = Field(min_length=1)
    llm_priority: list[str] = Field(min_length=1)
    tts_priority: list[str] = Field(min_length=1)
    failover_on: list[ErrorKind] = Field(default_factory=lambda: sorted(FAILOVER_ERRORS))

    @model_validator(mode="after")
    def validate_chains(self):
        by_name = {spec.name: spec for spec in self.providers}
        if len(by_name) != len(self.providers):
            raise ValueError("duplicate names")
        if not set(self.failover_on).issubset(FAILOVER_ERRORS):
            raise ValueError("permanent errors cannot trigger failover")
        for kind, chain in (("llm", self.llm_priority), ("tts", self.tts_priority)):
            if len(chain) != len(set(chain)):
                raise ValueError("duplicate priority entry")
            for name in chain:
                if name not in by_name or by_name[name].kind != kind:
                    raise ValueError("unknown provider or wrong kind")
        return self


def load_config(path: Path | None = None) -> ProvidersConfig:
    target = path or Path("bookcast.toml")
    if path is None and not target.exists():
        return ProvidersConfig(providers=[
            ProviderSpec(name="mock", kind="llm", type="mock", model="mock-llm-v1"),
            ProviderSpec(name="mock-tts", kind="tts", type="mock", model="mock-tones-v1"),
        ], llm_priority=["mock"], tts_priority=["mock-tts"])
    try:
        config = ProvidersConfig.model_validate(tomllib.loads(target.read_text(encoding="utf-8")))
        for spec in config.providers:
            if spec.local_tts:
                directory = Path(spec.local_tts.model_dir).expanduser()
                if not directory.is_absolute():
                    directory = target.resolve().parent / directory
                spec.local_tts.model_dir = str(directory.resolve())
        return config
    except (OSError, ValueError) as exc:
        # Pydantic errors may include field values, including an accidentally
        # pasted key. Do not expose those values in logs or CLI error messages.
        raise BookCastError("Provider 配置无效：检查 TOML 字段、优先级、端点和环境变量名；禁止直接存放密钥。") from None
