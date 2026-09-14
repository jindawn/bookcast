"""Read-only discovery and projections of authoritative per-job manifests."""
import os
from pathlib import Path
import re

from .errors import BookCastError
from .models import BookMetadata, TaskState
from .storage import artifact_path, job_is_locked


def manifest_paths(output_dir: Path):
    root = output_dir.resolve()
    if not root.is_dir():
        return
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = sorted(d for d in folders if not (Path(directory)/d).is_symlink() and not d.startswith('.'))
        if 'manifest.json' in files:
            path = Path(directory)/'manifest.json'
            if not path.is_symlink():
                yield path
            folders[:] = []  # Artifacts are not nested jobs.


def resolve_job(job: str, output_dir: Path = Path('output')) -> Path:
    from .pipeline import load_manifest
    candidate = Path(job)
    if candidate.is_symlink():
        raise BookCastError('任务路径不允许符号链接。')
    if candidate.is_dir():
        return artifact_path(candidate.resolve(), 'manifest.json')
    if candidate.is_file():
        if candidate.name != 'manifest.json':
            raise BookCastError('请指定任务目录或 manifest.json。')
        return artifact_path(candidate.parent.resolve(), 'manifest.json')
    if not re.fullmatch(r'[a-f0-9]{24}|[a-f0-9]{32}', job):
        raise BookCastError('任务不存在；请提供 jobs 列出的 Job ID 或任务目录。')
    matches = []
    for path in manifest_paths(output_dir):
        try:
            manifest = load_manifest(path)
        except BookCastError:
            continue
        if job in {manifest.job_id, manifest.book_id}:
            matches.append(path)
    if not matches:
        raise BookCastError('未找到任务；请检查 Job ID 和 --output-dir。')
    if len(matches) != 1:
        raise BookCastError('任务 ID 对应多个目录；请用 jobs 列出的完整任务目录明确选择。')
    return matches[0]


def job_status(job: str, output_dir: Path = Path('output')) -> dict:
    from .pipeline import artifacts_valid, load_manifest
    path = resolve_job(job, output_dir)
    root, m = path.parent, load_manifest(path)
    active = job_is_locked(root)
    # Probe first, read again: don't combine a just-replaced stale manifest with a newly released lock.
    if not active:
        m = load_manifest(path)
    stale = not active and (m.owner is not None or m.status == 'running' or any(
        s.status == 'running' for s in m.steps.values()) or any(c.status in {'pending','running'} for c in m.ai_calls))
    damaged = [name for name, record in m.steps.items()
               if record.status == 'completed' and not artifacts_valid(root, record)]
    done = sum(s.status in {'completed','skipped'} and name not in damaged for name,s in m.steps.items())
    metadata = m.metadata_seed
    try:
        metadata = BookMetadata.model_validate_json(artifact_path(root,'metadata.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    chapters = metadata.chapter_ids if metadata else []
    running = [(name,s) for name,s in m.steps.items() if s.status=='running']
    failed = [(name,s) for name,s in m.steps.items() if s.state in {TaskState.FAILED_RETRYABLE,TaskState.FAILED_PERMANENT}]
    recent = max(running or failed, key=lambda item:item[1].updated_at)[0] if running or failed else m.status
    last_call = m.ai_calls[-1] if m.ai_calls else None
    last_error = next((c.error for c in reversed(m.ai_calls) if c.error), None)
    progress = {'book': metadata.title if metadata else Path(m.source_name).stem,
                'stage': recent, 'provider': last_call.provider if last_call else None,
                'completed': done, 'remaining': len(m.steps)-done, 'total_final': m.inventory_complete,
                'chapters_total': len(chapters), 'chapters_completed': sum(
                    f'analysis:{cid}' in m.steps and m.steps[f'analysis:{cid}'].status in {'completed','skipped'}
                    and f'analysis:{cid}' not in damaged for cid in chapters),
                'error': m.error_kind or m.error or last_error}
    return {**m.model_dump(mode='json'), 'job_id': m.job_id or m.book_id,
            'directory': str(root), 'active': active, 'stale': stale,
            'effective_state': ('FAILED_PERMANENT' if m.state == TaskState.FAILED_PERMANENT or any(
                s.state == TaskState.FAILED_PERMANENT for s in m.steps.values()) else
                'FAILED_RETRYABLE' if stale else m.state.value),
            'integrity': 'damaged' if damaged else 'ok', 'damaged_steps': damaged, 'progress': progress}


def list_jobs(output_dir: Path = Path('output')) -> dict:
    found, errors = [], []
    for path in manifest_paths(output_dir):
        try:
            result = job_status(str(path), output_dir)
            found.append({key:result[key] for key in ('job_id','book_id','directory','state','effective_state',
                'active','stale','integrity','progress','updated_at')})
        except (BookCastError,OSError,ValueError):
            errors.append({'directory': str(path.parent), 'error': '任务记录或产物无效；未修改文件'})
    return {'jobs': sorted(found,key=lambda j:j['updated_at'],reverse=True), 'errors': errors}
