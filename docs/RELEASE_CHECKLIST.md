# BookCast release checklist

Statuses are deliberately limited to **verified**, **manual**, **optional**, and **blocked**. “Verified” means there is a reproducible test or checked-in evidence for the stated scope; it does not imply every OS, book, or provider is validated. Live services and large models stay outside ordinary CI.

| Area | Status | Evidence / action |
| --- | --- | --- |
| Fresh clone → Python install → doctor → Mock generation | verified | A temporary `git clone --no-local` created a new venv and installed the lockfile, then passed setup/doctor/Mock audio smoke and project validation. `.github/workflows/ci.yml` repeats this in CI on Ubuntu/Python 3.12. |
| Web dependency install, typecheck, build | verified | Web CI job runs `npm ci`, `npm run typecheck`, and `npm run build`; browser E2E is not part of the PR path. |
| Local TXT import, MP3, M4B and M4B chapter markers | verified | CI smoke generates a two-section temporary TXT, probes MP3/M4B using ffprobe, and checks M4B chapters. EPUB/PDF parsing also has offline integration tests. |
| Gutenberg source acquisition and rights validation | manual | Source adapter tests are offline; perform a real acquisition only where Project Gutenberg's US public-domain status is applicable. Full-book opt-in test uses `gutenberg:3300`; confirm local jurisdiction before use. |
| Full-book Mock pipeline, manifest, disk growth, completed resume, M4B chapters | verified | Isolated 2,468,951-byte Gutenberg 3300 source copy parsed to 67 chapters; 1,507 durable steps completed, 20 podcast-segment M4B chapter markers were read by ffprobe, and completed resume added 0 bytes. Mock providers only; no network AI calls. |
| Repeat full-book acquisition from a fresh directory using Gutenberg DNS | manual | `BOOKCAST_RUN_RELEASE_LARGE_BOOK=1 uv run pytest -m large_model tests/test_release_large_book.py -v` performs official-source acquisition plus the full Mock pipeline. This host's DNS/proxy returned a non-public address, so the downloader correctly rejected it before fetching; re-run from a network with public DNS. The source-acquisition test must remain independent of ignored developer imports/output. |
| Real LLM (DeepSeek/OpenAI-compatible) | optional | Explicit project/key configuration and manual live test only; may send book text and incur API charges. Never a default CI check. |
| Kokoro local TTS and model download | optional | Historical real-device acceptance is recorded in `docs/TTS.md` and phase handoffs. Model download/inference is excluded from CI; verify target hardware and current model archive before release. |
| Gemini TTS | optional | User must explicitly opt into sending script text to Google's Developer API. Manual A/B record is `docs/PHASE10_TTS_AB.md`; credentials, tier, and current account eligibility are environment-dependent. |
| Qwen local TTS | optional | Experimental, large local model, platform/resource-dependent. Phase 16 verifies the 12 fixed model files and rejects extra model inputs; no PR download/inference or quality promotion. See `docs/TTS_PROVIDER_EVALUATION.md`. |
| Qwen extra dependency advisories and redistribution | blocked | `uv audit --locked` finds 14 OSV records (including duplicate aliases) in `accelerate`, `setuptools`, `torch`, `transformers` from the optional Qwen extra. BookCast's fixed local model loading does not exercise the cited arbitrary Hub/Trainer/JIT/save paths; nevertheless do not bundle the experimental extra as a V1 default until compatible upstream versions and target-platform behavior are reviewed. `uv audit --locked --no-extra qwen` finds 0 in 36 packages. |
| Resume and provider-call cache | verified | Offline integration tests cover completed artifacts and recovery. Large-book exact no-growth resume is additionally checked by the opt-in full-book test. |
| SIGKILL recovery | verified | Automated subprocess kill/recovery tests cover job and TTS temporary artifacts; no external provider required. |
| Web browser interaction / Playwright | optional | `npm run test:e2e` can be run locally with the repository's browser test setup; not required for each PR owing to runtime/setup cost. |
| BookCast project license / PDF dependency compatibility | blocked | No root `LICENSE`; maintainer decision is required. PyMuPDF offers AGPL-3.0-or-later or commercial terms. See [third-party review](../THIRD_PARTY_NOTICES.md). |
| Kokoro archive asset redistribution notices | blocked | Exact `espeak-ng-data` license/notice was not confirmed for the packaged archive; do not redistribute the archive until audited. |
| FFmpeg redistribution | optional | BookCast invokes a system FFmpeg/ffprobe; it does not currently bundle those binaries. Any installer must audit its precise build flags/codecs and notices. |
| Release secret scan | verified | Phase 16 scanned 435 Git history blobs, 119 tracked files and 723 local task text artifacts without finding active credential values in Git, manifest or logs. A stale ignored `.next/cache` hit was cleared; a fresh fake-key build and 165 current Web build files had no key hits, including `web/out`. Ignored local private material remains untouched. Repeat on final package/build outputs before publication. |
| GitHub Actions execution on canonical GitHub repo | manual | The [first pushed baseline run](https://github.com/jindawn/bookcast/actions/runs/35830514918) at `eb3bc6c` succeeded for Python Core, Web and Static validation. Recheck Actions after pushing the Phase 16 audit commit; the earlier run does not verify this new code. |
| Phase 16 Critical/High audit fixes | verified | 0 Critical; 3 High input/model-integrity findings fixed with direct-generate/old-cache and forged/extra-asset regressions. Full evidence and remaining Medium/Low backlog: [Phase 16 audit](PHASE16_AUDIT.md). |
| Locked Web dependency advisories | verified | `npm audit --registry=https://registry.npmjs.org/ --json` returned 0 for the full web lock, including dev dependencies. The host's default npm mirror audit endpoint returned 404 and was not treated as a clean result. |
| Version | verified | Python/Web package version remains `0.1.0`; the project stays pre-1.0 while license and distribution blockers remain. |

## Commands

Default offline suite (the project-level pytest default is filtered to these tiers):

```sh
uv sync --locked --extra dev --extra web
uv run pytest -q
uv run python -m compileall -q src tests scripts
python3 scripts/validate_project.py
npm --prefix web ci
npm --prefix web run typecheck
npm --prefix web run build
```

Explicit full-book network acquisition plus Mock pipeline:

```sh
BOOKCAST_RUN_RELEASE_LARGE_BOOK=1 uv run pytest -m large_model tests/test_release_large_book.py -v
```

Paid live services require their existing dedicated environment opt-ins and valid credentials. They are a separate manual release action and are not enabled by `BOOKCAST_RUN_RELEASE_LIVE=1` alone unless their provider-specific tests and secrets are intentionally configured. Never put provider keys into GitHub logs or public repository variables.

## Version and release gate

Keep `0.1.0` and pre-1.0 status. Do not announce a 1.0 or publish a redistributable bundle until the maintainer resolves the project license, PyMuPDF compatibility, model archive data notices, and target-specific FFmpeg notices. A passing CI run demonstrates the checked CI scope, not commercial permission or a human listening-quality approval.
