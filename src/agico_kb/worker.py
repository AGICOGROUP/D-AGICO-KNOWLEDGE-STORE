"""Single worker, bounded subprocess parsing, leased and fenced PostgreSQL jobs."""

import json
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb

from .config import Settings
from .db import Database
from .embeddings import LocalEmbedding, vector_literal
from .ingestion import Chunk, Parsed
from .tokenizer import segmented

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, db, settings, embedder=None):
        self.db, self.settings, self.embedder = db, settings, embedder

    def claim(self):
        with self.db.connection() as conn:
            exhausted = conn.execute(
                "SELECT id,version_id FROM jobs WHERE state='running' AND lease_until<now() AND attempts>=%s",
                (self.settings.worker_max_attempts,),
            ).fetchall()
        for stale in exhausted:
            with self.db.connection(write=True) as conn:
                conn.execute(
                    "SELECT id FROM versions WHERE id=%s FOR UPDATE", (stale["version_id"],)
                )
                changed = conn.execute(
                    "UPDATE jobs SET state='failed',last_error='处理多次中断，需手动重试。' WHERE id=%s AND state='running' AND lease_until<now() AND attempts>=%s RETURNING id",
                    (stale["id"], self.settings.worker_max_attempts),
                ).fetchone()
                if changed:
                    conn.execute(
                        "UPDATE versions SET processing_status=CASE WHEN active_generation IS NULL THEN 'failed' ELSE processing_status END WHERE id=%s",
                        (stale["version_id"],),
                    )
        with self.db.connection(write=True) as conn:
            row = conn.execute(
                """SELECT j.*,u.blob_key,u.filename,u.size FROM jobs j JOIN versions v ON v.id=j.version_id
                JOIN uploads u ON u.id=v.upload_id WHERE j.attempts<%s AND
                ((j.state='queued' AND j.next_attempt_at<=now()) OR (j.state='running' AND j.lease_until<now()))
                ORDER BY j.created_at FOR UPDATE OF j SKIP LOCKED LIMIT 1""",
                (self.settings.worker_max_attempts,),
            ).fetchone()
            if not row:
                return None
            generation = uuid4()
            conn.execute(
                "UPDATE jobs SET state='running',generation=%s,attempts=attempts+1,lease_until=now()+make_interval(secs=>%s) WHERE id=%s",
                (generation, max(60, self.settings.parse_timeout_seconds + 300), row["id"]),
            )
        # No transaction holds a job lock while waiting for a version lock.
        with self.db.connection(write=True) as conn:
            conn.execute(
                "UPDATE versions SET processing_status='processing' WHERE id=%s AND active_generation IS NULL AND state<>'withdrawn' AND EXISTS(SELECT 1 FROM jobs WHERE version_id=versions.id AND state='running' AND generation=%s)",
                (row["version_id"], generation),
            )
        return {**row, "generation": generation, "attempts": row["attempts"] + 1}

    def retry(self, version_id):
        with self.db.connection(write=True) as conn:
            # Lock document then version then job, consistent with withdrawal/publication.
            ref = conn.execute(
                "SELECT document_id FROM versions WHERE id=%s", (version_id,)
            ).fetchone()
            if not ref:
                raise ValueError("资料不存在")
            conn.execute("SELECT id FROM documents WHERE id=%s FOR UPDATE", (ref["document_id"],))
            version = conn.execute(
                "SELECT state FROM versions WHERE id=%s FOR UPDATE", (version_id,)
            ).fetchone()
            if version["state"] == "withdrawn":
                raise ValueError("撤下资料不可重新处理，请先恢复或提交新稿")
            conn.execute(
                "UPDATE jobs SET state='queued',generation=NULL,attempts=0,lease_until=NULL,next_attempt_at=now(),last_error=NULL WHERE version_id=%s",
                (version_id,),
            )

    def _parse(self, claim, first_page=None, last_page=None, blob_key=None, filename=None):
        blob_key = blob_key or claim["blob_key"]
        filename = filename or claim["filename"]
        self.settings.storage_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="parse-", dir=self.settings.storage_root) as folder:
            result = Path(folder) / "result.json"
            arguments = [
                sys.executable,
                "-m",
                "agico_kb.ingestion",
                str(self.settings.storage_root / blob_key),
                filename,
                str(result),
            ]
            if first_page is not None:
                arguments += [str(first_page), str(last_page or first_page)]
            try:
                completed = subprocess.run(
                    arguments,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=self.settings.timeout_for(claim["size"]),
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise ValueError("解析超时，已终止子进程。") from None
            if completed.returncode != 0 or not result.exists():
                # Do not expose parser traceback, local paths, or file content in tools.
                raise ValueError("解析失败，请检查文件格式、加密状态或损坏情况。")
            data = json.loads(result.read_text(encoding="utf-8"))
            return Parsed([Chunk(**c) for c in data["chunks"]], data["status"], data["warnings"])

    def _parse_with_conversion(self, claim):
        """Legacy Office formats: LibreOffice conversion first, pure-Python fallback second.

        The stored original is untouched; converted copies live only for this parse run. When
        LibreOffice is missing or fails, the fallback parsers still recover text so the document
        stays searchable instead of degrading to stored-only.
        """
        from .convert import CONVERTIBLE, convert_to_modern, needs_conversion
        from .fallback_parse import FALLBACKS

        if not needs_conversion(claim["filename"]):
            return self._parse(claim)
        self.settings.storage_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="convert-", dir=self.settings.storage_root
        ) as folder:
            try:
                converted, converted_name = convert_to_modern(
                    self.settings.storage_root / claim["blob_key"], claim["filename"], Path(folder)
                )
            except ValueError:
                suffix = Path(claim["filename"]).suffix.lower()
                fallback = FALLBACKS.get(suffix)
                if fallback is None:
                    raise
                parsed = Parsed()
                fallback(self.settings.storage_root / claim["blob_key"], parsed)
                if not parsed.chunks:
                    raise ValueError(
                        "兜底解析未取得文字，原文件保留；请另存为 "
                        + CONVERTIBLE[suffix]
                        + " 后重新提交。"
                    )
                parsed.status = "partial"
                return parsed
            # _parse reads from storage_root, so copy the converted file there under a temp key.
            import shutil

            temp_key = f"convert-{uuid4().hex}.blob"
            shutil.copy2(converted, self.settings.storage_root / temp_key)
            try:
                return self._parse(claim, blob_key=temp_key, filename=converted_name)
            finally:
                (self.settings.storage_root / temp_key).unlink(missing_ok=True)

    def _pdf_page_count(self, claim):
        import pymupdf

        with pymupdf.open(self.settings.storage_root / claim["blob_key"]) as doc:
            return doc.page_count

    def _parse_segmented(self, claim):
        """Large-PDF strategy: parse page ranges as separate bounded subprocesses and merge.

        A whole-document timeout kill loses everything; per-segment failures only lose pages,
        and the merged result keeps every page that did parse.
        """
        try:
            page_count = self._pdf_page_count(claim)
        except Exception as error:
            raise ValueError("解析失败，请检查文件格式、加密状态或损坏情况。") from error
        if page_count <= 0:
            raise ValueError("解析失败，请检查文件格式、加密状态或损坏情况。")
        segment = max(1, int(page_count // 8) or 1)
        merged, warnings, failures = [], [], 0
        for start in range(1, page_count + 1, segment):
            try:
                parsed = self._parse(
                    claim, first_page=start, last_page=min(start + segment - 1, page_count)
                )
            except ValueError:
                failures += 1
                warnings.append(
                    f"第 {start}-{min(start + segment - 1, page_count)} 页解析未完成，该范围请核对原文件。"
                )
                continue
            merged.extend(parsed.chunks)
            warnings.extend(parsed.warnings)
            if parsed.status != "ready":
                failures += 1
        if not merged:
            raise ValueError("分段解析后仍未取得可读取文字，原文件保留。")
        status = "ready"
        if failures or any("未" in w or "OCR" in w for w in warnings):
            status = "partial"
        return Parsed(merged, status, warnings)

    def _valid(self, conn, claim):
        row = conn.execute(
            "SELECT state,generation,lease_until>now() AS live FROM jobs WHERE id=%s FOR UPDATE",
            (claim["id"],),
        ).fetchone()
        return (
            row
            and row["state"] == "running"
            and row["generation"] == claim["generation"]
            and row["live"]
        )

    def run_claim(self, claim):
        with self.db.connection() as conn:
            if not self._valid(conn, claim):
                return False
        try:
            try:
                parsed = self._parse_with_conversion(claim)
            except ValueError as error:
                # A whole-document timeout on a big PDF is retried page-range by page-range;
                # everything that parses within its segment budget is kept.
                if "解析超时" not in str(error) or not claim["filename"].lower().endswith(".pdf"):
                    raise
                parsed = self._parse_segmented(claim)
            if parsed.status == "failed":
                raise ValueError("文件未提取到可读取文字，原文件保留。")
            vectors = None
            identity = None
            if parsed.chunks:
                try:
                    if self.embedder is None:
                        self.embedder = LocalEmbedding(self.settings)
                    vectors = self.embedder.embed([c.text for c in parsed.chunks])
                    if len(vectors) != len(parsed.chunks):
                        raise ValueError("Incomplete vectors")
                    for vector in vectors:
                        if len(vector) != self.embedder.dimension:
                            raise ValueError("Dimension mismatch")
                        vector_literal(vector)
                    identity = self.embedder.identity
                except Exception:  # noqa: BLE001 - embedding failure must preserve extracted text
                    vectors = None
                    parsed.status = "partial"
                    parsed.warnings.append("向量暂不可用，仍可通过关键词检索并读取已解析文字。")
                if any(len(c.text) > 1200 for c in parsed.chunks):
                    parsed.warnings.append(
                        "长段／表格的向量表示可能截断，完整文字保留用于关键词检索与读取。"
                    )
            with self.db.connection(write=True) as conn:
                # Version first prevents deadlock with draft withdrawal; job token fences old workers.
                conn.execute(
                    "SELECT id FROM versions WHERE id=%s FOR UPDATE", (claim["version_id"],)
                )
                if not self._valid(conn, claim):
                    return False
                for ordinal, chunk in enumerate(parsed.chunks):
                    conn.execute(
                        """INSERT INTO chunks(id,version_id,generation,ordinal,text,locator,keywords,embedding,model_identity)
                        VALUES (%s,%s,%s,%s,%s,%s,to_tsvector('simple',%s),%s::public.vector,%s)""",
                        (
                            uuid4(),
                            claim["version_id"],
                            claim["generation"],
                            ordinal,
                            chunk.text,
                            Jsonb(chunk.locator),
                            segmented(chunk.text),
                            vector_literal(vectors[ordinal]) if vectors is not None else None,
                            identity,
                        ),
                    )
                conn.execute(
                    """UPDATE versions SET active_generation=%s,processing_status=%s,warnings=%s,
                    capabilities=%s,model_identity=%s WHERE id=%s""",
                    (
                        claim["generation"],
                        parsed.status,
                        Jsonb(parsed.warnings),
                        Jsonb(
                            {
                                "file": True,
                                "text": bool(parsed.chunks),
                                "vector": vectors is not None,
                            }
                        ),
                        identity,
                        claim["version_id"],
                    ),
                )
                conn.execute(
                    "UPDATE jobs SET state='complete',lease_until=NULL,last_error=NULL WHERE id=%s",
                    (claim["id"],),
                )
                conn.execute(
                    "DELETE FROM chunks WHERE version_id=%s AND generation<>%s",
                    (claim["version_id"], claim["generation"]),
                )
            return True
        except Exception as error:  # noqa: BLE001 - bounded job boundary persists retry state
            message = (
                str(error)
                if isinstance(error, ValueError)
                else "处理暂时失败，请稍后重试；原文件保留。"
            )
            with self.db.connection(write=True) as conn:
                conn.execute(
                    "SELECT id FROM versions WHERE id=%s FOR UPDATE", (claim["version_id"],)
                )
                if not self._valid(conn, claim):
                    return False
                terminal = claim["attempts"] >= self.settings.worker_max_attempts
                conn.execute(
                    """UPDATE jobs SET state=%s,last_error=%s,lease_until=NULL,
                    next_attempt_at=now()+interval '10 seconds' WHERE id=%s""",
                    ("failed" if terminal else "queued", message, claim["id"]),
                )
                conn.execute(
                    """UPDATE versions SET processing_status=CASE WHEN active_generation IS NULL THEN %s ELSE processing_status END,
                    warnings=warnings || %s::jsonb WHERE id=%s""",
                    ("failed" if terminal else "queued", Jsonb([message]), claim["version_id"]),
                )
            return True

    def run_once(self):
        claim = self.claim()
        return self.run_claim(claim) if claim else False


def main():
    settings = Settings.from_env()
    db = Database(settings)
    worker = Worker(db, settings)
    try:
        while True:
            try:
                if not worker.run_once():
                    time.sleep(2)
            except Exception:  # noqa: BLE001 - daemon retries database/process availability failures
                logger.error("Worker temporarily unavailable; retrying without publishing content.")
                time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        db.close()


if __name__ == "__main__":
    main()
