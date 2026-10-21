"""Private project feedback from every user, tracked by superadministrators."""
from typing import Literal

from fastapi import APIRouter, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .errors import KBError

Kind = Literal['problem', 'request', 'suggestion']
Status = Literal['new', 'planned', 'working', 'done', 'declined']


class CreateFeedback(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    kind: Kind
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=6000)
    area: str = Field(default='', max_length=150)


class UpdateFeedback(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    status: Status
    reply: str = Field(default='', max_length=4000)
    expected_revision: int = Field(ge=0)


SELECT = """SELECT f.*,COALESCE(a.email,p.name) AS author FROM feedback f
    JOIN principals p ON p.id=f.author_id LEFT JOIN account_users a ON a.principal_id=f.author_id"""


def router(db):
    routes = APIRouter()

    @routes.get('/v1/feedback')
    def list_feedback(request: Request, kind: Kind | None = None, status: Status | None = None,
                      scope: Literal['all', 'mine'] = 'all',
                      limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        principal = request.state.principal
        all_users = principal.is_admin and scope == 'all'
        with db.connection() as conn:
            counts = conn.execute("""SELECT count(*) AS total,
                count(*) FILTER (WHERE status='new') AS new,
                count(*) FILTER (WHERE status='planned') AS planned,
                count(*) FILTER (WHERE status='working') AS working,
                count(*) FILTER (WHERE status='done') AS done,
                count(*) FILTER (WHERE status='declined') AS declined
                FROM feedback WHERE %s OR author_id=%s""", (all_users, principal.id)).fetchone()
            rows = conn.execute(SELECT + """ WHERE (%s OR f.author_id=%s)
                AND (%s::text IS NULL OR f.kind=%s) AND (%s::text IS NULL OR f.status=%s)
                ORDER BY f.created_at DESC,f.id DESC LIMIT %s OFFSET %s""",
                (all_users, principal.id, kind, kind, status, status, limit+1, offset)).fetchall()
        return {'items':rows[:limit], 'has_more':len(rows)>limit, 'counts':counts,
                'can_manage':principal.is_admin, 'scope':'all' if all_users else 'mine'}

    @routes.post('/v1/feedback', status_code=201)
    def create_feedback(body: CreateFeedback, request: Request):
        principal = request.state.principal
        with db.connection(write=True) as conn:
            row = conn.execute("""INSERT INTO feedback(author_id,kind,title,description,area)
                VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                (principal.id, body.kind, body.title, body.description, body.area)).fetchone()
            conn.execute("INSERT INTO feedback_events(feedback_id,actor_id,action) VALUES (%s,%s,'submitted')",
                (row['id'], principal.id))
            return conn.execute(SELECT+' WHERE f.id=%s', (row['id'],)).fetchone()

    @routes.patch('/v1/feedback/{feedback_id}')
    def update_feedback(feedback_id: int, body: UpdateFeedback, request: Request):
        principal = request.state.principal
        if not principal.is_admin:
            raise KBError('FORBIDDEN', '只有开发人员可以跟进和回复反馈。', 403)
        with db.connection(write=True) as conn:
            row = conn.execute('SELECT * FROM feedback WHERE id=%s FOR UPDATE', (feedback_id,)).fetchone()
            if not row:
                raise KBError('NOT_FOUND', '这条反馈不存在。', 404)
            if row['revision'] != body.expected_revision:
                raise KBError('VERSION_CONFLICT', '反馈已被更新，请刷新后再保存。')
            conn.execute("""UPDATE feedback SET status=%s,reply=%s,handled_by=%s,
                revision=revision+1,updated_at=now() WHERE id=%s""",
                (body.status, body.reply, principal.id, feedback_id))
            conn.execute("INSERT INTO feedback_events(feedback_id,actor_id,action,detail) VALUES (%s,%s,'updated',%s)",
                (feedback_id, principal.id, Jsonb({'previous_status':row['status'],
                    'status':body.status, 'previous_reply':row['reply'], 'reply':body.reply})))
            return conn.execute(SELECT+' WHERE f.id=%s', (feedback_id,)).fetchone()

    return routes
