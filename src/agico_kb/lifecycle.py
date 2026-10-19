from pathlib import Path

from psycopg.types.json import Jsonb

from .errors import KBError, not_found
from .files import audit, submission_result
from .naming import title_quality_issue
from .parse_notices import public_warnings
from .project_registry import _fold as _project_fold
from .project_registry import confirm


def managed_document(conn, principal, document_id):
    doc = conn.execute("SELECT * FROM documents WHERE id=%s FOR UPDATE", (document_id,)).fetchone()
    if not doc or not principal.publisher(doc["organization_id"]):
        not_found()
    return doc


def managed_version(conn, principal, version_id):
    ref = conn.execute("SELECT document_id FROM versions WHERE id=%s", (version_id,)).fetchone()
    if not ref:
        not_found()
    # All lifecycle writers lock the document before the version.
    doc = managed_document(conn, principal, ref["document_id"])
    version = conn.execute(
        "SELECT * FROM versions WHERE id=%s FOR UPDATE", (version_id,)
    ).fetchone()
    return doc, version


def check_revision(doc, expected, base=None):
    if doc["revision"] != expected or (base is not None and base != doc["revision"]):
        raise KBError(
            "VERSION_CONFLICT",
            "文件已变更，请核对当前版本后重新提交。",
            current_revision=doc["revision"],
            active_version_id=doc["active_version_id"],
        )


def activate(conn, principal, doc, version, action):
    if doc["active_version_id"]:
        conn.execute(
            "UPDATE versions SET state='superseded' WHERE id=%s", (doc["active_version_id"],)
        )
    conn.execute(
        "UPDATE versions SET state='published',published_at=COALESCE(published_at,now()) WHERE id=%s",
        (version["id"],),
    )
    conn.execute(
        "UPDATE documents SET active_version_id=%s,revision=revision+1 WHERE id=%s",
        (version["id"], doc["id"]),
    )
    audit(
        conn,
        principal,
        doc["id"],
        version["id"],
        action,
        previous_version=str(doc["active_version_id"]) if doc["active_version_id"] else None,
        revision=doc["revision"] + 1,
    )
    return submission_result(conn, version["id"])


def publish(db, principal, version_id, body):
    with db.connection(write=True) as conn:
        doc, version = managed_version(conn, principal, version_id)
        check_revision(doc, body.expected_revision, version["base_revision"])
        if version["state"] != "draft":
            raise KBError("VERSION_CONFLICT", "只能发布待审核草稿。")
        if (version["capabilities"].get("naming_review") or {}).get("needs_review"):
            raise KBError("NAMING_REVIEW_REQUIRED", "请先核对文件名称和项目归属，再审核发布。")
        if (title_quality_issue(doc["title"]) and not
                (version["capabilities"].get("naming_review") or {}).get("reviewed_by")):
            raise KBError("NAMING_REVIEW_REQUIRED", "文件类型名称过于笼统，请先核对名称。")
        filename = conn.execute(
            "SELECT filename FROM uploads WHERE id=%s", (version["upload_id"],)
        ).fetchone()["filename"]
        if Path(filename).suffix.lower() in {".dwg", ".dxf"}:
            job = conn.execute("SELECT state FROM jobs WHERE version_id=%s", (version_id,)).fetchone()
            if not job or job["state"] != "complete" or version["processing_status"] not in {"ready", "partial", "stored_only"}:
                raise KBError("PROCESSING_INCOMPLETE", "CAD 图纸尚未完成文字解析，完成后才能审核通过。")
        if version["processing_status"] not in {"ready", "stored_only"} and not body.accept_incomplete:
            raise KBError("PROCESSING_INCOMPLETE", "正文尚未完整可检索；确认后可以仅作为文件发布。")
        if body.category_id is not None:
            if not conn.execute(
                "SELECT 1 FROM categories WHERE id=%s", (body.category_id,)
            ).fetchone():
                raise KBError("INVALID_ARGUMENT", "用途分类不存在。", 422)
            if body.category_id != doc["category_id"]:
                conn.execute(
                    "UPDATE documents SET category_id=%s WHERE id=%s",
                    (body.category_id, doc["id"]),
                )
                audit(
                    conn,
                    principal,
                    doc["id"],
                    version_id,
                    "review_category",
                    previous_category_id=doc["category_id"],
                    category_id=body.category_id,
                    revision=doc["revision"] + 1,
                )
        mode = "content" if version["processing_status"] == "ready" else "file_only"
        conn.execute("UPDATE versions SET publication_mode=%s WHERE id=%s", (mode, version_id))
        result = activate(conn, principal, doc, version, "publish")
        return result


def review_name(db, principal, version_id, body):
    with db.connection(write=True) as conn:
        doc, version = managed_version(conn, principal, version_id)
        if version["state"] != "draft" or doc["title"] != body.current_title:
            raise KBError("VERSION_CONFLICT", "文件名称已变化，请刷新后重新核对。")
        if (version["capabilities"].get("naming_review") or {}).get("state") in {"pending", "running"}:
            raise KBError("VERSION_CONFLICT", "AI 仍在分析文件名，请稍后再核对。")
        filename = conn.execute("SELECT filename FROM uploads WHERE id=%s", (version["upload_id"],)).fetchone()["filename"]
        if (Path(body.title).suffix.lower() != Path(filename).suffix.lower()
            or any(char in body.title for char in "/\\:")
            or any(ord(char) < 32 for char in body.title)):
            raise KBError("INVALID_ARGUMENT", "规范名称必须保留原文件扩展名，且不能包含路径字符。", 422)
        if title_quality_issue(body.title):
            raise KBError("INVALID_ARGUMENT", "文件类型名称过于笼统，请填写明确的文件类型后再确认。", 422)
        project = (body.project or "").strip()
        official_code = (body.official_project_code or "").strip()
        if official_code and not project:
            raise KBError("INVALID_ARGUMENT", "请先填写项目名称，再填写正式项目编号。", 422)
        if official_code and (any(char in official_code for char in "/\\:<>|?*")
                              or any(ord(char) < 32 for char in official_code)):
            raise KBError("INVALID_ARGUMENT", "正式项目编号不能包含路径字符。", 422)
        if project and not body.title.startswith(project + "_"):
            raise KBError("INVALID_ARGUMENT", "项目名称应与规范名称的开头一致。", 422)
        project_id = None
        title = body.title
        if project:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                         (doc["organization_id"] + ":project:" + _project_fold(project),))
            try:
                record = confirm(conn, doc["organization_id"], principal.id,
                                 doc["visibility"], project, official_code)
            except ValueError as error:
                raise KBError("INVALID_ARGUMENT", str(error), 422) from error
            project_id = record["id"]
            project = record["official_code"] or record["code"]
            title = project + body.title[len(body.project.strip()):]
            if len(title) > 300:
                raise KBError("INVALID_ARGUMENT", "规范名称过长。", 422)
        conn.execute("UPDATE documents SET title=%s,project=%s,project_id=%s WHERE id=%s",
                     (title, project or None, project_id, doc["id"]))
        conn.execute(
            "UPDATE versions SET capabilities=jsonb_set(capabilities,'{naming_review}',%s,true) WHERE id=%s",
            (Jsonb({"state": "done", "needs_review": False, "reviewed_by": principal.id}), version_id),
        )
        audit(conn, principal, doc["id"], version_id, "review_name",
              previous_title=doc["title"], title=title, project=project)
        return submission_result(conn, version_id)


def rename_pending(db, principal, version_id, body):
    """Rename a finished draft's display title without altering its naming decision."""
    with db.connection(write=True) as conn:
        doc, version = managed_version(conn, principal, version_id)
        check_revision(doc, body.expected_revision)
        if version['state'] != 'draft' or doc['title'] != body.current_title:
            raise KBError('VERSION_CONFLICT', '文件已变化，请刷新后重试。')
        job = conn.execute('SELECT state FROM jobs WHERE version_id=%s', (version_id,)).fetchone()
        if (version['processing_status'] not in {'ready', 'partial', 'stored_only'}
                or not job or job['state'] != 'complete'
                or (version['capabilities'].get('naming_review') or {}).get('state') in {'pending', 'running'}):
            raise KBError('PROCESSING_INCOMPLETE', '文件尚未完成处理，请完成后再重命名。')
        filename = conn.execute('SELECT filename FROM uploads WHERE id=%s',
            (version['upload_id'],)).fetchone()['filename']
        if (Path(body.title).suffix.lower() != Path(filename).suffix.lower()
                or any(char in body.title for char in '/\\:<>|?*')
                or any(ord(char) < 32 for char in body.title)):
            raise KBError('INVALID_ARGUMENT', '新名称必须保留原文件扩展名，且不能包含路径或特殊字符。', 422)
        if body.title != doc['title']:
            conn.execute('UPDATE documents SET title=%s WHERE id=%s', (body.title, doc['id']))
            audit(conn, principal, doc['id'], version_id, 'rename_pending',
                previous_title=doc['title'], title=body.title)
        # Draft renaming keeps the publication revision/base revision unchanged.
        return submission_result(conn, version_id)


def rename_published(db, principal, document_id, body):
    """Change a published file's catalog name without touching its original upload."""
    if not principal.is_admin:
        not_found()
    with db.connection(write=True) as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=%s FOR UPDATE", (document_id,)).fetchone()
        if not doc or not doc["active_version_id"]:
            not_found()
        check_revision(doc, body.expected_revision)
        if doc["title"] != body.current_title:
            raise KBError("VERSION_CONFLICT", "文件名称已变化，请刷新后重试。")
        filename = conn.execute(
            """SELECT u.filename FROM versions v JOIN uploads u ON u.id=v.upload_id
            WHERE v.id=%s""", (doc["active_version_id"],)
        ).fetchone()["filename"]
        if (Path(body.title).suffix.lower() != Path(filename).suffix.lower()
            or any(char in body.title for char in '/\\:<>|?*')
            or any(ord(char) < 32 for char in body.title)):
            raise KBError("INVALID_ARGUMENT", "新名称必须保留原文件扩展名，且不能包含路径或特殊字符。", 422)
        if body.title == doc["title"]:
            return {"document_id": document_id, "title": doc["title"], "revision": doc["revision"]}
        conn.execute(
            "UPDATE documents SET title=%s,revision=revision+1 WHERE id=%s",
            (body.title, document_id),
        )
        audit(conn, principal, document_id, doc["active_version_id"], "rename_published",
              previous_title=doc["title"], title=body.title, revision=doc["revision"] + 1)
        return {"document_id": document_id, "title": body.title, "revision": doc["revision"] + 1}


def withdraw(db, principal, document_id, body):
    with db.connection(write=True) as conn:
        doc = managed_document(conn, principal, document_id)
        check_revision(doc, body.expected_revision)
        if not doc["active_version_id"]:
            raise KBError("VERSION_CONFLICT", "当前没有正式版本可以撤下。")
        conn.execute(
            "UPDATE versions SET state='withdrawn' WHERE id=%s", (doc["active_version_id"],)
        )
        conn.execute(
            "UPDATE documents SET active_version_id=NULL,revision=revision+1 WHERE id=%s",
            (document_id,),
        )
        audit(
            conn,
            principal,
            document_id,
            doc["active_version_id"],
            "withdraw",
            revision=doc["revision"] + 1,
        )
        return {
            "document_id": document_id,
            "revision": doc["revision"] + 1,
            "active_version_id": None,
        }


def restore(db, principal, version_id, body):
    with db.connection(write=True) as conn:
        doc, version = managed_version(conn, principal, version_id)
        check_revision(doc, body.expected_revision)
        if version["state"] not in {"superseded", "withdrawn"} or not version["published_at"]:
            raise KBError("VERSION_CONFLICT", "只能恢复曾经正式发布的历史版本。")
        return activate(conn, principal, doc, version, "restore")


def withdraw_submission(db, principal, version_id):
    with db.connection(write=True) as conn:
        ref = conn.execute(
            """SELECT v.document_id,v.created_by,d.organization_id FROM versions v
            JOIN documents d ON d.id=v.document_id WHERE v.id=%s""",
            (version_id,),
        ).fetchone()
        # The author can withdraw their own draft; the unit's approver (publisher) can reject it.
        if not ref or not principal.can_access_department(ref['organization_id']) or (
            ref["created_by"] != principal.id and not principal.publisher(ref["organization_id"])
        ):
            not_found()
        conn.execute("SELECT id FROM documents WHERE id=%s FOR UPDATE", (ref["document_id"],))
        version = conn.execute(
            "SELECT * FROM versions WHERE id=%s FOR UPDATE", (version_id,)
        ).fetchone()
        if version["state"] != "draft":
            raise KBError("VERSION_CONFLICT", "只能处理尚未发布的提交。")
        conn.execute("UPDATE versions SET state='withdrawn' WHERE id=%s", (version_id,))
        conn.execute("UPDATE jobs SET state='cancelled' WHERE version_id=%s", (version_id,))
        rejected = ref["created_by"] != principal.id
        audit(
            conn,
            principal,
            ref["document_id"],
            version_id,
            "reject" if rejected else "withdraw_submission",
            **({"author_id": ref["created_by"]} if rejected else {}),
        )
        result = submission_result(conn, version_id)
        if rejected:
            result["rejected"] = True
            result["message"] = "已驳回该提交，上传者可修改后重新提交。"
        return result


def pending(db, principal, limit, offset):
    with db.connection() as conn:
        scope = (
            principal.id, [k for k in principal.memberships if principal.publisher(k)],
            not principal.department_restricted, list(principal.memberships),
        )
        total = conn.execute(
            """SELECT count(*) AS total FROM versions v JOIN documents d ON d.id=v.document_id
            WHERE v.state='draft' AND (v.created_by=%s OR d.organization_id=ANY(%s))
            AND (%s OR d.organization_id=ANY(%s))""",
            scope,
        ).fetchone()["total"]
        reviewable_total = conn.execute(
            """SELECT count(*) AS total FROM versions v JOIN documents d ON d.id=v.document_id
            LEFT JOIN jobs j ON j.version_id=v.id
            WHERE v.state='draft' AND d.organization_id=ANY(%s)
            AND v.processing_status IN ('ready','partial','stored_only')
            AND COALESCE((v.capabilities->'naming_review'->>'needs_review')::boolean,false)=false
            AND (d.title !~ '_(技术文件章节|方案文档|项目资料|技术资料|资料|文档|文件|章节|其他|未知)(_|\\.)'
                 OR v.capabilities->'naming_review'->>'reviewed_by' IS NOT NULL)
            AND (j.state IS NULL OR j.state NOT IN ('queued','running'))""",
            (scope[1],),
        ).fetchone()["total"]
        rows = conn.execute(
            """SELECT v.id AS version_id,v.document_id,d.title,u.filename,d.project,d.organization_id,
            d.revision,v.base_revision,v.processing_status,v.created_by,v.warnings,
            j.state AS job_state,j.last_error AS failure_reason,
            v.capabilities->'ingestion_progress' AS progress,
            v.capabilities->'naming_review' AS naming_review FROM versions v
            JOIN documents d ON d.id=v.document_id
            JOIN uploads u ON u.id=v.upload_id
            LEFT JOIN jobs j ON j.version_id=v.id WHERE v.state='draft'
            AND (v.created_by=%s OR d.organization_id=ANY(%s))
            AND (%s OR d.organization_id=ANY(%s))
            ORDER BY CASE
                WHEN v.processing_status IN ('ready','partial','stored_only')
                AND (j.state IS NULL OR j.state NOT IN ('queued','running')) THEN 0
                WHEN COALESCE((v.capabilities->'naming_review'->>'needs_review')::boolean,false) THEN 1
                ELSE 2 END,
            v.created_at ASC,v.id ASC LIMIT %s OFFSET %s""",
            (
                *scope,
                limit + 1,
                offset,
            ),
        ).fetchall()
        for row in rows:
            row['warnings'] = public_warnings(row['warnings'])
            if title_quality_issue(row['title']) and not (row['naming_review'] or {}).get('reviewed_by'):
                row['naming_review'] = {"state": "review", "needs_review": True,
                                        "reason": "文件类型名称过于笼统，请人工核对"}
        return {
            "total": total,
            "reviewable_total": reviewable_total,
            "items": rows[:limit],
            "has_more": len(rows) > limit,
            "next_offset": offset + limit if len(rows) > limit else None,
        }


def set_grants(db, principal, document_id, body):
    with db.connection(write=True) as conn:
        doc = managed_document(conn, principal, document_id)
        check_revision(doc, body.expected_revision)
        ids = sorted(set(body.principal_ids))
        found = conn.execute(
            "SELECT id FROM principals WHERE active=true AND id=ANY(%s)", (ids,)
        ).fetchall()
        if len(found) != len(ids):
            raise KBError("INVALID_ARGUMENT", "授权对象必须是有效身份。", 422)
        conn.execute("DELETE FROM document_grants WHERE document_id=%s", (document_id,))
        for person in ids:
            conn.execute("INSERT INTO document_grants VALUES (%s,%s)", (document_id, person))
        conn.execute("UPDATE documents SET revision=revision+1 WHERE id=%s", (document_id,))
        audit(
            conn,
            principal,
            document_id,
            None,
            "set_grants",
            principal_ids=ids,
            revision=doc["revision"] + 1,
        )
        return {"document_id": document_id, "revision": doc["revision"] + 1, "principal_ids": ids}


def delete_document(db, settings, principal, document_id, body):
    """Irreversible removal: rows purged, the original blob quarantined for later disposal.

    Permission mirrors the approval boundary (unit approver) plus system admins (is_admin).
    A published version is withdrawn first inside the same transaction, so the search pool
    clears atomically. Audit rows survive the delete - they reference a gone document but are
    kept intentionally as the only trace of who removed what.
    """
    with db.connection(write=True) as conn:
        doc = conn.execute(
            """SELECT id,title,organization_id,revision,active_version_id,created_by
            FROM documents WHERE id=%s FOR UPDATE""",
            (document_id,),
        ).fetchone()
        if not doc or not (principal.publisher(doc["organization_id"]) or principal.is_admin):
            not_found()
        check_revision(doc, body.expected_revision)
        blob_rows = conn.execute(
            """SELECT u.blob_key,u.filename FROM versions v JOIN uploads u ON u.id=v.upload_id
            WHERE v.document_id=%s""",
            (document_id,),
        ).fetchall()
        titles = [row["filename"] for row in blob_rows]
        for table, column in (("chunks", "version_id"), ("jobs", "version_id")):
            conn.execute(
                f"DELETE FROM {table} WHERE {column} IN (SELECT id FROM versions WHERE document_id=%s)",
                (document_id,),
            )
        conn.execute(
            """DELETE FROM file_links WHERE parent_version_id IN
            (SELECT id FROM versions WHERE document_id=%s)
            OR child_version_id IN (SELECT id FROM versions WHERE document_id=%s)""",
            (document_id, document_id),
        )
        conn.execute("DELETE FROM document_grants WHERE document_id=%s", (document_id,))
        conn.execute("UPDATE documents SET active_version_id=NULL WHERE id=%s", (document_id,))
        # Audit rows are the deletion trace; detach them from the doomed versions/documents
        # instead of deleting, so "who removed what" survives the purge.
        conn.execute(
            """UPDATE audit_events SET version_id=NULL, document_id=NULL
            WHERE document_id=%s""",
            (document_id,),
        )
        conn.execute("DELETE FROM versions WHERE document_id=%s", (document_id,))
        conn.execute("DELETE FROM documents WHERE id=%s", (document_id,))
        # The delete audit row is written after the purge with detached IDs: it is the trace of
        # "who removed what", and must not reference rows that no longer exist.
        quarantine = settings.storage_root / "quarantine"
        quarantine.mkdir(parents=True, exist_ok=True)
        moved = []
        for row in blob_rows:
            if not row["blob_key"]:
                continue
            source = settings.storage_root / row["blob_key"]
            if source.exists():
                target = quarantine / f"{document_id}-{row['blob_key']}"
                source.rename(target)
                moved.append(target.name)
                # Written after the purge with detached IDs: the delete trace must not reference rows
                # that no longer exist.
                conn.execute(
                    """INSERT INTO audit_events(actor_id,document_id,version_id,action,details)
                    VALUES (%s,NULL,NULL,'delete_document',%s)""",
                    (
                        principal.id,
                        Jsonb(
                            {
                                "title": doc["title"],
                                "organization_id": doc["organization_id"],
                                "filenames": titles,
                                "quarantined": moved,
                            }
                        ),
                    ),
                )
        return {
            "document_id": document_id,
            "title": doc["title"],
            "deleted_versions": len(blob_rows),
            "quarantined": moved,
        }


def version_history(db, principal, document_id, limit, offset):
    with db.connection() as conn:
        managed_document(conn, principal, document_id)
        rows = conn.execute(
            """SELECT id AS version_id,state,processing_status,source_version,
            base_revision,published_at,created_at FROM versions WHERE document_id=%s
            ORDER BY created_at DESC,id LIMIT %s OFFSET %s""",
            (document_id, limit + 1, offset),
        ).fetchall()
        return {
            "items": rows[:limit],
            "has_more": len(rows) > limit,
            "next_offset": offset + limit if len(rows) > limit else None,
        }


def audit_history(db, principal, document_id, limit, offset):
    with db.connection() as conn:
        managed_document(conn, principal, document_id)
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE document_id=%s ORDER BY id LIMIT %s OFFSET %s",
            (document_id, limit + 1, offset),
        ).fetchall()
        return {
            "items": rows[:limit],
            "has_more": len(rows) > limit,
            "next_offset": offset + limit if len(rows) > limit else None,
        }
