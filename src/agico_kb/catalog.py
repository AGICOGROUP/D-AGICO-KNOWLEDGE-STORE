from .auth import DEFAULT_VISIBLE_ORGANIZATIONS
from .errors import not_found
from .parse_notices import public_warnings

ORGANIZATION_ORDER = (
    "general-group", "business-intelligence", "baiste", "baichuang", "xingyuan",
    "aijike", "jimuke", "company-common",
)


def ordered_organizations(rows):
    ranks = {key: index for index, key in enumerate(ORGANIZATION_ORDER)}
    ordered = sorted(rows, key=lambda row: (ranks.get(row["id"], len(ranks)), row["id"]))
    return [dict(row, name="公共知识库") if row["id"] == "company-common" else row
            for row in ordered]


def can_read_document(conn, principal, doc):
    if not principal.can_access_department(doc['organization_id']):
        return False
    if principal.publisher(doc["organization_id"]) or doc["created_by"] == principal.id:
        return True
    if doc["visibility"] == "department" and doc["organization_id"] in principal.memberships:
        return True
    return bool(
        conn.execute(
            "SELECT 1 FROM document_grants WHERE document_id=%s AND principal_id=%s",
            (doc["id"], principal.id),
        ).fetchone()
    )


def get_version(conn, principal, version_id, history=False):
    row = conn.execute(
        """SELECT v.*, d.organization_id, d.category_id, d.title,
        d.visibility, d.created_by AS document_owner, d.active_version_id, d.revision,
        d.product, d.model, d.project, d.business_date,
        u.filename, u.size, u.sha256, u.blob_key
        FROM versions v JOIN documents d ON d.id=v.document_id
        JOIN uploads u ON u.id=v.upload_id WHERE v.id=%s""",
        (version_id,),
    ).fetchone()
    if row is None or not principal.can_access_department(row['organization_id']):
        not_found()
    publisher = principal.publisher(row["organization_id"])
    if row["state"] == "draft":
        allowed = publisher or row["created_by"] == principal.id
    elif row["state"] == "published" and row["active_version_id"] == row["id"]:
        doc = {**row, "id": row["document_id"], "created_by": row["document_owner"]}
        allowed = can_read_document(conn, principal, doc)
    else:
        allowed = history and publisher
    if not allowed:
        not_found()
    return row


def version_result(row):
    return {
        k: row[k]
        for k in [
            "document_id",
            "processing_status",
            "state",
            "revision",
            "filename",
            "size",
            "sha256",
            "publication_mode",
            "organization_id",
            "category_id",
            "title",
            "visibility",
            "product",
            "model",
            "project",
            "business_date",
            "base_revision",
            "source_version",
            "capabilities",
            "warnings",
        ]
    } | {"version_id": row["id"], "warnings": public_warnings(row['warnings'])}


def catalog(db, principal):
    with db.connection() as conn:
        return {
            'default_visible_organizations': list(DEFAULT_VISIBLE_ORGANIZATIONS) if principal.account_role else [],
            "organizations": ordered_organizations(conn.execute(
                "SELECT * FROM organizations WHERE id=ANY(%s) ORDER BY id",
                (list(principal.memberships),),
            ).fetchall()),
            "categories": conn.execute("SELECT * FROM categories ORDER BY id").fetchall(),
        }


def list_documents(db, principal, limit, offset, managed=False):
    with db.connection() as conn:
        if managed:
            rows = conn.execute(
                """SELECT id AS document_id,active_version_id AS version_id,title,
                organization_id,category_id,revision FROM documents WHERE organization_id=ANY(%s)
                ORDER BY created_at,id LIMIT %s OFFSET %s""",
                ([k for k in principal.memberships if principal.publisher(k)], limit + 1, offset),
            ).fetchall()
            return {
                "items": rows[:limit],
                "has_more": len(rows) > limit,
                "next_offset": offset + limit if len(rows) > limit else None,
            }
        rows = conn.execute(
            """SELECT d.id AS document_id, d.active_version_id AS version_id,
            d.title,d.organization_id,d.category_id,d.revision,v.publication_mode,
            v.processing_status FROM documents d JOIN versions v ON v.id=d.active_version_id
            WHERE v.state='published' AND
            (d.created_by=%s OR d.organization_id=ANY(%s)
             OR (d.visibility='department' AND d.organization_id=ANY(%s))
             OR EXISTS(SELECT 1 FROM document_grants g WHERE g.document_id=d.id AND g.principal_id=%s))
            AND (%s OR d.organization_id=ANY(%s))
            ORDER BY d.created_at,d.id LIMIT %s OFFSET %s""",
            (
                principal.id,
                [k for k in principal.memberships if principal.publisher(k)],
                list(principal.memberships),
                principal.id,
                not principal.department_restricted,
                list(principal.memberships),
                limit + 1,
                offset,
            ),
        ).fetchall()
        return {
            "items": rows[:limit],
            "has_more": len(rows) > limit,
            "next_offset": offset + limit if len(rows) > limit else None,
        }
