import hashlib
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

from anyio import to_thread
from psycopg.types.json import Jsonb

from .catalog import can_read_document, get_version, version_result
from .errors import KBError, not_found


def fingerprint(body):
    return hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()


def audit(conn, principal, document_id, version_id, action, **details):
    conn.execute(
        "INSERT INTO audit_events(actor_id,document_id,version_id,action,details) VALUES (%s,%s,%s,%s,%s)",
        (principal.id, document_id, version_id, action, Jsonb(details)),
    )


def duplicate_version(conn, checksum, exclude_upload_id=None):
    """An existing non-withdrawn version whose stored original has the same SHA-256.

    Withdrawn versions do not count, so a deliberately removed file can be uploaded again.
    """
    if not checksum:
        return None
    sql = """SELECT v.id AS version_id,v.state,d.id AS document_id,d.title,d.organization_id,
        v.created_by,u.filename FROM versions v JOIN documents d ON d.id=v.document_id
        JOIN uploads u ON u.id=v.upload_id WHERE u.sha256=%s AND v.state <> 'withdrawn'"""
    params = [checksum]
    if exclude_upload_id is not None:
        sql += " AND u.id <> %s"
        params.append(exclude_upload_id)
    sql += " ORDER BY v.created_at LIMIT 1"
    return conn.execute(sql, tuple(params)).fetchone()


def duplicate_result(row, checksum):
    """Same bytes are already in the knowledge base: skip instead of storing a second copy."""
    return {
        "status": "duplicate",
        "skipped": True,
        "message": "该文件与库中已有资料完全相同（SHA-256 一致），已自动跳过。",
        "sha256": checksum,
        "duplicate_of": {
            "version_id": row["version_id"],
            "document_id": row["document_id"],
            "title": row["title"],
            "organization_id": row["organization_id"],
            "filename": row["filename"],
            "state": row["state"],
        },
    }


def prepare(db, settings, principal, body, key):
    if not key or len(key) > 128 or body.size > settings.max_upload_bytes:
        raise KBError("INVALID_ARGUMENT", "需要有效幂等键，且文件大小不能超过配置上限。", 422)
    request_hash = fingerprint(body)
    with db.connection(write=True) as conn:
        # Declared hash lets a known duplicate be skipped before any bytes are transferred.
        existing = duplicate_version(conn, body.sha256)
        if existing:
            return duplicate_result(existing, body.sha256), 200
        conn.execute(
            """INSERT INTO uploads(id,owner_id,filename,expected_size,expected_sha256,idempotency_key,request_hash)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(owner_id,idempotency_key) DO NOTHING""",
            (uuid4(), principal.id, body.filename, body.size, body.sha256, key, request_hash),
        )
        row = conn.execute(
            "SELECT * FROM uploads WHERE owner_id=%s AND idempotency_key=%s", (principal.id, key)
        ).fetchone()
        if row["request_hash"] != request_hash:
            raise KBError("VERSION_CONFLICT", "同一幂等键已用于不同请求。")
        return {
            "upload_id": row["id"],
            "state": row["state"],
            "expires_at": row["expires_at"],
            "upload_url": f"/v1/uploads/{row['id']}/content",
        }, 201


def owned_upload(conn, principal, upload_id, lock=False):
    row = conn.execute(
        "SELECT * FROM uploads WHERE id=%s AND owner_id=%s" + (" FOR UPDATE" if lock else ""),
        (upload_id, principal.id),
    ).fetchone()
    if row is None:
        not_found()
    if row["state"] != "submitted" and row["expires_at"] <= datetime.now(UTC):
        raise KBError("UPLOAD_INCOMPLETE", "上传已过期，请重新准备上传。")
    return row


async def upload_content(db, settings, principal, upload_id, request):
    def lookup():
        with db.connection() as conn:
            return owned_upload(conn, principal, upload_id)

    row = await to_thread.run_sync(lookup)
    root = settings.storage_root
    root.mkdir(parents=True, exist_ok=True)
    temp = root / (uuid4().hex + ".part")
    count, digest = 0, hashlib.sha256()
    try:
        with temp.open("xb") as target:
            async for chunk in request.stream():
                count += len(chunk)
                if count > min(row["expected_size"], settings.max_upload_bytes):
                    raise KBError("INVALID_ARGUMENT", "接收字节数超过申报大小。", 422)
                digest.update(chunk)
                await to_thread.run_sync(target.write, chunk)
            await to_thread.run_sync(target.flush)
            await to_thread.run_sync(os.fsync, target.fileno())
        checksum = digest.hexdigest()
        if count != row["expected_size"] or (
            row["expected_sha256"] and checksum != row["expected_sha256"]
        ):
            raise KBError("INVALID_ARGUMENT", "文件大小或 SHA-256 校验不匹配。", 422)

        def finalize():
            with db.connection(write=True) as conn:
                current = owned_upload(conn, principal, upload_id, lock=True)
                if current["state"] != "pending":
                    if current["sha256"] != checksum:
                        raise KBError("VERSION_CONFLICT", "已保存的原文件不可覆盖。")
                    return {"upload_id": upload_id, "state": current["state"], "sha256": checksum}
                blob_key = uuid4().hex + ".blob"
                # Rename precedes DB commit. A crash can leave an orphan, never a false completion.
                temp.rename(root / blob_key)
                conn.execute(
                    "UPDATE uploads SET state='complete',size=%s,sha256=%s,blob_key=%s WHERE id=%s",
                    (count, checksum, blob_key, upload_id),
                )
                return {"upload_id": upload_id, "state": "complete", "sha256": checksum}

        return await to_thread.run_sync(finalize)
    finally:
        temp.unlink(missing_ok=True)


def submit(db, principal, body):
    request_hash = fingerprint(body)
    with db.connection(write=True) as conn:
        upload = owned_upload(conn, principal, body.upload_id, lock=True)
        if upload["state"] == "pending":
            raise KBError("UPLOAD_INCOMPLETE", "文件尚未完整上传。")
        if upload["state"] == "submitted":
            if upload["submit_hash"] != request_hash:
                raise KBError("VERSION_CONFLICT", "该上传已用于不同提交。")
            row = conn.execute(
                "SELECT id FROM versions WHERE upload_id=%s", (body.upload_id,)
            ).fetchone()
            result = version_result(get_version(conn, principal, row["id"]))
            return submission_response(result, 200)
        # Authoritative duplicate check: the hash is always computed by the server while receiving
        # the bytes, so identical content is skipped even when the client never declared a hash.
        existing = duplicate_version(conn, upload["sha256"], exclude_upload_id=body.upload_id)
        if existing:
            return duplicate_result(existing, upload["sha256"]), 200
        if body.organization_id not in principal.memberships:
            not_found()
        if not conn.execute("SELECT 1 FROM categories WHERE id=%s", (body.category_id,)).fetchone():
            raise KBError("INVALID_ARGUMENT", "用途分类不存在。", 422)
        if body.document_id:
            doc = conn.execute(
                "SELECT * FROM documents WHERE id=%s FOR UPDATE", (body.document_id,)
            ).fetchone()
            if (
                not doc
                or doc["organization_id"] != body.organization_id
                or not can_read_document(conn, principal, doc)
            ):
                not_found()
            for field in [
                "category_id",
                "title",
                "visibility",
                "product",
                "model",
                "project",
                "business_date",
            ]:
                if getattr(body, field) != doc[field]:
                    raise KBError(
                        "INVALID_ARGUMENT", "替换稿应保持原文件元数据；请沿用文件当前信息。", 422
                    )
            document_id = doc["id"]
        else:
            if body.base_revision != 0:
                raise KBError("INVALID_ARGUMENT", "新文件的基础修订号必须为 0。", 422)
            document_id = uuid4()
            conn.execute(
                """INSERT INTO documents(id,organization_id,category_id,title,created_by,visibility,
                product,model,project,business_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    document_id,
                    body.organization_id,
                    body.category_id,
                    body.title,
                    principal.id,
                    body.visibility,
                    body.product,
                    body.model,
                    body.project,
                    body.business_date,
                ),
            )
        version_id = uuid4()
        conn.execute(
            "INSERT INTO versions(id,document_id,upload_id,created_by,source_version,base_revision) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                version_id,
                document_id,
                body.upload_id,
                principal.id,
                body.source_version,
                body.base_revision,
            ),
        )
        conn.execute("INSERT INTO jobs(id,version_id) VALUES (%s,%s)", (uuid4(), version_id))
        conn.execute(
            "UPDATE uploads SET state='submitted',submit_hash=%s WHERE id=%s",
            (request_hash, body.upload_id),
        )
        audit(conn, principal, document_id, version_id, "submit", base_revision=body.base_revision)
        result = submission_result(conn, version_id)
        return submission_response(result, 201)


def submission_response(result, success_status):
    if result["state"] == "draft" and result["base_revision"] != result["revision"]:
        result["error"] = {
            "code": "VERSION_CONFLICT",
            "message": "基础版本已变更，修改稿已保留，请核对后重新提交。",
        }
        return result, 409
    return result, success_status


def submission_result(conn, version_id):
    row = conn.execute(
        """SELECT v.id,v.document_id,v.processing_status,v.state,v.publication_mode,v.capabilities,v.warnings,
        d.revision,d.organization_id,d.category_id,d.title,d.visibility,d.product,d.model,
        d.project,d.business_date,v.base_revision,v.source_version,
        u.filename,u.size,u.sha256 FROM versions v JOIN documents d ON d.id=v.document_id
        JOIN uploads u ON u.id=v.upload_id WHERE v.id=%s""",
        (version_id,),
    ).fetchone()
    return version_result(row)


def status(db, principal, version_id, history=False):
    with db.connection() as conn:
        return version_result(get_version(conn, principal, version_id, history))


def download(db, settings, principal, version_id, history=False):
    with db.connection() as conn:
        row = get_version(conn, principal, version_id, history)
        return settings.storage_root / row["blob_key"], row["filename"]


def file_reference(db, principal, version_id):
    with db.connection() as conn:
        row = get_version(conn, principal, version_id)
        links = []
        for link in conn.execute(
            "SELECT child_version_id,label FROM file_links WHERE parent_version_id=%s ORDER BY child_version_id LIMIT 50",
            (version_id,),
        ).fetchall():
            try:
                child = get_version(conn, principal, link["child_version_id"])
            except KBError:
                continue
            links.append(
                {"version_id": child["id"], "filename": child["filename"], "label": link["label"]}
            )
        return {
            "version_id": row["id"],
            "filename": row["filename"],
            "size": row["size"],
            "sha256": row["sha256"],
            "download_url": f"/v1/versions/{version_id}/content",
            "requires_auth": True,
            "links": links,
        }


def link_file(db, principal, version_id, body):
    with db.connection(write=True) as conn:
        parent = get_version(conn, principal, version_id)
        if not principal.publisher(parent["organization_id"]):
            not_found()
        get_version(conn, principal, body.child_version_id)
        if str(version_id) == str(body.child_version_id):
            raise KBError("INVALID_ARGUMENT", "不能关联文件自身。", 422)
        conn.execute(
            "INSERT INTO file_links VALUES (%s,%s,%s) ON CONFLICT(parent_version_id,child_version_id) DO UPDATE SET label=excluded.label",
            (version_id, body.child_version_id, body.label),
        )
        audit(
            conn,
            principal,
            parent["document_id"],
            version_id,
            "link_file",
            child_version_id=str(body.child_version_id),
        )
        return {
            "version_id": version_id,
            "child_version_id": body.child_version_id,
            "label": body.label,
        }


def retry_processing(db, settings, principal, version_id):
    from .worker import Worker

    with db.connection() as conn:
        row = get_version(conn, principal, version_id)
        if not principal.publisher(row["organization_id"]) and not (
            row["state"] == "draft" and row["created_by"] == principal.id
        ):
            not_found()
    try:
        Worker(db, settings).retry(version_id)
    except ValueError as error:
        raise KBError("VERSION_CONFLICT", str(error)) from None
    return {"version_id": version_id, "queued": True}
