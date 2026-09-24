"""Optional on-device Apple Vision OCR; no model download or network service."""

import hashlib
import json
import os
import shutil
import subprocess
import sys

from ..document_extraction import OCRText
from ..errors import BookCastError


_SWIFT = r'''
import Foundation
import Vision

let image = FileHandle.standardInput.readDataToEndOfFile()
if image.isEmpty || image.count > 16 * 1024 * 1024 { exit(2) }
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = true
do {
    try VNImageRequestHandler(data: image, options: [:]).perform([request])
    let observations = request.results ?? []
    if observations.count > 2048 { exit(3) }
    var lines: [[String: Any]] = []
    for observation in observations {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let value = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
        if value.isEmpty || value.count > 4096 { continue }
        let box = observation.boundingBox
        lines.append([
            "text": value,
            "confidence": Double(candidate.confidence),
            "region": [Double(box.minX), Double(1 - box.maxY),
                       Double(box.maxX), Double(1 - box.minY)]
        ])
    }
    let output = try JSONSerialization.data(withJSONObject: lines)
    FileHandle.standardOutput.write(output)
} catch {
    exit(4)
}
'''


class AppleVisionOCRProvider:
    name = "apple-vision"
    cache_key = "apple-vision-v1:" + hashlib.sha256(_SWIFT.encode()).hexdigest()

    def __init__(self):
        self.swift = shutil.which("swift") if sys.platform == "darwin" else None
        if not self.swift:
            raise BookCastError("Apple Vision OCR 仅可在安装 Swift 的 macOS 上显式启用。")

    def recognize(self, png: bytes) -> list[OCRText]:
        if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > 16 * 1024 * 1024:
            raise BookCastError("OCR 仅接收不超过 16 MiB 的 PNG 图像。")
        # Do not pass API keys from the parent environment into the local OCR process.
        environment = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG") if key in os.environ}
        try:
            result = subprocess.run([self.swift, "-e", _SWIFT], input=png, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, env=environment, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BookCastError("本地 Apple Vision OCR 无法启动或超时。") from exc
        if result.returncode != 0 or len(result.stdout) > 2 * 1024 * 1024:
            raise BookCastError("本地 Apple Vision OCR 失败或输出超出上限。")
        try:
            raw = json.loads(result.stdout)
            if not isinstance(raw, list) or len(raw) > 2048:
                raise ValueError("OCR 输出数量无效")
            lines = [OCRText.model_validate(item) for item in raw]
        except (ValueError, UnicodeError, TypeError) as exc:
            raise BookCastError("本地 Apple Vision OCR 返回无效结果。") from exc
        return sorted(lines, key=lambda line: (line.region[1], line.region[0]))
