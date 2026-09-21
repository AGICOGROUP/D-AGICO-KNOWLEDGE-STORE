from .errors import KBError, not_found
from .files import audit, submission_result


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
        if version["processing_status"] != "ready" and not body.accept_incomplete:
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
        return activate(conn, principal, doc, version, "publish")


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
        if not ref or (
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
        rows = conn.execute(
            """SELECT v.id AS version_id,v.document_id,d.title,d.organization_id,
            d.revision,v.base_revision,v.processing_status,v.created_by FROM versions v
            JOIN documents d ON d.id=v.document_id WHERE v.state='draft'
            AND (v.created_by=%s OR d.organization_id=ANY(%s))
            ORDER BY v.created_at DESC,v.id DESC LIMIT %s OFFSET %s""",
            (
                principal.id,
                [k for k in principal.memberships if principal.publisher(k)],
                limit + 1,
                offset,
            ),
        ).fetchall()
        return {
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
