"""Persistent, permission-scoped project identities for runtime naming."""

import re
from uuid import uuid4

from psycopg.types.json import Jsonb


def _fold(value):
    return re.sub(r"[\W_]+", "", value or "", flags=re.UNICODE).casefold()


def visible_projects(conn, organization_id, creator_id, publisher=False, limit=None):
    sql = (
        """SELECT id,code,official_code,status,name,aliases,evidence,visibility,created_by
        FROM projects WHERE organization_id=%s
        AND (visibility='department' OR created_by=%s OR %s)
        ORDER BY created_at DESC"""
    )
    params = (organization_id, creator_id, publisher)
    if limit is not None:
        sql += " LIMIT %s"
        params += (limit,)
    return conn.execute(sql, params).fetchall()


def _promote(conn, row, official_code, actor_id):
    """Switch the display prefix, keeping the temporary code as a permanent alias."""
    conn.execute("UPDATE projects SET official_code=%s,status='won' WHERE id=%s",
                 (official_code, row["id"]))
    documents = conn.execute(
        "SELECT id,title,project,active_version_id FROM documents WHERE project_id=%s FOR UPDATE",
        (row["id"],),
    ).fetchall()
    for document in documents:
        previous = document["project"] or row["code"]
        title = document["title"]
        if title.startswith(previous + "_"):
            title = official_code + title[len(previous):]
        conn.execute("UPDATE documents SET title=%s,project=%s WHERE id=%s",
                     (title, official_code, document["id"]))
        conn.execute(
            """INSERT INTO audit_events(actor_id,document_id,version_id,action,details)
            VALUES (%s,%s,%s,'project_code_formalized',%s)""",
            (actor_id, document["id"], document["active_version_id"],
             Jsonb({"previous_code": previous, "official_code": official_code})),
        )
    return {**row, "official_code": official_code, "status": "won"}


def resolve(conn, organization_id, creator_id, visibility, suggestion, *, publisher=False):
    """Return (project row, reason). Allocate only for a reviewed, grounded suggestion.

    Caller holds a transaction-wide advisory lock, so concurrent batches cannot create
    two temporary IDs for the same confirmed label.
    """
    name = suggestion.get("project") or ""
    if not name:
        return None, ""
    candidates = visible_projects(conn, organization_id, creator_id, publisher)
    selected_code = suggestion.get("matched_project_code") or ""
    explicit_code = suggestion.get("official_project_code") or ""
    if selected_code:
        matches = [row for row in candidates if row["code"] == selected_code
                   or row["official_code"] == selected_code]
        if len(matches) != 1:
            return None, "已有项目编号不在可用记录中，请人工确认"
        row = matches[0]
        if _fold(name) not in {_fold(row["name"]), *(_fold(a) for a in row["aliases"])}:
            aliases = list(dict.fromkeys([*row["aliases"], name]))
            conn.execute("UPDATE projects SET aliases=%s WHERE id=%s", (Jsonb(aliases), row["id"]))
        return row, ""
    if explicit_code:
        matches = [row for row in candidates if row["official_code"] == explicit_code
                   or row["code"] == explicit_code]
        if len(matches) == 1:
            return matches[0], ""
    same_name = [row for row in candidates if _fold(name) in
                 {_fold(row["name"]), *(_fold(a) for a in row["aliases"])}]
    if len(same_name) > 1:
        return None, "存在多个同名项目，请人工确认项目编号"
    if same_name:
        row = same_name[0]
        if explicit_code and row["official_code"] not in {None, explicit_code}:
            return None, "正式项目编号与已有记录冲突，请人工确认"
        if explicit_code and row["official_code"] is None:
            row = _promote(conn, row, explicit_code, creator_id)
        return row, ""
    if explicit_code:
        code, status = explicit_code, "won"
    else:
        serial = conn.execute("SELECT nextval('opportunity_code_seq') AS n").fetchone()["n"]
        year = conn.execute("SELECT extract(year from now())::integer AS y").fetchone()["y"]
        code, status = f"OPP-{year}-{serial:06d}", "opportunity"
    return conn.execute(
        """INSERT INTO projects(id,organization_id,code,official_code,status,name,aliases,
        evidence,visibility,created_by)
        VALUES (%s,%s,%s,%s,%s,%s,'[]',%s,%s,%s)
        RETURNING id,code,official_code,status,name,aliases,evidence,visibility,created_by""",
        (uuid4(), organization_id, code, explicit_code or None, status, name,
         Jsonb({"filename_evidence": suggestion.get("evidence") or ""}), visibility, creator_id),
    ).fetchone(), ""


def confirm(conn, organization_id, creator_id, visibility, name, official_code=""):
    """Human-confirmed project identity; caller validates the title and permissions."""
    if not name:
        return None
    projects = visible_projects(conn, organization_id, creator_id, True)
    if official_code:
        match = [row for row in projects if row["official_code"] == official_code
                 or row["code"] == official_code]
        if match:
            row = match[0]
            if _fold(name) not in {_fold(row["name"]), *(_fold(a) for a in row["aliases"])}:
                aliases = list(dict.fromkeys([*row["aliases"], name]))
                conn.execute("UPDATE projects SET aliases=%s WHERE id=%s", (Jsonb(aliases), row["id"]))
            return row
    match = [row for row in projects if _fold(name) in
             {_fold(row["name"]), *(_fold(a) for a in row["aliases"])}]
    if len(match) == 1:
        row = match[0]
        if official_code and row["official_code"] not in {None, official_code}:
            raise ValueError("正式项目编号与已有记录不一致")
        if official_code and row["official_code"] is None:
            return _promote(conn, row, official_code, creator_id)
        return row
    if len(match) > 1:
        raise ValueError("存在多个同名项目，需填写正式项目编号以区分")
    return resolve(conn, organization_id, creator_id, visibility,
                   {"project": name, "official_project_code": official_code}, publisher=True)[0]
