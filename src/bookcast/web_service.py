"""Local application submissions. Generation checkpoints remain owned by Core."""

import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .acquisition import choose_edition
from .composition import settings_snapshot
from .errors import BookCastError
from .export import read_valid_export
from .jobs import job_status, manifest_paths
from .models import Model, utc_now
from .provider_config import load_config
from .source_api import EditionCandidate, SearchResult
from .sources import source_registry
from .storage import artifact_path, job_is_locked, job_lock, write_json


class Submission(Model):
    upload_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    search_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    edition: str | None = Field(default=None, max_length=100)
    mode: Literal['summary', 'deep_read', 'two_host'] = 'two_host'
    minutes: int = Field(default=10, ge=1, le=120)

    @model_validator(mode='after')
    def one_source(self):
        if bool(self.upload_id) == bool(self.search_id) or (self.search_id and not self.edition):
            raise ValueError('请选择一个上传文件，或明确选择一个检索候选版本。')
        if self.upload_id and self.edition:
            raise ValueError('上传文件不能同时指定目录版本。')
        return self


class WebJob(Model):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r'^[a-f0-9]{32}$')
    request: Submission
    title: str
    candidate: EditionCandidate | None = None
    settings: dict
    status: Literal['PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED_RETRYABLE', 'FAILED_PERMANENT'] = 'PENDING'
    error: str | None = None
    retry: bool = False
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


def id_path(root: Path, kind: str, identifier: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise BookCastError('无效的本地记录 ID。')
    return artifact_path(root, f'{kind}/{identifier}')


class WebService:
    def __init__(self, data_dir: Path, config: Path | None = None):
        self.root = data_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.children: dict[str, subprocess.Popen] = {}

    def source_provider(self):
        return source_registry().create('gutenberg', cache_dir=artifact_path(self.root, 'imports/.catalog/gutenberg'))

    def search(self, title: str, author: str | None = None, language: str | None = None):
        result = self.source_provider().search(title, author=author, language=language)
        identifier = uuid4().hex
        write_json(id_path(self.root, 'searches', identifier).with_suffix('.json'), result.model_dump(mode='json'))
        return {'search_id': identifier, **result.model_dump(mode='json')}

    def read(self, identifier: str) -> WebJob:
        return WebJob.model_validate_json(id_path(self.root, 'jobs', identifier).joinpath('submission.json').read_text())

    def core_path(self, identifier: str) -> Path | None:
        paths = list(manifest_paths(artifact_path(id_path(self.root, 'jobs', identifier), 'output')))
        if len(paths) > 1:
            raise BookCastError('提交目录出现多个 Core 任务；请通过 CLI 检查。')
        return paths[0] if paths else None

    def status(self, identifier: str) -> dict:
        record = self.read(identifier)
        active = job_is_locked(id_path(self.root, 'jobs', identifier))
        child = self.children.get(identifier)
        active = active or bool(child is not None and child.poll() is None)
        path = self.core_path(identifier)
        core = job_status(str(path)) if path else None
        saved = (core.get('provider_settings') if core else None) or record.settings
        configuration = saved.get('config', {}) if isinstance(saved, dict) else {}
        def selected_names(kind):
            selection = saved.get(f'{kind}_selection', 'auto') if isinstance(saved, dict) else 'auto'
            return configuration.get(f'{kind}_priority', []) if selection == 'auto' else [selection]
        state = core['effective_state'] if core else record.status
        active = active or bool(core and core['active'])
        if not active and state in {'RUNNING', 'PENDING'}:
            state = 'FAILED_RETRYABLE'
        if active:
            state = 'RUNNING'
        integrity = core['integrity'] if core else None
        if not active and integrity == 'damaged':
            state = 'FAILED_RETRYABLE'
        audio = artifact_path(path.parent, 'podcast.mp3') if path else None
        m4b = read_valid_export(path.parent) if path and state == 'SUCCEEDED' and integrity == 'ok' else None
        warnings = list(core['warnings']) if core else []
        if core and core['metadata_seed']:
            warnings.extend(core['metadata_seed'].get('warnings', []))
        quality = None
        audio_info = {}
        if path:
            quality_path = artifact_path(path.parent, 'evaluation/quality.json')
            if quality_path.is_file():
                quality = json.loads(quality_path.read_text())
                warnings.extend(quality.get('warnings', []))
            export_path = artifact_path(path.parent, 'audio/export.json')
            if state == 'SUCCEEDED' and integrity == 'ok' and export_path.is_file():
                audio_info = json.loads(export_path.read_text(encoding='utf-8'))
        return {'id': identifier, 'title': core['progress']['book'] if core else record.title,
                'mode': record.request.mode, 'minutes': record.request.minutes, 'state': state,
                'task_providers': {'llm': selected_names('llm'), 'tts': selected_names('tts')},
                'active': active, 'created_at': record.created_at, 'updated_at': core['updated_at'] if core else record.updated_at,
                'progress': core['progress'] if core else None, 'core_job_id': core['job_id'] if core else None,
                'directory': str(path.parent) if path else str(id_path(self.root, 'jobs', identifier)),
                'error': None if state == 'SUCCEEDED' else (core['error'] if core else None) or record.error,
                'warnings': list(dict.fromkeys(warnings)),
                'audio_kind': audio_info.get('audio_kind', 'unknown'),
                'audio_seconds': audio_info.get('duration_seconds'),
                'audio_url': f'/api/jobs/{identifier}/audio' if state == 'SUCCEEDED' and integrity == 'ok' and audio and audio.is_file() else None,
                'm4b_url': f'/api/jobs/{identifier}/audio.m4b' if m4b else None,
                'can_resume': not active and state == 'FAILED_RETRYABLE',
                'can_retry': not active and state == 'FAILED_PERMANENT'}

    def history(self):
        jobs, errors = [], []
        root = artifact_path(self.root, 'jobs')
        for path in root.glob('*/submission.json'):
            try:
                jobs.append(self.status(path.parent.name))
            except (BookCastError, OSError, ValueError, KeyError):
                errors.append('一项本地任务记录损坏；请通过 CLI 检查，原文件未修改。')
        return {'jobs': sorted(jobs, key=lambda job: job['created_at'], reverse=True), 'errors': errors}

    def submit(self, request: Submission, identifier: str):
        # Idempotency key comes from the client and survives uncertain HTTP responses.
        root = id_path(self.root, 'jobs', identifier)
        with job_lock(self.root):
            if root.exists():
                if self.read(identifier).request != request:
                    raise BookCastError('该提交 ID 已用于其他参数；请新建任务。')
                return self.status(identifier)
            candidate = None
            if request.upload_id:
                upload = json.loads(id_path(self.root, 'uploads', request.upload_id).joinpath('upload.json').read_text())
                title = upload['name']
            else:
                result = SearchResult.model_validate_json(id_path(self.root, 'searches', request.search_id).with_suffix('.json').read_text())
                candidate = choose_edition(result, request.edition)
                title = candidate.identity.title
            settings = load_config(self.config)
            record = WebJob(id=identifier, request=request, title=title, candidate=candidate,
                            settings=settings_snapshot(settings, 'auto', 'auto'))
            write_json(root / 'submission.json', record.model_dump(mode='json'))
            self.launch(identifier)
        return self.status(identifier)

    def dispatch(self, identifier: str, *, retry=False):
        with job_lock(self.root):
            status = self.status(identifier)
            if status['active']:
                raise BookCastError('任务正在执行，请勿重复启动。')
            if status['state'] == 'SUCCEEDED':
                return status
            if status['state'] == 'FAILED_PERMANENT' and not retry:
                raise BookCastError('永久错误需先修复原因，再显式重试。')
            record = self.read(identifier)
            record.retry, record.updated_at = retry, utc_now()
            write_json(id_path(self.root, 'jobs', identifier) / 'submission.json', record.model_dump(mode='json'))
            self.launch(identifier)
        return self.status(identifier)

    def launch(self, identifier: str):
        # A per-submission kernel lock also serializes workers across API restarts.
        self.children = {key: child for key, child in self.children.items() if child.poll() is None}
        active = set(self.children)
        for path in artifact_path(self.root, 'jobs').glob('*/submission.json'):
            if job_is_locked(id_path(self.root, 'jobs', path.parent.name)):
                active.add(path.parent.name)
        if len(active) >= 2:
            raise BookCastError('已有两个后台任务；本次提交已保存，请稍后从书架恢复。')
        with artifact_path(id_path(self.root, 'jobs', identifier), 'worker.log').open('ab') as log:
            self.children[identifier] = subprocess.Popen(
                [sys.executable, '-m', 'bookcast.web_worker', str(self.root), identifier],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
