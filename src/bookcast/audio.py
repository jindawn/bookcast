"""WAV contract checking and FFmpeg MP3 merge; never invokes a shell."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import wave

from .errors import BookCastError


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def concat_wav(parts: list[Path], destination: Path, pause_seconds: float = 0.18) -> None:
    if not parts:
        raise BookCastError("没有可拼接的语音单元。")
    with wave.open(str(destination), "wb") as output:
        output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        for index, part in enumerate(parts):
            validate_wav(part)
            if index:
                output.writeframes(b"\0\0" * round(24000 * pause_seconds))
            with wave.open(str(part), "rb") as source:
                while frames := source.readframes(24000):
                    output.writeframes(frames)


def validate_wav(path: Path) -> None:
    try:
        with wave.open(str(path), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 24_000):
                raise BookCastError("TTS 必须输出 24 kHz、单声道、16 位 PCM WAV。")
            frames = audio.getnframes()
            if frames == 0:
                raise BookCastError("TTS 输出了空音频。")
            remaining = frames
            while remaining:
                count = min(remaining, 24_000)
                if len(audio.readframes(count)) != count * 2:
                    raise BookCastError("TTS WAV 已截断。")
                remaining -= count
    except (wave.Error, EOFError) as exc:
        raise BookCastError(f"TTS 没有输出有效 WAV：{exc}") from exc


def merge_audio(root: Path, chapter_ids: list[str], destination: Path) -> None:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise BookCastError("找不到 FFmpeg。安装并加入 PATH 后，用相同输入加 --resume 继续。")
    # All names are internally generated numeric chapter IDs. The concat file
    # lives beside audio/, so paths remain safe even when root has spaces/quotes.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", prefix=".concat-", dir=root, encoding="utf-8") as listing:
        for chapter_id in chapter_ids:
            validate_wav(root / "audio" / f"{chapter_id}.wav")
            listing.write(f"file 'audio/{chapter_id}.wav'\n")
        listing.flush()
        result = subprocess.run([
            executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "concat", "-safe", "1", "-i", listing.name,
            "-vn", "-map_metadata", "-1", "-c:a", "libmp3lame", "-b:a", "96k",
            "-f", "mp3", str(destination),
        ], capture_output=True, text=True, timeout=600, cwd=root)
    if result.returncode or not destination.is_file() or destination.stat().st_size == 0:
        raise BookCastError(f"FFmpeg 合并失败：{result.stderr.strip()[-1500:]}")
