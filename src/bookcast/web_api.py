"""FastAPI transport for a single-user, loopback-only BookCast client."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import shutil
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .errors import BookCastError
from .provider_api import ProviderStatus, classify_error
from .provider_config import load_config
from .provider_registry import default_registry
from .source_validation import MIMES, validate_source
from .storage import artifact_path, atomic_target, write_json
from .web_service import Submission, WebService, id_path

MAX_UPLOAD = 32 * 1024 * 1024


def create_app(data_dir: Path = Path('data/web'), config: Path | None = None, ui_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title='BookCast local application API', version='1', docs_url=None, redoc_url=None)
    service = WebService(data_dir, config)
    app.state.service = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]'])

    @app.middleware('http')
    async def local_boundary(request: Request, call_next):
        origin = request.headers.get('origin')
        # No cookies or cloud identity. Reject cross-origin browser reads/writes, including multipart forms.
        if origin and origin != f'{request.url.scheme}://{request.headers.get("host")}':
            return JSONResponse({'detail': '仅接受同源本地请求。'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': '仅接受同源本地请求。'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(BookCastError)
    async def core_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.exception_handler(FileNotFoundError)
    async def not_found(request, exc):
        return JSONResponse({'detail': '本地记录或文件不存在，请刷新列表。'}, status_code=404)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({'detail': '请求参数无效；请选择文件或明确版本、有效模式和 1–120 分钟。'}, status_code=422)

    @app.exception_handler(ValueError)
    @app.exception_handler(OSError)
    @app.exception_handler(KeyError)
    async def corrupt_record(request, exc):
        return JSONResponse({'detail': '本地记录或配置无效；请通过 CLI 检查，原文件未修改。'}, status_code=409)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'ffmpeg': bool(shutil.which('ffmpeg')), 'local': True}

    @app.post('/api/uploads', status_code=201)
    async def upload(request: Request, filename: str = Query(min_length=1, max_length=240)):
        # Raw streaming avoids buffering multipart bodies before the size limit can be enforced.
        name = re.sub(r'[^\w.() -]', '_', Path(filename.replace('\\', '/')).name, flags=re.UNICODE).strip(' .')[:120]
        fmt = Path(name).suffix.lower().lstrip('.')
        if fmt not in MIMES:
            raise HTTPException(415, '只支持 EPUB、PDF、TXT 文件。')
        mime = request.headers.get('content-type', '').split(';')[0].lower()
        if mime not in MIMES[fmt] | {'application/octet-stream'}:
            raise HTTPException(415, '文件 MIME 与所选格式不符。')
        identifier = uuid4().hex
        root = id_path(service.root, 'uploads', identifier)
        destination = artifact_path(root, name)
        with atomic_target(destination) as temporary:
            count = 0
            with temporary.open('wb') as stream:
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > MAX_UPLOAD:
                        raise HTTPException(413, '文件超过 32 MiB，请使用 CLI 处理。')
                    stream.write(chunk)
            validate_source(temporary, fmt, MAX_UPLOAD)
        write_json(root / 'upload.json', {'file': name, 'name': name, 'bytes': count})
        return {'upload_id': identifier, 'name': name, 'bytes': count}

    @app.get('/api/books/search')
    def search(title: str = Query(min_length=1, max_length=300), author: str | None = Query(default=None, max_length=200),
               language: str | None = Query(default=None, max_length=20)):
        return service.search(title, author, language)

    @app.post('/api/jobs', status_code=202)
    def submit(body: Submission, idempotency_key: str = Header(pattern=r'^[a-f0-9]{32}$')):
        return service.submit(body, idempotency_key)

    @app.get('/api/jobs')
    def jobs():
        return service.history()

    @app.get('/api/jobs/{identifier}')
    def status(identifier: str):
        return service.status(identifier)

    @app.delete('/api/jobs/{identifier}')
    def remove(identifier: str):
        service.remove(identifier)
        return {'removed': True}

    @app.post('/api/jobs/{identifier}/resume', status_code=202)
    def resume(identifier: str):
        return service.dispatch(identifier)

    @app.post('/api/jobs/{identifier}/retry', status_code=202)
    def retry(identifier: str):
        return service.dispatch(identifier, retry=True)

    @app.get('/api/jobs/{identifier}/audio')
    def audio(identifier: str):
        status = service.status(identifier)
        if not status['audio_url']:
            raise HTTPException(409, '音频尚未完成或产物校验失败，请恢复任务。')
        path = artifact_path(service.core_path(identifier).parent, 'podcast.mp3')
        return FileResponse(path, media_type='audio/mpeg', filename='podcast.mp3', content_disposition_type='inline')

    @app.get('/api/jobs/{identifier}/audio.m4b')
    def audiobook(identifier: str):
        status = service.status(identifier)
        if not status['m4b_url']:
            raise HTTPException(409, 'M4B 尚未导出或已失效；请用 bookcast export 重新导出。')
        path = artifact_path(service.core_path(identifier).parent, 'podcast.m4b')
        return FileResponse(path, media_type='audio/mp4', filename='podcast.m4b')

    @app.get('/api/providers')
    def providers():
        from .onboarding import provider_summary
        settings, registry = load_config(config), default_registry()

        def check(spec):
            try:
                provider = registry.create(spec)
                status = provider.health_check()
                caps = provider.capabilities().model_dump(mode='json')
            except Exception as exc:
                status = ProviderStatus.from_error(spec.name, spec.model, classify_error(exc))
                caps = {}
            return {**status.model_dump(mode='json'), 'kind': spec.kind, 'capabilities': caps}

        with ThreadPoolExecutor(max_workers=4) as pool:
            result = list(pool.map(check, settings.providers))
        return {'providers': result, 'llm_priority': settings.llm_priority, 'tts_priority': settings.tts_priority,
                'onboarding': provider_summary(settings, result)}

    if ui_dir is not None and ui_dir.joinpath('index.html').is_file():
        app.mount('/', StaticFiles(directory=ui_dir, html=True), name='ui')
    else:
        @app.get('/')
        def missing_ui():
            return JSONResponse({'detail': '请先在 web 目录运行 npm ci && npm run build，再通过 --ui-dir 指向 web/out。'}, status_code=503)
    return app
