import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from anyio import to_thread
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response

from . import (
    access_management,
    account_oauth,
    accounts,
    catalog,
    feedback,
    files,
    lifecycle,
    naming,
)
from .auth import authenticate
from .config import Settings
from .contracts import (
    ChunkEdit,
    ContextRequest,
    DeleteDocument,
    Grants,
    LinkRequest,
    NameReview,
    NamingRequest,
    PrepareUpload,
    Publish,
    PublishedRename,
    ReadRequest,
    Revision,
    SearchRequest,
    Submission,
)
from .db import Database
from .errors import KBError
from .failures import failure_log_path, log_failure
from .mcp_server import create_mcp
from .previews import DocumentPreviewService
from .search import SearchService
from .thumbnails import ThumbnailService


def create_app(settings: Settings, *, portal_root: Path | None = None) -> FastAPI:
    db = Database(settings)
    search_service = SearchService(db, settings)
    thumbnails = ThumbnailService(settings.storage_root)
    previews = DocumentPreviewService(settings.storage_root)
    mcp_app = create_mcp(db, settings, search_service)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield
        search_service.close()
        thumbnails.close()
        previews.close()
        db.close()

    app = FastAPI(title="AGICO Knowledge Store", lifespan=lifespan)
    app.state.db = db
    app.state.settings = settings
    app.state.search = search_service
    default_portal = Path(__file__).with_name('portal')
    portal_directory = portal_root or default_portal
    app.include_router(access_management.router(db))
    app.include_router(accounts.router(db))
    app.include_router(account_oauth.router(db))
    app.include_router(feedback.router(db))

    @app.exception_handler(KBError)
    async def business_error(request, exc):
        return JSONResponse(jsonable_encoder(exc.payload), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Field validation messages (e.g. Office lock-file names) must reach the portal in the
        # same envelope as business errors, so errorText() can show them verbatim.
        first = exc.errors()[0] if exc.errors() else {}
        message = first.get("msg", "提交信息不符合要求。")
        return JSONResponse(
            {"error": {"code": "INVALID_ARGUMENT", "message": message}}, status_code=422
        )

    @app.middleware("http")
    async def check_identity(request: Request, call_next):
        if request.url.path.startswith("/v1/") or request.url.path.rstrip("/") == "/mcp":
            public_auth = request.url.path in {'/v1/auth/settings', '/v1/auth/login', '/v1/auth/invitation', '/v1/auth/activate'}
            if public_auth:
                response = await call_next(request)
                response.headers['Cache-Control'] = 'no-store'
                response.headers['Referrer-Policy'] = 'no-referrer'
                return response
            authorization = request.headers.get("authorization", "")
            # Reject anonymous traffic without depending on DB availability. The discovery
            # challenge also lets MCP clients begin account authorization from this 401.
            if not request.cookies.get(accounts.COOKIE) and not authorization.startswith('Bearer '):
                return JSONResponse({'error': {'code': 'UNAUTHENTICATED'}}, status_code=401,
                    headers={'WWW-Authenticate':f'Bearer resource_metadata="{str(request.base_url).rstrip("/")}/.well-known/oauth-protected-resource", scope="kb:access"'})
            account_mode = await to_thread.run_sync(accounts.enabled, db)
            cookie_session = None
            if account_mode and not request.url.path.startswith('/mcp'):
                cookie_session = await to_thread.run_sync(accounts.authenticate_session, db, request.cookies.get(accounts.COOKIE))
            if cookie_session:
                principal, request.state.csrf_token = cookie_session
            else:
                principal = await to_thread.run_sync(authenticate, db, authorization[7:], str(request.base_url).rstrip('/')+'/mcp') if authorization.startswith('Bearer ') else None
            if principal is None:
                headers = {'WWW-Authenticate':f'Bearer resource_metadata="{str(request.base_url).rstrip("/")}/.well-known/oauth-protected-resource", scope="kb:access"'} if account_mode else {}
                return JSONResponse({"error": {"code": "UNAUTHENTICATED"}}, status_code=401, headers=headers)
            request.state.principal = principal
            if account_mode:
                try:
                    if principal.must_change_password and request.url.path not in {'/v1/auth/me','/v1/auth/change-password','/v1/auth/logout'}:
                        raise KBError('PASSWORD_CHANGE_REQUIRED','首次登录请先修改初始密码。',403)
                    if request.url.path.startswith('/v1/admin/'):
                        raise KBError('ACCESS_CODES_RETIRED','访问码管理已停用，请使用账号管理。',410)
                    if request.url.path.startswith(('/v1/auth/','/v1/accounts')) and not cookie_session:
                        raise KBError('BROWSER_SESSION_REQUIRED','请在浏览器中使用邮箱密码登录。',401)
                    if cookie_session and request.method in {'POST','PUT','PATCH','DELETE'}:
                        accounts.validate_csrf(request,request.state.csrf_token)
                except KBError as exc:
                    return JSONResponse(jsonable_encoder(exc.payload),status_code=exc.status)
        response = await call_next(request)
        if request.url.path.startswith(("/v1/admin/", '/v1/accounts', '/v1/auth/')):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        import psycopg

        try:
            # Independent bounded probe never queues behind the application pool.
            with psycopg.connect(
                settings.database_url, connect_timeout=2, options="-cstatement_timeout=2000"
            ) as conn:
                conn.execute("SELECT 1")
            return {"status": "ready"}
        except psycopg.Error:
            return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.get("/v1/catalog")
    def get_catalog(request: Request):
        return catalog.catalog(db, request.state.principal)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        # Anything not modelled as a KBError is a defect: record the reason with a traceback so the
        # failure can be diagnosed after the fact, and still answer in the business envelope.
        path = log_failure(
            settings,
            "request_failed",
            f"{type(exc).__name__}: {exc}",
            method=request.method,
            path=request.url.path,
            traceback=traceback.format_exc().replace("\n", " ")[:2000],
        )
        return JSONResponse(
            {
                "error": {
                    "code": "INTERNAL",
                    "message": f"服务器处理失败：{type(exc).__name__}。原因已记入 {path}",
                }
            },
            status_code=500,
        )

    @app.get("/v1/portal-session")
    def portal_session(request: Request):
        principal = request.state.principal
        result = {
            "identity": principal.id,
            "max_upload_bytes": settings.max_upload_bytes,
            # The portal shows the approval actions only for the units this account approves for.
            "publisher_organizations": sorted(
                key for key in principal.memberships if principal.publisher(key)
            ),
            "is_admin": principal.is_admin,
            # So a failure message can point at the exact file to inspect.
            "failure_log": str(failure_log_path(settings)),
        }
        if principal.account_role:
            with db.connection() as conn:
                result['identity'] = conn.execute('SELECT email FROM account_users WHERE principal_id=%s', (principal.id,)).fetchone()['email']
            result['account_role'] = principal.account_role
        return result

    @app.get("/", include_in_schema=False)
    def portal():
        return FileResponse(
            portal_directory / "index.html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' blob:; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/portal/{asset}", include_in_schema=False)
    def portal_asset(asset: str):
        if asset not in {"app.js", "account.js", "folder-upload.js", "document-preview.js", "styles.css", "agico-logo.png", "agico-mark.png", "admin.js", "feedback.js"}:
            raise HTTPException(status_code=404)
        # No caching: without Cache-Control the browser applies heuristic freshness to app.js and
        # keeps running an older portal after the service is updated.
        return FileResponse(
            (portal_directory / asset) if (portal_directory / asset).is_file() else (default_portal / asset),
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
        )

    @app.post("/v1/uploads", status_code=201)
    def prepare_upload(body: PrepareUpload, request: Request, idempotency_key: str = Header()):
        result, code = files.prepare(db, settings, request.state.principal, body, idempotency_key)
        return JSONResponse(jsonable_encoder(result), status_code=code)

    @app.get("/v1/naming/rules")
    def naming_rules():
        return naming.shared_rules()

    @app.post("/v1/naming/suggest")
    def suggest_names(body: NamingRequest, request: Request):
        return naming.suggest(db, settings, request.state.principal, body)

    @app.put("/v1/uploads/{upload_id}/content")
    async def upload(upload_id: UUID, request: Request):
        return await files.upload_content(db, settings, request.state.principal, upload_id, request)

    @app.post("/v1/submissions")
    def submit(body: Submission, request: Request):
        result, code = files.submit(db, request.state.principal, body)
        return JSONResponse(jsonable_encoder(result), status_code=code)

    @app.get("/v1/documents")
    def documents(
        request: Request,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        managed: bool = False,
    ):
        return catalog.list_documents(db, request.state.principal, limit, offset, managed)

    @app.get("/v1/versions/{version_id}")
    def status(version_id: UUID, request: Request, history: bool = False):
        return files.status(db, request.state.principal, version_id, history)

    @app.get("/v1/versions/{version_id}/content")
    def download(version_id: UUID, request: Request, history: bool = False):
        path, name = files.download(db, settings, request.state.principal, version_id, history)
        return FileResponse(
            path,
            filename=name,
            media_type="application/octet-stream",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/v1/versions/{version_id}/thumbnail")
    def thumbnail(version_id: UUID, request: Request):
        # Reuse the exact visibility checks used for the original download.
        path, name = files.download(db, settings, request.state.principal, version_id)
        preview = thumbnails.get(path, name)
        if preview is None:
            return Response(status_code=202, headers={"Retry-After": "2", "Cache-Control": "no-store"})
        return FileResponse(preview, media_type="image/png",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/v1/versions/{version_id}/preview")
    def document_preview(version_id: UUID, request: Request):
        path, name = files.download(db, settings, request.state.principal, version_id)
        manifest = previews.manifest(path, name)
        if manifest is None:
            return Response(status_code=202, headers={"Retry-After": "2", "Cache-Control": "no-store"})
        return JSONResponse(manifest, headers={"Cache-Control": "no-store"})

    @app.get("/v1/versions/{version_id}/preview/pages/{page}")
    def document_page(version_id: UUID, page: int, request: Request,
                      width: int = Query(1800, ge=800, le=2400)):
        # Authorize every page and cache hit using the original's current visibility.
        path, name = files.download(db, settings, request.state.principal, version_id)
        preview = previews.page(path, name, page, width)
        if preview is None:
            return Response(status_code=202, headers={"Retry-After": "2", "Cache-Control": "no-store"})
        return FileResponse(preview, media_type="image/png",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/v1/submissions")
    def pending(
        request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
    ):
        return lifecycle.pending(db, request.state.principal, limit, offset)

    @app.patch("/v1/versions/{version_id}/name")
    def review_name(version_id: UUID, body: NameReview, request: Request):
        return lifecycle.review_name(db, request.state.principal, version_id, body)

    @app.post("/v1/versions/{version_id}/publish")
    def publish(version_id: UUID, body: Publish, request: Request):
        return lifecycle.publish(db, request.state.principal, version_id, body)

    @app.get("/v1/versions/{version_id}/chunks")
    def review_chunks(version_id: UUID, request: Request):
        return search_service.review_chunks(request.state.principal, version_id)

    @app.patch("/v1/versions/{version_id}/chunks")
    def edit_chunk(version_id: UUID, body: ChunkEdit, request: Request):
        return search_service.edit_chunk(request.state.principal, version_id, body)

    @app.delete("/v1/documents/{document_id}")
    def delete_document(document_id: UUID, body: DeleteDocument, request: Request):
        return lifecycle.delete_document(db, settings, request.state.principal, document_id, body)

    @app.patch("/v1/documents/{document_id}/title")
    def rename_published(document_id: UUID, body: PublishedRename, request: Request):
        return lifecycle.rename_published(db, request.state.principal, document_id, body)

    @app.patch('/v1/versions/{version_id}/title')
    def rename_pending(version_id: UUID, body: PublishedRename, request: Request):
        return lifecycle.rename_pending(db, request.state.principal, version_id, body)

    @app.post("/v1/documents/{document_id}/withdraw")
    def withdraw(document_id: UUID, body: Revision, request: Request):
        return lifecycle.withdraw(db, request.state.principal, document_id, body)

    @app.post("/v1/versions/{version_id}/restore")
    def restore(version_id: UUID, body: Revision, request: Request):
        return lifecycle.restore(db, request.state.principal, version_id, body)

    @app.post("/v1/submissions/{version_id}/withdraw")
    def withdraw_submission(version_id: UUID, request: Request):
        return lifecycle.withdraw_submission(db, request.state.principal, version_id)

    @app.put("/v1/documents/{document_id}/grants")
    def grants(document_id: UUID, body: Grants, request: Request):
        return lifecycle.set_grants(db, request.state.principal, document_id, body)

    @app.get("/v1/documents/{document_id}/audit")
    def audit(
        document_id: UUID,
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        return lifecycle.audit_history(db, request.state.principal, document_id, limit, offset)

    @app.get("/v1/documents/{document_id}/versions")
    def versions(
        document_id: UUID,
        request: Request,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        return lifecycle.version_history(db, request.state.principal, document_id, limit, offset)

    @app.post("/v1/search")
    def search(body: SearchRequest, request: Request):
        return search_service.search(request.state.principal, body)

    @app.post("/v1/read")
    def read(body: ReadRequest, request: Request):
        return search_service.read(request.state.principal, body)

    @app.post("/v1/context")
    def context(body: ContextRequest, request: Request):
        return search_service.context(request.state.principal, body)

    @app.get("/v1/versions/{version_id}/file")
    def file_ref(version_id: UUID, request: Request):
        return files.file_reference(db, request.state.principal, version_id)

    @app.post("/v1/versions/{version_id}/links")
    def link(version_id: UUID, body: LinkRequest, request: Request):
        return files.link_file(db, request.state.principal, version_id, body)

    @app.post("/v1/versions/{version_id}/retry")
    def retry(version_id: UUID, request: Request):
        return files.retry_processing(db, settings, request.state.principal, version_id)

    app.router.routes.extend(account_oauth.routes(db, settings))
    app.mount("/", mcp_app)
    return app


def app_factory():
    return create_app(Settings.from_env())
