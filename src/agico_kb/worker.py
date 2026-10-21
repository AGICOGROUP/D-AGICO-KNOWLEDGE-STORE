"""Bounded concurrent parsing with leased and fenced PostgreSQL jobs."""

import hashlib
import json
import logging
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from . import naming
from .chunking import split_for_embedding
from .config import Settings
from .db import Database
from .embeddings import LocalEmbedding, vector_literal
from .failures import log_failure
from .ingestion import PARSER_VERSION, Chunk, Parsed
from .tokenizer import segmented
from .vision import VisionDeferred

logger = logging.getLogger(__name__)
WORKER_LEASE_SECONDS = 120
WORKER_HEARTBEAT_SECONDS = 30
# OCR-heavy work must leave capacity for small Office/text uploads. This is a
# global database limit, so multiple worker processes cannot bypass it.
_HEAVY_JOB_SQL = "(lower(u.filename) LIKE '%%.pdf' OR (u.size>8388608 AND lower(u.filename) ~ '\\.(pptx?|png|jpe?g|tiff?|bmp)$'))"


def _native_checkpoint_matches(saved, current):
    """The v6 paper tile-size optimisation does not invalidate v5 OCR content.

    Accept only the same source hash and this explicitly compatible revision;
    earlier parser revisions still require a fresh parse.
    """
    return saved == current or (
        isinstance(saved, str) and isinstance(current, str)
        and current.endswith('native-v6-ocr-paper')
        and saved == current[:64] + 'native-v5-ocr-prose-v2'
    )


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
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                         (self.settings.schema + ":worker-slots",))
            running = conn.execute(
                "SELECT count(*) AS n FROM jobs WHERE state='running' AND lease_until>now()"
            ).fetchone()["n"]
            if running >= max(1, min(5, self.settings.worker_concurrency)):
                return None
            heavy_running = conn.execute(
                f"""SELECT count(*) AS n FROM jobs j JOIN versions v ON v.id=j.version_id
                JOIN uploads u ON u.id=v.upload_id
                WHERE j.state='running' AND j.lease_until>now() AND {_HEAVY_JOB_SQL}"""
            ).fetchone()["n"]
            heavy_full = heavy_running >= max(1, min(2, self.settings.worker_concurrency))
            row = conn.execute(
                f"""SELECT j.*,u.blob_key,u.filename,u.size FROM jobs j JOIN versions v ON v.id=j.version_id
                JOIN uploads u ON u.id=v.upload_id WHERE j.attempts<%s
                AND COALESCE((v.capabilities->'naming_review'->>'needs_review')::boolean,false)=false AND
                ((j.state='queued' AND j.next_attempt_at<=now()) OR (j.state='running' AND j.lease_until<now()))
                AND (NOT %s OR NOT {_HEAVY_JOB_SQL})
                ORDER BY v.created_at,j.created_at,j.id FOR UPDATE OF j SKIP LOCKED LIMIT 1""",
                (self.settings.worker_max_attempts, heavy_full),
            ).fetchone()
            if not row:
                return None
            generation = uuid4()
            conn.execute(
                "UPDATE jobs SET state='running',generation=%s,attempts=attempts+1,lease_until=now()+make_interval(secs=>%s) WHERE id=%s",
                (
                    generation,
                    WORKER_LEASE_SECONDS,
                    row["id"],
                ),
            )
        # No transaction holds a job lock while waiting for a version lock.
        with self.db.connection(write=True) as conn:
            conn.execute(
                "UPDATE versions SET processing_status='processing' WHERE id=%s AND active_generation IS NULL AND state<>'withdrawn' AND EXISTS(SELECT 1 FROM jobs WHERE version_id=versions.id AND state='running' AND generation=%s)",
                (row["version_id"], generation),
            )
        return {**row, "generation": generation, "attempts": row["attempts"] + 1}

    def renew_lease(self, job_id, generation):
        """Only a live executor fence may extend a short crash-recovery lease."""
        with self.db.connection(write=True) as conn:
            changed = conn.execute(
                """UPDATE jobs SET lease_until=now()+make_interval(secs=>%s)
                WHERE id=%s AND generation=%s AND state='running' AND lease_until>now()
                RETURNING id""", (WORKER_LEASE_SECONDS, job_id, generation),
            ).fetchone()
            return bool(changed)

    def release_interrupted(self, job_id, generation):
        """Requeue a stopped execution by its fence; never change parsed content."""
        with self.db.connection(write=True) as conn:
            ref = conn.execute("SELECT version_id FROM jobs WHERE id=%s", (job_id,)).fetchone()
            if not ref:
                return False
            conn.execute("SELECT id FROM versions WHERE id=%s FOR UPDATE", (ref["version_id"],))
            changed = conn.execute(
                """UPDATE jobs SET state='queued',generation=NULL,lease_until=NULL,
                attempts=greatest(attempts-1,0),next_attempt_at=now(),last_error='执行器重载，自动重新排队。'
                WHERE id=%s AND state='running' AND generation=%s RETURNING id""",
                (job_id, generation),
            ).fetchone()
            if changed:
                conn.execute(
                    """UPDATE versions SET processing_status='queued'
                    WHERE id=%s AND active_generation IS NULL AND state<>'withdrawn'""",
                    (ref["version_id"],),
                )
            return bool(changed)

    def retry(self, version_id, *, preserve_progress=False):
        if not preserve_progress:
            self._checkpoint_path(version_id).unlink(missing_ok=True)
            self._clear_segments(version_id)
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
            conn.execute(
                """UPDATE versions SET warnings=COALESCE((SELECT jsonb_agg(w)
                    FROM jsonb_array_elements_text(warnings) AS item(w)
                    WHERE w NOT LIKE '视觉识别每日额度已用完%%'), '[]'::jsonb)
                    WHERE id=%s""", (version_id,),
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
        from .vision import enrich_excel, enrich_image, enrich_ppt, enrich_word

        if not needs_conversion(claim["filename"]):
            parsed = self._parse(claim)
            if claim["filename"].lower().endswith(".docx"):
                enrich_word(parsed, self.settings.storage_root / claim["blob_key"], self.settings)
            elif claim["filename"].lower().endswith(".xlsx"):
                enrich_excel(parsed, self.settings.storage_root / claim["blob_key"], self.settings)
            elif claim["filename"].lower().endswith(".pptx"):
                enrich_ppt(parsed, self.settings.storage_root / claim["blob_key"], self.settings)
            elif claim["filename"].lower().endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")):
                enrich_image(parsed, self.settings.storage_root / claim["blob_key"], self.settings)
            return parsed
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
                parsed = self._parse(claim, blob_key=temp_key, filename=converted_name)
                if converted_name.lower().endswith(".docx"):
                    enrich_word(parsed, self.settings.storage_root / temp_key, self.settings)
                elif converted_name.lower().endswith(".xlsx"):
                    enrich_excel(parsed, self.settings.storage_root / temp_key, self.settings)
                elif converted_name.lower().endswith(".pptx"):
                    enrich_ppt(parsed, self.settings.storage_root / temp_key, self.settings)
                return parsed
            finally:
                (self.settings.storage_root / temp_key).unlink(missing_ok=True)

    def _pdf_page_count(self, claim):
        import pymupdf

        with pymupdf.open(self.settings.storage_root / claim["blob_key"]) as doc:
            return doc.page_count

    def _parse_segmented(self, claim, fingerprint=None):
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
        completed_pages = 0
        for start in range(1, page_count + 1, segment):
            end = min(start + segment - 1, page_count)
            self._progress(claim, {"stage": "native", "page": start, "end_page": end,
                                   "pages": page_count, "completed_pages": completed_pages})
            checkpoint = self._checkpoint_path(claim["version_id"]).with_name(
                f"{UUID(str(claim['version_id']))}-{start}-{end}.json")
            try:
                parsed = None
                if fingerprint and checkpoint.exists():
                    try:
                        data = json.loads(checkpoint.read_text("utf-8"))
                        if _native_checkpoint_matches(data.get("fingerprint"), fingerprint):
                            parsed = Parsed([Chunk(**c) for c in data["chunks"]], data["status"], data["warnings"])
                    except (OSError, ValueError, KeyError, TypeError):
                        pass
                if parsed is None:
                    try:
                        parsed = self._parse(claim, first_page=start, last_page=end)
                    except ValueError:
                        # A transient local subprocess failure must not turn a
                        # readable page range into a permanent missing-content
                        # notice. One fresh retry is bounded; a second failure
                        # follows the existing partial-result path below.
                        logger.warning('PDF page range %s-%s interrupted; retrying once.', start, end)
                        parsed = self._parse(claim, first_page=start, last_page=end)
                    if fingerprint:
                        checkpoint.parent.mkdir(parents=True, exist_ok=True)
                        checkpoint.write_text(json.dumps({"fingerprint": fingerprint, **asdict(parsed)}, ensure_ascii=False), "utf-8")
            except ValueError:
                failures += 1
                warnings.append(
                    f"第 {start}-{min(start + segment - 1, page_count)} 页解析未完成，该范围请核对原文件。"
                )
                continue
            completed_pages += end - start + 1
            merged.extend(parsed.chunks)
            warnings.extend(parsed.warnings)
            if parsed.status != "ready":
                failures += 1
        if not merged:
            raise ValueError("分段解析后仍未取得可读取文字，原文件保留。")
        status = "ready"
        if failures:
            status = "partial"
        return Parsed(merged, status, list(dict.fromkeys(warnings)))

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

    def _checkpoint_path(self, version_id):
        return self.settings.storage_root / ".processing" / f"{UUID(str(version_id))}.json"

    def _clear_segments(self, version_id):
        folder = self._checkpoint_path(version_id).parent
        for path in folder.glob(f"{UUID(str(version_id))}-*.json"):
            path.unlink(missing_ok=True)

    def _progress(self, claim, value):
        with self.db.connection(write=True) as conn:
            conn.execute(
                """UPDATE versions SET capabilities=jsonb_set(capabilities,'{ingestion_progress}',%s)
                WHERE id=%s AND state<>'withdrawn' AND EXISTS (
                    SELECT 1 FROM jobs WHERE id=%s AND state='running'
                    AND generation=%s AND lease_until>now())""",
                (Jsonb(value), claim["version_id"], claim["id"], claim["generation"]),
            )

    def _pdf_base(self, claim):
        source = self.settings.storage_root / claim["blob_key"]
        fingerprint = hashlib.sha256(source.read_bytes()).hexdigest() + PARSER_VERSION
        checkpoint = self._checkpoint_path(claim["version_id"])
        if checkpoint.exists():
            try:
                data = json.loads(checkpoint.read_text("utf-8"))
                if _native_checkpoint_matches(data.get("fingerprint"), fingerprint):
                    return Parsed([Chunk(**c) for c in data["chunks"]], data["status"], data["warnings"])
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("Discarding incomplete native checkpoint; parsing the original again.")
        self._progress(claim, {"stage": "native"})
        try:
            parsed = self._parse_segmented(claim, fingerprint) if self._pdf_page_count(claim) >= 16 else self._parse_with_conversion(claim)
        except ValueError as error:
            if "解析超时" not in str(error):
                raise
            parsed = self._parse_segmented(claim, fingerprint)
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(json.dumps({"fingerprint": fingerprint, **asdict(parsed)}, ensure_ascii=False), "utf-8")
        self._clear_segments(claim["version_id"])
        return parsed

    def run_claim(self, claim):
        with self.db.connection() as conn:
            if not self._valid(conn, claim):
                return False
        try:
            if claim["filename"].lower().endswith(".pdf"):
                from .vision import enrich_pdf

                parsed = self._pdf_base(claim)
                enrich_pdf(parsed, self.settings.storage_root / claim["blob_key"], self.settings,
                           progress=lambda value: self._progress(claim, value))
            else:
                parsed = self._parse_with_conversion(claim)
            self._progress(claim, {"stage": "embedding", "chunks": len(parsed.chunks)})
            if parsed.status == "failed":
                raise ValueError("文件未提取到可读取文字，原文件保留。")
            vectors = None
            identity = None
            if parsed.chunks:
                try:
                    if self.embedder is None:
                        self.embedder = LocalEmbedding(self.settings)
                    if tokenizer := getattr(self.embedder, "tokenizer", None):
                        parsed.chunks = split_for_embedding(parsed.chunks, tokenizer)
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
                    capabilities=(capabilities-'ingestion_progress') || %s::jsonb,model_identity=%s,
                    publication_mode=CASE WHEN state='published' THEN
                        CASE WHEN %s='ready' THEN 'content' ELSE 'file_only' END
                        ELSE publication_mode END WHERE id=%s""",
                    (
                        claim["generation"],
                        parsed.status,
                        Jsonb(parsed.warnings),
                        Jsonb(
                            {
                                "file": True,
                                "text": bool(parsed.chunks),
                                "vector": vectors is not None,
                                "parser": PARSER_VERSION,
                            }
                        ),
                        identity,
                        parsed.status,
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
            self._checkpoint_path(claim["version_id"]).unlink(missing_ok=True)
            return True
        except VisionDeferred as error:
            # This is scheduled continuation, not a parser failure. Successful visual
            # regions remain in the project cache; the next pass advances past them.
            with self.db.connection(write=True) as conn:
                conn.execute("SELECT id FROM versions WHERE id=%s FOR UPDATE", (claim["version_id"],))
                if not self._valid(conn, claim):
                    return False
                conn.execute(
                    """UPDATE jobs SET state='queued',lease_until=NULL,last_error=%s,
                    attempts=GREATEST(0,attempts-1),
                    next_attempt_at=now()+(%s * interval '1 second') WHERE id=%s""",
                    (str(error), error.delay_seconds, claim["id"]),
                )
                conn.execute(
                    """UPDATE versions SET processing_status='queued',
                    warnings=CASE WHEN warnings @> %s::jsonb THEN warnings
                        ELSE warnings || %s::jsonb END WHERE id=%s""",
                    (Jsonb([str(error)]), Jsonb([str(error)]), claim["version_id"]),
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
                terminal = getattr(error, 'terminal', False) or claim["attempts"] >= self.settings.worker_max_attempts
                conn.execute(
                    """UPDATE jobs SET state=%s,last_error=%s,lease_until=NULL,
                    next_attempt_at=now()+interval '10 seconds' WHERE id=%s""",
                    ("failed" if terminal else "queued", message, claim["id"]),
                )
                conn.execute(
                    """UPDATE versions SET processing_status=CASE WHEN active_generation IS NULL OR state='draft' THEN %s ELSE processing_status END,
                    warnings=warnings || %s::jsonb WHERE id=%s""",
                    ("failed" if terminal else "queued", Jsonb([message]), claim["version_id"]),
                )
            log_failure(
                self.settings,
                "parse_failed" if terminal else "parse_retry",
                message,
                version_id=claim["version_id"],
                filename=claim["filename"],
                attempt=claim["attempts"],
                error=f"{type(error).__name__}: {error}",
            )
            return True

    def run_once(self):
        claim = self.claim()
        return self.run_claim(claim) if claim else False

    def purge_expired_quarantine(self, retention_days: int = 30):
        """Permanently remove quarantined blobs older than the retention window.

        Called from the daemon loop once a day; deletion errors never kill the worker.
        """
        from datetime import UTC, datetime, timedelta

        quarantine = self.settings.storage_root / "quarantine"
        if not quarantine.exists():
            return
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        removed = 0
        for item in quarantine.iterdir():
            try:
                if item.is_file() and datetime.fromtimestamp(item.stat().st_mtime, UTC) < cutoff:
                    item.unlink()
                    removed += 1
            except OSError:
                logger.warning("Quarantine cleanup could not remove %s; will retry tomorrow.", item)
        if removed:
            logger.info(
                "Quarantine purge removed %d blob(s) older than %d days.", removed, retention_days
            )


class WorkerPool:
    """Claim serially in submission order; execute at most five files concurrently."""

    def __init__(self, db, settings, worker_factory=Worker):
        count = max(1, min(5, settings.worker_concurrency))
        self.db, self.settings = db, settings
        self.available = [worker_factory(db, settings) for _ in range(count)]
        self.active = {}
        self.executor = ThreadPoolExecutor(max_workers=count, thread_name_prefix="kb-parse")
        self.naming_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kb-name")
        self.naming_future = None
        self.last_heartbeat = time.monotonic()

    def tick(self):
        if self.naming_future is not None and self.naming_future.done():
            try:
                self.naming_future.result()
            except Exception:
                logger.exception("Naming batch interrupted; its lease will expire for retry.")
            self.naming_future = None
        if self.naming_future is None:
            self.naming_future = self.naming_executor.submit(naming.process_pending, self.db, self.settings)
        for future, (worker, claim) in list(self.active.items()):
            if future.done():
                try:
                    future.result()
                except Exception:
                    worker.release_interrupted(claim["id"], claim["generation"])
                    logger.exception("Parsing slot failed; job returned to queue.")
                del self.active[future]
                self.available.append(worker)
        if time.monotonic() - self.last_heartbeat >= WORKER_HEARTBEAT_SECONDS:
            for worker, claim in self.active.values():
                worker.renew_lease(claim['id'], claim['generation'])
            self.last_heartbeat = time.monotonic()
        while self.available:
            worker = self.available[-1]
            claim = worker.claim()
            if not claim:
                break
            self.available.pop()
            self.active[self.executor.submit(worker.run_claim, claim)] = (worker, claim)

    def close(self):
        self.naming_executor.shutdown(wait=True)
        self.executor.shutdown(wait=True)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--recover-interrupted-job", type=UUID)
    parser.add_argument("--generation", type=UUID)
    args = parser.parse_args()
    settings = Settings.from_env()
    db = Database(settings)
    worker = Worker(db, settings)
    if args.recover_interrupted_job:
        if not args.generation:
            parser.error("--generation is required for fenced recovery")
        try:
            print("requeued" if worker.release_interrupted(args.recover_interrupted_job, args.generation) else "unchanged")
        finally:
            db.close()
        return
    pool = WorkerPool(db, settings)
    last_purge = None
    try:
        while True:
            try:
                if last_purge != time.strftime("%Y-%m-%d"):
                    worker.purge_expired_quarantine()
                    last_purge = time.strftime("%Y-%m-%d")
                pool.tick()
                time.sleep(1)
            except Exception:  # noqa: BLE001 - daemon retries database/process availability failures
                logger.error("Worker temporarily unavailable; retrying without publishing content.")
                time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        pool.close()
        db.close()


if __name__ == "__main__":
    main()
