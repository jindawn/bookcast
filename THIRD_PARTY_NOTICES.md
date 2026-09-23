# Third-party components and licensing review

**Review snapshot:** 2026-09-23. This is a dependency/source inventory, not legal advice or a substitute for reviewing the exact distributed artifacts. BookCast has no root `LICENSE` file and its project license has deliberately not been selected here. Do not infer a license grant for BookCast from the licenses below.

## BookCast licensing decision — blocked

The maintainer must select and add a license for BookCast before publishing a release that claims an open-source license. This phase does not make that choice. A permissive distribution goal has a material compatibility issue: the current required `PyMuPDF` dependency is offered under AGPL-3.0-or-later or a commercial Artifex license. The maintainer should decide whether AGPL terms are acceptable, obtain applicable commercial terms, or authorize evaluating a replacement / separately packaged PDF adapter. Do not state that all of BookCast is Apache-2.0.

## Current direct runtime components

| Component | Current use | License information | Source / review note |
| --- | --- | --- | --- |
| Typer | CLI | MIT | [PyPI project](https://pypi.org/project/typer/) |
| Pydantic | data validation and models | MIT | [PyPI project](https://pypi.org/project/pydantic/) |
| EbookLib | EPUB parsing | MIT | [PyPI project](https://pypi.org/project/EbookLib/) |
| Beautiful Soup 4 | HTML parsing | MIT | [PyPI project](https://pypi.org/project/beautifulsoup4/) |
| PyMuPDF | PDF parsing | AGPL-3.0-or-later **or** commercial license | [Official licensing FAQ](https://pymupdf.readthedocs.io/en/latest/faq/index.html); release compatibility decision required |
| FastAPI / Uvicorn | optional local Web API | MIT / BSD-3-Clause | [FastAPI](https://github.com/fastapi/fastapi), [Uvicorn](https://github.com/encode/uvicorn) |
| sherpa-onnx / sherpa-onnx-core | optional local Kokoro runtime | Apache-2.0 | [Upstream setup metadata](https://github.com/k2-fsa/sherpa-onnx/blob/master/setup.py); pin in `pyproject.toml` |
| qwen-tts | optional experimental runtime | package/model components must be checked at the chosen pinned release | [Official Qwen3-TTS repository](https://github.com/QwenLM/Qwen3-TTS) |
| PyTorch / torchaudio | optional Qwen runtime | BSD-style upstream license; bundled binaries and transitive runtime components require artifact-level review | [PyTorch license](https://github.com/pytorch/pytorch/blob/main/LICENSE) |

The Python lockfile and optional extras are not by themselves a complete redistribution notice. Re-run a license inventory against the exact platform wheels and transitive dependencies before making binary or bundled releases.

## Models, speech data, and external executables

| Artifact | Current use | Known license / remaining check |
| --- | --- | --- |
| Kokoro-82M / Kokoro ONNX weights | optional local Chinese TTS | Model pages identify Apache-2.0; [Kokoro model card](https://huggingface.co/hexgrad/Kokoro-82M), [sherpa-onnx Kokoro docs](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/kokoro.html). Verify the exact downloaded archive and hash when redistributing it. |
| `espeak-ng-data` within the Kokoro model bundle | phoneme / language data used by local speech runtime | The upstream eSpeak NG project is GPL-3.0-or-later ([upstream](https://github.com/espeak-ng/espeak-ng)). The exact license and notice for the bundled data files were not established from the model archive; **redistribution is blocked until audited**. |
| Qwen3-TTS weights | optional experimental local TTS | Official repository and model cards identify Apache-2.0 for model code/weights ([repository](https://github.com/QwenLM/Qwen3-TTS), [official model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice)); check each exact model revision and any voice/reference assets. |
| FFmpeg / ffprobe | external executable invoked for audio encoding/probing; not included in this repository | FFmpeg's default code is LGPL-2.1-or-later, while GPL-enabled builds and particular codecs change the obligations. See [FFmpeg legal information](https://ffmpeg.org/legal.html). BookCast does not certify a user's installed build; record build configuration and notices if distributing one. |

Model-weight licensing does not establish the license of runtime code, bundled phoneme data, generated voices, or system packages.

## Web dependency lock snapshot

The committed `web/package-lock.json` currently declares these license identifiers across its package entries: MIT, Apache-2.0, LGPL-3.0-or-later, ISC, CC-BY-4.0, BSD-3-Clause, and 0BSD; one entry has no license field. The lock includes 10 entries marked LGPL-3.0-or-later. This is a metadata snapshot, not a complete source/binary notice audit. The exact production dependency tree, native packages, and notices must be reviewed for the target platform before redistribution.

## Release actions

- [ ] Maintainer selects BookCast license; add root `LICENSE` and update package metadata consistently.
- [ ] Decide the PyMuPDF AGPL/commercial/replacement path before a release distribution.
- [ ] Determine the license and required notice for every `espeak-ng-data` file in the Kokoro archive.
- [ ] Archive exact model revisions, hashes, license texts, and notices for each model offered by setup.
- [ ] Record the exact FFmpeg build, enabled options/codecs, and corresponding notices for each bundled installer, if any.
- [ ] Re-run Python and npm transitive license inventories against release artifacts and include required notices.
- [ ] Scan the release source and artifacts for credentials and unintended user books/audio.
