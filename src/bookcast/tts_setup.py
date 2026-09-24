"""Explicit, pinned model installation; never called by generate or a provider."""

import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import time
from urllib import request
from urllib.parse import urlsplit

from .errors import BookCastError
from .storage import fingerprint, sha256_file, write_json, job_lock

MODEL = "kokoro-multi-lang-v1_0"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/{MODEL}.tar.bz2"
MODEL_SHA256 = "c5f7e2d2caf082bc1d20fb70334a61d99d20b484500aad32e7cf84c128ea3298"
MODEL_BYTES = 349906910
# SHA-256 of the canonical JSON file-hash map extracted from the pinned archive.
# The installation receipt is mutable local data, so its claimed archive digest
# alone cannot authenticate a model directory supplied by a user or another tool.
OFFICIAL_ASSET_FINGERPRINT = "b42e0a65e66a80dcd574c123530073f8f4dd64a153562b5bab7628d4ded1df1e"
MAX_UNPACKED = 1024 * 1024 * 1024
REQUIRED = {"model.onnx", "voices.bin", "tokens.txt", "lexicon-us-en.txt", "lexicon-zh.txt",
            "date-zh.fst", "number-zh.fst", "phone-zh.fst", "LICENSE"}
RECEIPT = "bookcast-model.json"


class ModelRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        if (url.scheme != "https" or url.hostname not in {"github.com", "release-assets.githubusercontent.com"}
                or url.username or url.password or url.port not in {None, 443}):
            raise BookCastError("模型下载重定向不在官方发布域名内。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_model(destination: Path) -> None:
    start, total = time.monotonic(), 0
    opener = request.build_opener(ModelRedirect())
    try:
        with opener.open(MODEL_URL, timeout=30) as response, destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MODEL_BYTES or time.monotonic() - start > 900:
                    raise BookCastError("模型下载超过大小或时间限制。")
                output.write(chunk)
    except (OSError, ValueError):
        raise BookCastError("模型下载失败；检查网络后重试 tts setup，或用 --archive 提供官方模型包。") from None


def unpack_model(archive: Path, staging: Path) -> Path:
    """Extract only bounded regular files/directories; ignore all archive permissions."""
    seen, total = set(), 0
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            for member in tar:
                name = PurePosixPath(member.name)
                if (name.is_absolute() or ".." in name.parts or "\\" in member.name
                        or not name.parts or name.parts[0] != MODEL or name in seen
                        or not (member.isfile() or member.isdir())):
                    raise BookCastError("模型包包含不安全或重复的路径/文件类型。")
                seen.add(name)
                total += member.size
                if len(seen) > 10000 or member.size < 0 or total > MAX_UNPACKED:
                    raise BookCastError("模型包展开超过限制。")
                target = staging.joinpath(*name.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
    except (tarfile.TarError, OSError, EOFError):
        raise BookCastError("模型包损坏或无法安全展开。") from None
    root = staging / MODEL
    if not all((root / name).is_file() for name in REQUIRED) or not (root / "espeak-ng-data").is_dir():
        raise BookCastError("模型包缺少必需文件。")
    return root


def verify_model(root: Path) -> str:
    """Hash actual weights and dictionaries once per provider instance/run."""
    try:
        if root.is_symlink():
            raise ValueError("linked model directory")
        receipt = json.loads((root / RECEIPT).read_text(encoding="utf-8"))
        files = receipt["files"]
        if (receipt["model"] != MODEL or receipt["archive_sha256"] != MODEL_SHA256
                or not isinstance(files, dict) or not REQUIRED.issubset(files) or len(files) > 10000
                or fingerprint(files) != OFFICIAL_ASSET_FINGERPRINT):
            raise ValueError("invalid receipt")
        actual = {}
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ValueError("linked asset")
            if path.is_file() and path != root / RECEIPT:
                actual[path.relative_to(root).as_posix()] = sha256_file(path)
        if actual != files or not any(p.startswith("espeak-ng-data/") for p in files):
            raise ValueError("damaged assets")
        return fingerprint(actual)
    except (OSError, ValueError, KeyError, TypeError):
        raise BookCastError("本地 TTS 模型缺失或校验失败；运行 bookcast tts setup 安装到新的模型目录。") from None


def install_model(parent: Path, archive: Path | None = None) -> Path:
    parent = parent.expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    with job_lock(parent):
        target = parent / MODEL
        if target.exists():
            verify_model(target)
            return target
        with tempfile.TemporaryDirectory(prefix=".bookcast-model-", dir=parent) as temporary:
            stage = Path(temporary)
            package = archive or stage / "model.tar.bz2"
            if archive is None:
                download_model(package)
            if package.stat().st_size != MODEL_BYTES or sha256_file(package) != MODEL_SHA256:
                raise BookCastError("模型包大小或 SHA-256 不匹配官方发布；未安装。")
            root = unpack_model(package, stage / "unpack")
            files = {p.relative_to(root).as_posix(): sha256_file(p) for p in root.rglob("*") if p.is_file()}
            write_json(root / RECEIPT, {"model": MODEL, "url": MODEL_URL, "archive_sha256": MODEL_SHA256,
                                      "files": files})
            root.rename(target)
        return target


def write_local_config(destination: Path, model_dir: Path) -> None:
    # Exclusive creation: never overwrite a user's LLM configuration or credentials.
    config = f'''schema_version = 1
llm_priority = ["mock"]
tts_priority = ["kokoro"]

[[providers]]
name = "mock"
kind = "llm"
type = "mock"
model = "mock-llm-v1"

[[providers]]
name = "kokoro"
kind = "tts"
type = "kokoro-local"
model = "{MODEL}"
[providers.local_tts]
model_dir = {json.dumps(str(model_dir.resolve()), ensure_ascii=False)}
host_voice = 45
guest_voice = 50
speed = 1.0
threads = 2
'''
    try:
        with destination.open("x", encoding="utf-8") as output:
            output.write(config)
    except FileExistsError:
        raise BookCastError("配置文件已存在，未覆盖；选择新的 --config-output 或手工合并 TTS 配置。") from None
