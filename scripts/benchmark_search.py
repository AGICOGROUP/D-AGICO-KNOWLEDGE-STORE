"""Fresh synthetic corpus; only agico_test + a new schema, never operational data.

Run with the project Python. An optional XERJ node must be an isolated loopback
test node. The script creates a new index name and removes only that exact index.
No historical parsed content, vectors or answers are loaded.
"""

import argparse
import importlib.util
import json
import math
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from agico_kb.auth import Principal
from agico_kb.chunking import split_for_embedding
from agico_kb.config import Settings
from agico_kb.contracts import SearchRequest
from agico_kb.db import Database
from agico_kb.embeddings import LocalEmbedding, QueryEmbedding, vector_literal
from agico_kb.ingestion import parse_file
from agico_kb.search import FIELDS, FROM, SearchService, keyword_query, permission_filter
from agico_kb.tokenizer import segmented

FACTS = [
    ("seal", "AX-210 密封维护", "AX-210 标准配置最高工作温度 80 摄氏度。密封泄漏时先检查密封圈磨损，再核对轴套表面。",
     ["AX-210 密封圈磨损", "设备漏液应该先查看哪些部位"]),
    ("seal_b", "AX-210B 密封维护", "AX-210B 增强配置最高工作温度 120 摄氏度，密封组件不得与 AX-210 互换。",
     ["AX-210B 120 摄氏度", "增强配置密封组件能否与标准配置互换"]),
    ("bearing", "BX-320 轴承润滑", "BX-320 轴承每运行 2000 小时补充润滑脂，禁止混用不同牌号的润滑剂。",
     ["BX-320 2000 小时", "轴承补油周期是多少"]),
    ("torque", "CX-400 安装紧固", "CX-400 地脚螺栓使用 M24，预紧扭矩 420 N·m，紧固后复查设备水平度。",
     ["CX-400 M24 420", "地脚螺栓应该拧多紧"]),
    ("pressure", "DX-510 压力试验", "DX-510 水压试验压力 1.6 MPa，保压时间 30 分钟，试验期间不得敲击承压部件。",
     ["DX-510 1.6 MPa", "水压试验需要保持多久"]),
    ("vibration", "EX-620 振动排查", "EX-620 振动超限时停机检查联轴器对中，校核地脚松动及转子不平衡。",
     ["EX-620 联轴器对中", "机器抖动很大需要检查什么"]),
    ("electric", "FX-730 电机接线", "FX-730 电机额定电压 380 V，频率 50 Hz。送电前检查保护接地和绝缘电阻。",
     ["FX-730 380 V 50 Hz", "电机通电之前需要做哪些检查"]),
    ("invoice", "差旅费用报销制度", "差旅报销必须提交发票及出差审批单，由部门负责人审核后送财务复核。",
     ["差旅 发票 审批单", "员工出差后怎样申请费用报销"]),
    ("delivery", "项目交付检查规范", "项目交付前核对装箱清单、合格证与操作说明书，备件数量必须与合同一致。",
     ["交付 装箱清单 合格证", "发货时需要准备哪些随附资料"]),
    ("incident", "解析故障恢复说明", "PDF 解析超时后保留原文件，任务重试最多 3 次，连续失败后进入人工复核。",
     ["PDF 解析 超时 重试", "文档处理卡住后怎样恢复"]),
    ("code", "检索接口开发说明", "def search_documents(query, organization_id): 检索前过滤事业部权限，只返回当前已发布版本。",
     ["search_documents organization_id", "如何保证员工只能搜索已发布的部门资料"]),
    ("log", "运行故障日志分析", "2026-10-19 ERROR service=parser code=OCR_TIMEOUT batch=测试数据。扫描件识别超时，请核查本地 OCR 进程。",
     ["OCR_TIMEOUT parser", "扫描件文字识别失败应该排查哪个进程"]),
]


def save_source(path, title, text):
    if path.suffix == ".docx":
        from docx import Document

        doc = Document()
        doc.add_heading(title, 1)
        doc.add_paragraph(text)
        doc.save(path)
    elif path.suffix == ".xlsx":
        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        sheet.title = "测试数据"
        sheet.append(["资料名称", "完整技术说明"])
        sheet.append([title, text])
        book.save(path)
        book.close()
    elif path.suffix == ".pdf":
        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_textbox((40, 40, 550, 780), title + "\n\n" + text,
                            fontname="china-s", fontsize=14)
        doc.save(path)
        doc.close()
    else:
        path.write_text(title + "\n\n" + text, encoding="utf-8")


def timing(values):
    ordered = sorted(values)
    return {"median_ms": round(statistics.median(values), 3),
            "p95_ms": round(ordered[max(0, math.ceil(len(values) * .95) - 1)], 3),
            "count": len(values)}


def run(args):
    root = Path(__file__).resolve().parents[1]
    day = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
    batch = "TEST-SEARCH-" + day + "-" + uuid4().hex[:10]
    directory = root / ".local" / "search-benchmarks" / batch
    sources = directory / "测试数据"
    sources.mkdir(parents=True)
    # Subprocess-local only: a synthetic benchmark must never call company APIs.
    for key in list(os.environ):
        if key.startswith(("AGICO_KB_VISION_", "AGICO_KB_NAMING_")):
            os.environ.pop(key)
    password = (root / ".local/pg-password.txt").read_text().strip()
    dsn = make_conninfo(host="127.0.0.1", port=int(os.environ.get("AGICO_KB_TEST_PORT", "15432")),
                        dbname="agico_test", user="agico_dev", password=password)
    schema = "test_search_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.info.dbname == "agico_test"
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    settings = Settings(dsn, directory / "storage", schema, model_offline=True)
    db = Database(settings)
    service = None
    index = "test-search-" + uuid4().hex
    xerj = None
    report = {"batch_id": batch, "data_label": "测试数据", "schema": schema,
              "fresh_sources": True, "production_accessed": False}
    try:
        db.migrate()
        embedder = LocalEmbedding(settings)
        print("model_ready", flush=True)
        documents = []
        for i, (key, title, text, _) in enumerate(FACTS):
            suffix = [".md", ".docx", ".xlsx", ".pdf"][i % 4]
            path = sources / (key + suffix)
            save_source(path, "测试数据 " + title, text + "\n测试批次：" + batch)
            parsed = parse_file(path, path.name)
            assert parsed.chunks, (path.name, parsed.warnings)
            documents.append({"key": key, "title": title, "path": path,
                              "chunks": split_for_embedding(parsed.chunks, embedder.tokenizer),
                              "warnings": parsed.warnings, "visible": True})
        # A fresh scan exercises the project's actual OCR path as well.
        import pymupdf

        native = pymupdf.open(sources / "torque.pdf")
        scan = pymupdf.open()
        image = native[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
        scan.new_page().insert_image(native[0].rect, stream=image)
        scan_path = sources / "torque-scan.pdf"
        scan.save(scan_path)
        scan.close()
        native.close()
        parsed = parse_file(scan_path, scan_path.name)
        documents.append({"key": "torque", "title": "安装紧固扫描副本", "path": scan_path,
                          "chunks": split_for_embedding(parsed.chunks, embedder.tokenizer),
                          "warnings": parsed.warnings, "visible": True})
        # New distractors create nontrivial index size; every vector is computed now.
        for n in range(args.noise_files):
            path = sources / f"noise-{n:03}.md"
            paragraphs = [f"测试数据 {batch} 监测记录 {n}-{j}：仓库环境温度 {18+j%9} 摄氏度，"
                          f"湿度 {40+j%13}%，存放包装箱 {100+n+j} 个，记录班次和库位。"
                          for j in range(40)]
            path.write_text("\n\n".join(paragraphs), encoding="utf-8")
            documents.append({"key": "noise", "title": f"环境监测记录 {n}", "path": path,
                              "chunks": split_for_embedding(parse_file(path, path.name).chunks, embedder.tokenizer),
                              "warnings": [], "visible": True})
        for key, text in [("private", "AX-210 密封泄漏 保密特制配置最高工作温度 300 摄氏度。"),
                          ("foreign", "AX-210 密封圈磨损 其他事业部资料 禁止访问。")]:
            path = sources / (key + ".md")
            save_source(path, "测试数据 " + key, text + " " + batch)
            documents.append({"key": key, "title": key, "path": path,
                              "chunks": parse_file(path, path.name).chunks,
                              "warnings": [], "visible": False})
        all_chunks = [chunk for doc in documents for chunk in doc["chunks"]]
        print(f"sources_parsed files={len(documents)} chunks={len(all_chunks)}", flush=True)
        started = time.perf_counter()
        vectors = iter(embedder.embed([chunk.text for chunk in all_chunks]))
        report["embedding_seconds"] = round(time.perf_counter() - started, 3)
        print(f"fresh_files={len(documents)} fresh_chunks={len(all_chunks)}", flush=True)
        records = []
        with db.connection() as conn:
            for person in ("test-author", "test-peer"):
                conn.execute("INSERT INTO principals(id,name) VALUES (%s,%s)", (person, "测试数据 " + person))
                conn.execute("INSERT INTO memberships VALUES (%s,'baiste','member')", (person,))
            for doc in documents:
                did, vid, uid, generation = (uuid4() for _ in range(4))
                org = "xingyuan" if doc["key"] == "foreign" else "baiste"
                visibility = "private" if doc["key"] == "private" else "department"
                conn.execute("""INSERT INTO documents(id,organization_id,category_id,title,created_by,visibility,revision)
                    VALUES (%s,%s,'technical',%s,'test-author',%s,1)""", (did, org, doc["title"], visibility))
                conn.execute("""INSERT INTO uploads(id,owner_id,filename,expected_size,size,state,idempotency_key,request_hash,blob_key)
                    VALUES (%s,'test-author',%s,1,1,'submitted',%s,%s,%s)""", (uid, doc["path"].name, str(uid), str(uid), str(uid)))
                conn.execute("""INSERT INTO versions(id,document_id,upload_id,created_by,base_revision,state,processing_status,
                    active_generation,capabilities,model_identity)
                    VALUES (%s,%s,%s,'test-author',0,'published','ready',%s,%s,%s)""",
                             (vid, did, uid, generation, Jsonb({"text": True, "vector": True, "file": True}), embedder.identity))
                conn.execute("UPDATE documents SET active_version_id=%s WHERE id=%s", (vid, did))
                for ordinal, chunk in enumerate(doc["chunks"]):
                    cid, vector = uuid4(), next(vectors)
                    conn.execute("""INSERT INTO chunks(id,version_id,generation,ordinal,text,locator,keywords,embedding,model_identity)
                        VALUES (%s,%s,%s,%s,%s,%s,to_tsvector('simple',%s),%s::public.vector,%s)""",
                                 (cid, vid, generation, ordinal, chunk.text, Jsonb(chunk.locator),
                                  segmented(chunk.text), vector_literal(vector), embedder.identity))
                    records.append({"id": str(cid), "key": doc["key"], "visible": doc["visible"],
                                    "text": segmented(chunk.text), "embedding": vector,
                                    "original": chunk.text, "locator": chunk.locator})
            # A new schema has no planner statistics yet. Do not compare XERJ's
            # populated index with PostgreSQL's guesses before auto-analyze runs.
            for table in ("documents", "versions", "uploads", "chunks", "document_grants"):
                conn.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(table)))
        report["postgres_statistics_refreshed"] = True
        by_id = {row["id"]: row for row in records}
        queries = [(key, query) for key, _, _, values in FACTS for query in values]
        principal = Principal("test-peer", {"baiste": "member"})
        service = SearchService(db, settings)
        # Same already-loaded real model for both query implementations; initialization
        # and downloading are excluded from hot-query latency, never from ingest cost.
        service.query_model._model = embedder
        if args.baseline_embeddings:
            spec = importlib.util.spec_from_file_location("agico_kb._benchmark_embeddings", args.baseline_embeddings)
            baseline_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(baseline_module)
            baseline = baseline_module.QueryEmbedding(settings)
            baseline._model = embedder
        else:
            baseline = None

        def measure_pg(query_model, target_service):
            original = target_service.query_model
            target_service.query_model = query_model
            durations, hits, quality = [], [], []
            try:
                for _ in range(args.rounds):
                    for key, query in queries:
                        start = time.perf_counter()
                        response = target_service.search(principal, SearchRequest(query=query))
                        durations.append((time.perf_counter() - start) * 1000)
                        ids = [str(item["chunk_id"]) for item in response["items"]]
                        assert all(by_id[cid]["visible"] for cid in ids)
                        hits.append(ids)
                        quality.append(any(by_id[cid]["key"] == key for cid in ids))
                return {**timing(durations), "hit_at_5": round(sum(quality) / len(quality), 4),
                        "first_pass": timing(durations[:len(queries)]),
                        "repeat_pass": timing(durations[len(queries):]) if args.rounds > 1 else None,
                        "details": [{"query": query, "expected": key,
                                     "returned": [by_id[cid]["key"] for cid in ids]}
                                    for (key, query), ids in zip(queries, hits, strict=False)]}
            finally:
                target_service.query_model = original

        if baseline:
            baseline_service = service
            if args.baseline_search:
                spec = importlib.util.spec_from_file_location("agico_kb._benchmark_search", args.baseline_search)
                baseline_search = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(baseline_search)
                baseline_service = baseline_search.SearchService(db, settings)
            report["postgres_before"] = measure_pg(baseline, baseline_service)
            if baseline_service is not service:
                baseline_service.close()
            baseline.close()
            print("postgres_before=" + json.dumps({k:v for k,v in report["postgres_before"].items() if k != "details"}), flush=True)
        report["postgres_after"] = measure_pg(service.query_model, service)
        print("postgres_after=" + json.dumps({k:v for k,v in report["postgres_after"].items() if k != "details"}), flush=True)
        # No result caching: permission/publication mutations must be visible even
        # when query vectors have already been cached.
        with db.connection() as conn:
            conn.execute("UPDATE documents SET visibility='private' WHERE title=%s", (FACTS[0][1],))
        hidden = service.search(principal, SearchRequest(query=FACTS[0][3][0]))
        assert all(by_id[str(item["chunk_id"])]["key"] != "seal" for item in hidden["items"])
        with db.connection() as conn:
            conn.execute("UPDATE documents SET visibility='department' WHERE title=%s", (FACTS[0][1],))
        report["cached_vector_permission_recheck"] = True

        if args.xerj_url:
            assert args.xerj_url.startswith("http://127.0.0.1:")
            xerj = httpx.Client(base_url=args.xerj_url, timeout=30, trust_env=False)
            response = xerj.put('/' + index, json={"mappings": {"properties": {
                "text": {"type": "text", "analyzer": "whitespace"},
                "visible": {"type": "boolean"},
                "embedding": {"type": "dense_vector", "dims": embedder.dimension, "similarity": "cosine"}}}})
            response.raise_for_status()
            start = time.perf_counter()
            for offset in range(0, len(records), 100):
                lines = []
                for record in records[offset:offset + 100]:
                    lines.extend([json.dumps({"index": {"_index": index, "_id": record["id"]}}),
                                  json.dumps({k: record[k] for k in ("text", "visible", "embedding")})])
                response = xerj.post('/_bulk?refresh=true', content="\n".join(lines) + "\n",
                                     headers={"content-type": "application/x-ndjson"})
                response.raise_for_status()
                assert not response.json().get("errors"), response.text[:500]
            report["xerj_index_seconds"] = round(time.perf_counter() - start, 3)
            # Equal cache state: just like postgres_after, XERJ starts with an
            # empty query cache and the same loaded native model. Its first pass
            # must pay for query inference too; later passes exercise reuse.
            service.query_model.close()
            service.query_model = QueryEmbedding(settings)
            service.query_model._model = embedder
            durations, quality, details = [], [], []
            # Parallel lexical/vector requests, using precisely the same real
            # multilingual vectors, permission prefilter and RRF constant as PG.
            with ThreadPoolExecutor(max_workers=2) as executor:
                for _ in range(args.rounds):
                    for key, query in queries:
                        start = time.perf_counter()
                        vector, _ = service.query_model.query(query)
                        words = keyword_query(query).replace('"', '').replace(' OR ', ' ')
                        def request(clause):
                            result = xerj.post('/' + index + '/_search', json={"size": 1100, "_source": False,
                                "query": {"bool": {"must": [clause], "filter": [{"term": {"visible": True}}]}}})
                            result.raise_for_status()
                            return result.json()["hits"]["hits"]
                        lexical = executor.submit(request, {"match": {"text": words}})
                        semantic = executor.submit(request, {"knn": {"field": "embedding", "query_vector": vector,
                                                                    "k": 1100, "num_candidates": 1100}})
                        scores = {}
                        for candidates in (lexical.result(), [hit for hit in semantic.result() if hit["_score"] > .675]):
                            for rank, hit in enumerate(candidates, 1):
                                scores[hit["_id"]] = scores.get(hit["_id"], 0) + 1 / (60 + rank)
                        ids = sorted(scores, key=lambda cid: (-scores[cid], cid))[:5]
                        # A usable external-engine result still has to fetch the
                        # authoritative source metadata and recheck permissions.
                        req = SearchRequest(query=query)
                        where, parameters = permission_filter(principal, req)
                        with db.connection() as conn:
                            found = conn.execute(
                                f"""SELECT {FIELDS},c.id AS chunk_id,left(c.text,600) AS excerpt,
                                length(c.text)>600 AS excerpt_truncated,c.locator
                                {FROM} JOIN chunks c ON c.version_id=v.id AND c.generation=v.active_generation
                                WHERE {where} AND c.id=ANY(%s::uuid[])""", [*parameters, ids],
                            ).fetchall()
                        mapped = {str(row["chunk_id"]): row for row in found}
                        page = service._page(principal, [mapped[cid] for cid in ids if cid in mapped], req, [])
                        ids = [str(item["chunk_id"]) for item in page["items"]]
                        durations.append((time.perf_counter() - start) * 1000)
                        assert all(by_id[cid]["visible"] for cid in ids)
                        quality.append(any(by_id[cid]["key"] == key for cid in ids))
                        details.append({"query": query, "expected": key, "returned": [by_id[cid]["key"] for cid in ids]})
            report["xerj_same_model"] = {**timing(durations), "hit_at_5": round(sum(quality) / len(quality), 4),
                                          "first_pass": timing(durations[:len(queries)]),
                                          "repeat_pass": timing(durations[len(queries):]) if args.rounds > 1 else None,
                                          "details": details[:len(queries)]}
            print("xerj=" + json.dumps({k:v for k,v in report["xerj_same_model"].items() if k != "details"}), flush=True)
        report.update(files=len(documents), chunks=len(records), questions=len(queries), rounds=args.rounds,
                      equal_empty_query_caches=True, loaded_model_shared=True,
                      parse_warnings={doc["path"].name: doc["warnings"] for doc in documents if doc["warnings"]})
        (directory / "parsed-manifest.json").write_text(json.dumps([
            {"file": doc["path"].name, "chunks": [asdict(chunk) for chunk in doc["chunks"]]}
            for doc in documents], ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if service:
            service.close()
        db.close()
        try:
            if xerj:
                try:
                    deleted = xerj.delete('/' + index)
                    if deleted.status_code != 404:
                        deleted.raise_for_status()
                finally:
                    xerj.close()
        finally:
            # A failed or unavailable external test node must not prevent
            # cleanup of this run's isolated PostgreSQL schema.
            with psycopg.connect(dsn, autocommit=True) as conn:
                assert conn.info.dbname == "agico_test" and schema.startswith("test_search_")
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    report["isolated_schema_removed"] = True
    output = Path(args.output) if args.output else directory / "results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("results=" + str(output.resolve()), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xerj-url", help="Dedicated loopback test node only")
    parser.add_argument("--baseline-embeddings", type=Path, help="Optional pre-change module snapshot")
    parser.add_argument("--baseline-search", type=Path, help="Optional pre-change SearchService snapshot")
    parser.add_argument("--noise-files", type=int, default=30)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path)
    run(parser.parse_args())
