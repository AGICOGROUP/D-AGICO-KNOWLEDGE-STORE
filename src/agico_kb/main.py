import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from anyio import to_thread
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from . import access_management, catalog, files, lifecycle
from .auth import authenticate
from .config import Settings
from .contracts import (
    ChunkEdit,
    ContextRequest,
    DeleteDocument,
    Grants,
    LinkRequest,
    PrepareUpload,
    Publish,
    ReadRequest,
    Revision,
    SearchRequest,
    Submission,
)
from .db import Database
from .errors import KBError
from .failures import failure_log_path, log_failure
from .mcp_server import create_mcp
from .search import SearchService


def create_app(settings: Settings) -> FastAPI:
    db = Database(settings)
    search_service = SearchService(db, settings)
    mcp_app = create_mcp(db, settings, search_service)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield
        search_service.close()
        db.close()

    app = FastAPI(title="AGICO Knowledge Store", lifespan=lifespan)
    app.state.db = db
    app.state.settings = settings
    app.state.search = search_service
    app.include_router(access_management.router(db))

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
            authorization = request.headers.get("authorization", "")
            if not authorization.startswith("Bearer ") or not authorization[7:].strip():
                return JSONResponse({"error": {"code": "UNAUTHENTICATED"}}, status_code=401)
            principal = await to_thread.run_sync(authenticate, db, authorization[7:])
            if principal is None:
                return JSONResponse({"error": {"code": "UNAUTHENTICATED"}}, status_code=401)
            request.state.principal = principal
        response = await call_next(request)
        if request.url.path.startswith("/v1/admin/"):
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
        return {
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

    portal_root = Path(__file__).parent / "portal"

    @app.get("/", include_in_schema=False)
    def portal():
        return FileResponse(
            portal_root / "index.html",
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
        if asset not in {"app.js", "styles.css", "agico-logo.png", "agico-mark.png", "admin.js"}:
            raise HTTPException(status_code=404)
        # No caching: without Cache-Control the browser applies heuristic freshness to app.js and
        # keeps running an older portal after the service is updated.
        return FileResponse(
            portal_root / asset,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
        )

    @app.post("/v1/uploads", status_code=201)
    def prepare_upload(body: PrepareUpload, request: Request, idempotency_key: str = Header()):
        result, code = files.prepare(db, settings, request.state.principal, body, idempotency_key)
        return JSONResponse(jsonable_encoder(result), status_code=code)

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

    @app.get("/v1/submissions")
    def pending(
        request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
    ):
        return lifecycle.pending(db, request.state.principal, limit, offset)

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

    app.mount("/", mcp_app)
    return app


def app_factory():
    return create_app(Settings.from_env())
