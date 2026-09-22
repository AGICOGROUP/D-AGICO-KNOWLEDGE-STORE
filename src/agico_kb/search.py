"""Permission-filtered candidates, simple rank fusion, bounded source reads."""

import re

from psycopg.types.json import Jsonb

from .catalog import get_version
from .contracts import SearchRequest
from .embeddings import LocalEmbedding, QueryEmbedding, vector_literal
from .errors import KBError, not_found
from .tokenizer import normalize, segmented


def permission_filter(principal, request):
    clauses = [
        "v.id=d.active_version_id",
        "v.state='published'",
        "(d.created_by=%s OR d.organization_id=ANY(%s) OR (d.visibility='department' AND d.organization_id=ANY(%s)) OR EXISTS(SELECT 1 FROM document_grants g WHERE g.document_id=d.id AND g.principal_id=%s))",
    ]
    args = [
        principal.id,
        [k for k in principal.memberships if principal.publisher(k)],
        list(principal.memberships),
        principal.id,
    ]
    for field in ["organization_id", "category_id", "document_id"]:
        value = getattr(request, field)
        if value is not None:
            clauses.append(f"d.{'id' if field == 'document_id' else field}=%s")
            args.append(value)
    if request.model is not None:
        clauses.append("lower(trim(d.model))=%s")
        args.append(normalize(request.model))
    if request.business_date_from:
        clauses.append("d.business_date>=%s")
        args.append(request.business_date_from)
    if request.business_date_to:
        clauses.append("d.business_date<=%s")
        args.append(request.business_date_to)
    return " AND ".join(clauses), args


def keyword_query(text):
    stop = {
        "的",
        "了",
        "是",
        "吗",
        "呢",
        "多少",
        "什么",
        "可以",
        "这个",
        "这种",
        "我",
        "帮",
        "请",
        "公司",
        "能",
        "多高",
    }
    tokens = [
        t for t in segmented(text).split() if t not in stop and re.search(r"[\w\u4e00-\u9fff]", t)
    ]
    # Bound expression length; quotes are data escaped for websearch syntax, never SQL.
    return " OR ".join('"' + t.replace('"', "") + '"' for t in dict.fromkeys(tokens))


FIELDS = """d.id AS document_id,v.id AS version_id,d.title,d.organization_id,d.category_id,
    d.model,d.business_date,d.revision,v.source_version,v.capabilities,v.warnings,v.processing_status"""
FROM = "FROM documents d JOIN versions v ON v.document_id=d.id JOIN uploads u ON u.id=v.upload_id"


class SearchService:
    def __init__(self, db, settings):
        self.db = db
        self.settings = settings
        self.query_model = QueryEmbedding(settings)

    def close(self):
        self.query_model.close()

    def search(self, principal, request):
        where, args = permission_filter(principal, request)
        words = keyword_query(request.query)
        normalized = normalize(request.query)
        # A fixed pool keeps rank fusion stable across pages of an unchanged query/index.
        count = min(1100, request.offset + request.limit + 1) if request.mode == "files" else 1100
        warnings = []
        if request.mode == "files":
            with self.db.connection() as conn:
                rows = conn.execute(
                    f"""SELECT {FIELDS},u.filename,u.size,u.sha256,
                    CASE WHEN lower(trim(d.model))=%s THEN 2 ELSE 0 END AS rank
                    {FROM} WHERE {where} AND (%s=''
                    OR lower(normalize(d.title,NFKC)) LIKE lower(normalize(%s,NFKC))
                    OR lower(normalize(u.filename,NFKC)) LIKE lower(normalize(%s,NFKC))
                    OR lower(d.model)=%s OR EXISTS(SELECT 1 FROM chunks c WHERE c.version_id=v.id
                    AND c.generation=v.active_generation AND c.keywords @@ websearch_to_tsquery('simple',%s)))
                    ORDER BY rank DESC,d.created_at DESC,d.id LIMIT %s""",
                    [
                        normalized,
                        *args,
                        normalized,
                        "%" + request.query.strip() + "%",
                        "%" + request.query.strip() + "%",
                        normalized,
                        words,
                        count,
                    ],
                ).fetchall()
            return self._page(principal, rows, request, warnings)
        chunk_from = (
            FROM + " JOIN chunks c ON c.version_id=v.id AND c.generation=v.active_generation"
        )
        with self.db.connection() as conn:
            lexical = conn.execute(
                f"""SELECT {FIELDS},c.id AS chunk_id,left(c.text,600) AS excerpt,length(c.text)>600 AS excerpt_truncated,c.locator,
                ts_rank_cd(c.keywords,websearch_to_tsquery('simple',%s)) AS rank
                {chunk_from} WHERE {where} AND (%s='' OR c.keywords @@ websearch_to_tsquery('simple',%s))
                ORDER BY rank DESC,c.id LIMIT %s""",
                [words, *args, words, words, count],
            ).fetchall()
        semantic = []
        if request.query:
            try:
                vector, identity = self.query_model.query(request.query)
                vector = vector_literal(vector)
                with self.db.connection() as conn:
                    semantic = conn.execute(
                        f"""SELECT {FIELDS},c.id AS chunk_id,left(c.text,600) AS excerpt,length(c.text)>600 AS excerpt_truncated,c.locator,
                        c.embedding <=> %s::public.vector AS distance
                        {chunk_from} WHERE {where} AND c.model_identity=%s AND c.embedding IS NOT NULL
                        AND (c.embedding <=> %s::public.vector)<0.65
                        ORDER BY distance,c.id LIMIT %s""",
                        [vector, *args, identity, vector, count],
                    ).fetchall()
                    mismatch = conn.execute(
                        f"""SELECT 1 {chunk_from} WHERE {where}
                        AND c.embedding IS NOT NULL AND c.model_identity<>%s LIMIT 1""",
                        [*args, identity],
                    ).fetchone()
                    if mismatch:
                        warnings.append(
                            "部分资料使用其他模型版本，仅参与关键词检索，需重新索引后恢复向量匹配。"
                        )
            except Exception:  # noqa: BLE001 - model/driver failures deliberately degrade to keyword search
                warnings.append("查询向量暂不可用，本次仅使用关键词检索。")
        ranked = {}
        scores = {}
        for candidates in [lexical, semantic]:
            for rank, item in enumerate(candidates, 1):
                key = item["chunk_id"]
                ranked[key] = item
                scores[key] = scores.get(key, 0) + 1 / (60 + rank)
        rows = [ranked[key] for key in sorted(scores, key=lambda k: (-scores[k], str(k)))]
        # Only compact excerpts leave search; full semantic units are available through read.
        for item in rows:
            item.pop("rank", None)
            item.pop("distance", None)
        return self._page(principal, rows, request, warnings)

    def _page(self, principal, rows, request, warnings):
        items = []
        with self.db.connection() as conn:
            for row in rows[request.offset : request.offset + request.limit]:
                try:
                    get_version(conn, principal, row["version_id"])
                except KBError:
                    continue
                row.pop("rank", None)
                items.append(row)
        has_more = (
            len(rows) > request.offset + request.limit and request.offset + request.limit <= 1000
        )
        if request.offset + request.limit > 1000:
            warnings.append("本次候选读取已达保护上限，请缩小文件或事业部范围。")
        return {
            "items": items,
            "has_more": has_more,
            "next_offset": request.offset + request.limit if has_more else None,
            "warnings": warnings,
        }

    def read(self, principal, request):
        with self.db.connection() as conn:
            version = get_version(conn, principal, request.version_id)
            warnings = list(version["warnings"])
            start = request.offset
            if request.chunk_id:
                anchor = conn.execute(
                    "SELECT ordinal FROM chunks WHERE id=%s AND version_id=%s AND generation=%s",
                    (request.chunk_id, request.version_id, version["active_generation"]),
                ).fetchone()
                if not anchor:
                    raise KBError("VERSION_CONFLICT", "索引已更新或定位无效，请重新搜索。")
                start += anchor["ordinal"]
            rows = conn.execute(
                "SELECT id AS chunk_id,text,locator,ordinal FROM chunks WHERE version_id=%s AND generation=%s AND ordinal>=%s ORDER BY ordinal LIMIT %s",
                (request.version_id, version["active_generation"], start, request.limit + 1),
            ).fetchall()
            selected = []
            size = 0
            blocked = False
            for row in rows[: request.limit]:
                if size + len(row["text"]) > request.max_chars:
                    if not selected:
                        warnings.append(
                            "该完整段落／表格超过当前读取上限，请提高 max_chars 或获取原文件；未截断后冒充完整内容。"
                        )
                        blocked = True
                    break
                selected.append({k: v for k, v in row.items() if k != "ordinal"})
                size += len(row["text"])
            has_more = not blocked and len(rows) > len(selected)
            if not rows and not version["capabilities"]["text"]:
                warnings.append("当前版本尚无可读取正文，可获取原文件或查询处理状态。")
            return {
                "version_id": request.version_id,
                "title": version["title"],
                "source_version": version["source_version"],
                "items": selected,
                "has_more": has_more,
                "next_offset": request.offset + len(selected) if has_more else None,
                "warnings": warnings,
                "capabilities": version["capabilities"],
                "requires_file": blocked,
            }

    def context(self, principal, request):
        return self.search(
            principal,
            SearchRequest(
                query=request.query,
                category_id="background",
                organization_id=request.organization_id,
            ),
        )

    def review_chunks(self, principal, version_id):
        """Full parsed content of a pending draft, for the approver's review drawer.

        Same access rule as get_version: the unit's approver (publisher) or the author.
        """
        with self.db.connection() as conn:
            version = get_version(conn, principal, version_id)
            if version["state"] != "draft":
                raise KBError("VERSION_CONFLICT", "只有待审核草稿可以查看解析结果。")
            rows = conn.execute(
                """SELECT id AS chunk_id,ordinal,locator,text,
                (embedding IS NOT NULL) AS has_vector FROM chunks
                WHERE version_id=%s AND generation=%s ORDER BY ordinal""",
                (version_id, version["active_generation"]),
            ).fetchall()
            return {
                "version_id": version_id,
                "title": version["title"],
                "processing_status": version["processing_status"],
                "warnings": list(version["warnings"]),
                "capabilities": version["capabilities"],
                "chunk_count": len(rows),
                "items": rows,
            }

    def edit_chunk(self, principal, version_id, body):
        """Fix a wrong/untidy chunk in a pending draft. Re-derives keywords and re-embeds the
        chunk so search and AI references reflect the correction. Approver or author, draft only.
        """
        with self.db.connection(write=True) as conn:
            version = get_version(conn, principal, version_id)
            if version["state"] != "draft":
                raise KBError("VERSION_CONFLICT", "只有待审核草稿可以修改解析结果。")
            locked = conn.execute(
                "SELECT id,active_generation FROM versions WHERE id=%s FOR UPDATE", (version_id,)
            ).fetchone()
            row = conn.execute(
                """SELECT id,ordinal,locator FROM chunks WHERE id=%s AND version_id=%s
                AND generation=%s""",
                (body.chunk_id, version_id, locked["active_generation"]),
            ).fetchone()
            if not row:
                not_found()
            try:
                embedder = LocalEmbedding(self.settings)
            except Exception as error:  # offline model missing or identity pin mismatch
                raise KBError("INVALID_ARGUMENT", "向量模型不可用，无法保存修改。", 503) from error
            text = body.text.strip()
            conn.execute(
                """UPDATE chunks SET text=%s,
                keywords=to_tsvector('simple',%s), embedding=%s::public.vector,
                model_identity=%s WHERE id=%s""",
                (
                    text,
                    segmented(text),
                    vector_literal(embedder.embed([text])[0]),
                    embedder.identity,
                    body.chunk_id,
                ),
            )
            conn.execute(
                """INSERT INTO audit_events(actor_id,document_id,version_id,action,details)
                VALUES (%s,%s,%s,'edit_chunk',%s)""",
                (
                    principal.id,
                    version["document_id"],
                    version_id,
                    Jsonb(
                        {
                            "chunk_id": str(body.chunk_id),
                            "ordinal": row["ordinal"],
                            "length": len(text),
                        }
                    ),
                ),
            )
            return {"chunk_id": body.chunk_id, "ordinal": row["ordinal"], "updated": True}
