"""Detached local worker: all parsing, sourcing, AI and audio work delegates to Core."""

import json
from pathlib import Path
import sys

from .acquisition import Acquirer
from .composition import configured_pipeline, resume_pipeline
from .errors import BookCastError
from .models import BookMetadata, utc_now
from .pipeline import load_manifest
from .provider_api import classify_error
from .provider_config import ProvidersConfig
from .storage import artifact_path, job_lock, write_json
from .web_service import WebService, id_path


def run_worker(data_dir: Path, identifier: str):
    service = WebService(data_dir)
    root = id_path(service.root, 'jobs', identifier)
    with job_lock(root):
        record = service.read(identifier)
        record.status, record.error = 'RUNNING', None

        def save():
            record.updated_at = utc_now()
            write_json(root / 'submission.json', record.model_dump(mode='json'))

        save()
        try:
            existing = service.core_path(identifier)
            if existing:
                pipeline = resume_pipeline(load_manifest(existing), existing.parent, None, None, None)
                pipeline.resume_job(existing.parent, retry=record.retry)
            else:
                metadata = None
                if record.request.upload_id:
                    upload_root = id_path(service.root, 'uploads', record.request.upload_id)
                    upload = json.loads((upload_root / 'upload.json').read_text())
                    source = artifact_path(upload_root, upload['file'])
                else:
                    offers = service.source_provider().sources(record.candidate)
                    if len(offers) != 1:
                        raise BookCastError('所选版本没有唯一可用来源，请更换版本或上传本地文件。')
                    acquired = Acquirer(service.root / 'imports').acquire(record.candidate, offers[0])
                    metadata = BookMetadata.model_validate_json((acquired / 'metadata.json').read_text())
                    source = acquired / 'source' / f'input.{offers[0].format}'
                settings = ProvidersConfig.model_validate(record.settings['config'])
                pipeline = configured_pipeline(settings, root / 'output')
                pipeline.generate(source, metadata_seed=metadata, mode=record.request.mode, minutes=record.request.minutes)
            record.status = 'SUCCEEDED'
        except (Exception, KeyboardInterrupt) as exc:
            error = classify_error(exc)
            record.status = 'FAILED_RETRYABLE' if error.retryable else 'FAILED_PERMANENT'
            record.error = str(exc) if isinstance(exc, BookCastError) else '本地任务失败；请检查输入、Provider 配置与 FFmpeg 后重试。'
        finally:
            save()


if __name__ == '__main__':
    try:
        run_worker(Path(sys.argv[1]), sys.argv[2])
    except BookCastError:
        # Another worker owns the submission; do not overwrite its status.
        sys.exit(1)
