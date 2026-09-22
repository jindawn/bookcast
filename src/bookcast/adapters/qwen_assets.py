"""Pinned official CustomVoice snapshot; no model downloads during generation."""

from pathlib import Path

from ..errors import BookCastError
from ..storage import sha256_file

MODEL = "Qwen3-TTS-12Hz-1.7B-CustomVoice"
REVISION = "0c0e3051f131929182e2c023b9537f8b1c68adfe"
# Official HF model card declares Apache-2.0. Preserve README with the weights.
FILES = {
    "README.md": "64f65e809b51cc0c35f393fbbfcc2d735c0cb3fdbbc6f3fdc4a5e6ce55e9d088",
    "config.json": "17a07f527a1c25ea30b4e023a184482a23d3e279d697b1dc81b1bde498d29cf9",
    "generation_config.json": "f1b90b4513f3b34c62851049e2492d7b4c5940daf1276f89c82b8ef04127f3aa",
    "merges.txt": "599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3",
    "model.safetensors": "38b1d5971bdbd982b561cccec982669a53b0537c3cf5e9bd4778ed07bb2f5137",
    "preprocessor_config.json": "efdde1022ea9d76928bf7a9cd53139138f5ba2e466e837f08f6105ab1af1c119",
    "speech_tokenizer/config.json": "ee65bb901c876664ab8707c487157aa1a6ee57c65969b28fb5ec9dc211e68167",
    "speech_tokenizer/configuration.json": "6bc26d64eb5024b4d1dab5a52371958b429256d6c9d59787f1f5294a54e0cebd",
    "speech_tokenizer/model.safetensors": "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258",
    "speech_tokenizer/preprocessor_config.json": "fcb3805e597e786d4067706e602f6688524640f8d3396790e2e09b5942fcbdfb",
    "tokenizer_config.json": "dc3c31c3bdaedd5016382bb3cbe07323026775ad51f5a4fb564505992ae4a670",
    "vocab.json": "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910",
}


def verify_assets(root: Path) -> dict[str, str]:
    for name, expected in FILES.items():
        path = root / name
        try:
            if (not path.is_file() or path.is_symlink()
                    or (root / "speech_tokenizer").is_symlink()
                    or sha256_file(path) != expected):
                raise ValueError("invalid asset")
        except (OSError, ValueError):
            raise BookCastError("Qwen本地模型缺失或校验失败；请按TTS指南准备固定官方快照，generate不会下载模型。") from None
    return dict(FILES)
