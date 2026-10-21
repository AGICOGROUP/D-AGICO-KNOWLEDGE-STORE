import json
import re
from uuid import uuid4

from agico_kb import naming
from agico_kb.ingestion import Parsed
from agico_kb.project_registry import confirm
from agico_kb.worker import Worker
from tests.conftest import headers, submit


def test_shared_naming_rules_require_login_and_match_runtime(kb):
    client, _, _ = kb
    assert client.get('/v1/naming/rules').status_code == 401
    response = client.get('/v1/naming/rules', headers=headers())
    assert response.status_code == 200
    assert response.json() == naming.shared_rules()


def test_preformatted_upload_skips_ai_and_unblocks_normal_parse(kb, monkeypatch):
    client, db, storage = kb
    original = 'Cedar_设备清单_ZH.md'
    content = ('Fresh equipment schedule ' + uuid4().hex).encode()
    upload = client.post('/v1/uploads', headers=headers(key=uuid4().hex),
                         json={'filename': original, 'size': len(content)}).json()['upload_id']
    assert client.put(f'/v1/uploads/{upload}/content', headers=headers(), content=content).status_code == 200
    response = client.post('/v1/submissions', headers=headers(), json={
        'upload_id': upload, 'organization_id': 'baiste', 'category_id': 'technical',
        'title': original, 'visibility': 'department', 'naming_pending': True})
    assert response.status_code == 201
    version_id = response.json()['version_id']
    with db.connection(write=True) as conn:
        conn.execute('UPDATE documents SET project=%s WHERE id=(SELECT document_id FROM versions WHERE id=%s)',
                     ('Existing registered context', version_id))

    def forbidden(*args, **kwargs):
        raise AssertionError('Naming must not call AI or read originals for compliant names')

    monkeypatch.setattr(naming, '_call_model', forbidden)
    monkeypatch.setattr(naming, '_bounded_content_hint', forbidden)
    settings = client.app.state.settings
    assert naming.process_pending(db, settings) == 1
    result = client.get(f'/v1/versions/{version_id}', headers=headers()).json()
    assert result['title'] == original
    assert result['capabilities']['naming_review']['method'] == 'filename_rules'
    assert result['capabilities']['naming_review']['needs_review'] is False
    with db.connection() as conn:
        row = conn.execute('SELECT d.project,u.blob_key FROM documents d JOIN versions v ON v.document_id=d.id '
                           'JOIN uploads u ON u.id=v.upload_id WHERE v.id=%s', (version_id,)).fetchone()
    assert row['project'] == 'Existing registered context'
    assert (storage / row['blob_key']).read_bytes() == content
    assert Worker(db, settings).claim() is not None


def test_authenticated_batch_suggestion_unifies_project_prefix_before_upload(kb, monkeypatch):
    client, _, _ = kb
    calls = []

    def model(settings, filenames, references):
        calls.append((filenames, references))
        return json.dumps({"items": [
            {"index": 0, "project": "阿根廷400TPD石灰项目", "object": "", "file_type": "工艺流程图",
             "topic": "", "language": "", "business_date": "", "version": "",
             "confidence": "high", "evidence": "阿根廷400TPD石灰项目"},
            {"index": 1, "project": "阿根廷400TPD石灰项目", "object": "", "file_type": "设备清单",
             "topic": "", "language": "", "business_date": "", "version": "",
             "confidence": "high", "evidence": "Argentina 400TPD lime"},
        ]}, ensure_ascii=False)

    monkeypatch.setattr(naming, "_call_model", model)
    response = client.post("/v1/naming/suggest", headers=headers(), json={
        "organization_id": "baiste", "filenames": [
            "阿根廷400TPD石灰项目工艺流程图.dwg",
            "Argentina 400TPD lime equipment list.xlsx",
        ]})
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(calls) == 1
    assert all(item["title"].startswith("阿根廷400TPD石灰项目_") for item in items)
    assert not any(item["needs_review"] for item in items)
    assert client.post("/v1/naming/suggest", json={"organization_id": "baiste",
        "filenames": ["x.pdf"]}).status_code == 401


def test_naming_does_not_reveal_another_divisions_references(kb, monkeypatch):
    client, _, _ = kb
    monkeypatch.setattr(naming, "_call_model", lambda *args: '{"items":[]}')
    response = client.post("/v1/naming/suggest", headers=headers(), json={
        "organization_id": "xingyuan", "filenames": ["private.dwg"]})
    assert response.status_code == 403


def test_older_ai_review_is_rechecked_once_after_naming_rules_upgrade(kb, monkeypatch):
    client, db, _ = kb
    original = "1.工艺技术方案（菏泽双龙）.doc"
    content = b"synthetic naming input"
    prepared = client.post("/v1/uploads", headers=headers(key="naming-upgrade-case"),
                           json={"filename": original, "size": len(content)})
    upload_id = prepared.json()["upload_id"]
    assert client.put(f"/v1/uploads/{upload_id}/content", headers=headers(),
                      content=content).status_code == 200
    created = client.post("/v1/submissions", headers=headers(), json={
        "upload_id": upload_id, "organization_id": "baiste", "category_id": "technical",
        "title": original, "visibility": "department", "naming_pending": True})
    assert created.status_code == 201
    version_id = created.json()["version_id"]
    with db.connection(write=True) as conn:
        conn.execute("""UPDATE versions SET capabilities=jsonb_set(capabilities,
            '{naming_review}', '{"state":"review","needs_review":true,"reason":"旧规则"}'::jsonb,
            true) WHERE id=%s""", (version_id,))

    monkeypatch.setattr(naming, "_call_model", lambda *_: json.dumps({"items": [{
        "index": 0, "project": "", "object": "菏泽双龙", "file_type": "工艺技术方案",
        "topic": "", "language": "", "business_date": "", "version": "",
        "confidence": "high", "evidence": "工艺技术方案（菏泽双龙）",
    }]}, ensure_ascii=False))
    assert naming.process_pending(db, client.app.state.settings) == 1
    renamed = client.get(f"/v1/versions/{version_id}", headers=headers()).json()
    assert renamed["title"] == "菏泽双龙_工艺技术方案.doc"
    assert renamed["capabilities"]["naming_review"]["needs_review"] is False
    assert renamed["capabilities"]["naming_review"]["rules_version"] == naming.NAMING_RULES_VERSION
    assert naming.process_pending(db, client.app.state.settings) == 0


def test_uncertain_name_goes_to_reviewer_without_changing_original_file(kb):
    client, db, _ = kb
    content = b"synthetic CAD bytes"
    prepared = client.post("/v1/uploads", headers=headers(key="naming-review-case"),
                           json={"filename": "图纸.dwg", "size": len(content)})
    upload_id = prepared.json()["upload_id"]
    assert client.put(f"/v1/uploads/{upload_id}/content", headers=headers(), content=content).status_code == 200
    created = client.post("/v1/submissions", headers=headers(), json={
        "upload_id": upload_id, "organization_id": "baiste", "category_id": "technical",
        "title": "图纸.dwg", "visibility": "department", "naming_review_required": True,
        "naming_reason": "无法判断项目和图名"})
    assert created.status_code == 201, created.text
    version_id = created.json()["version_id"]
    pending = client.get("/v1/submissions", headers=headers("chief")).json()["items"][0]
    assert pending["naming_review"]["needs_review"] is True
    assert Worker(db, client.app.state.settings).claim() is None
    blocked = client.post(f"/v1/versions/{version_id}/publish", headers=headers("chief"),
                          json={"expected_revision": 0})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "NAMING_REVIEW_REQUIRED"
    assert client.patch(f"/v1/versions/{version_id}/name", headers=headers(),
                        json={"current_title": "图纸.dwg", "title": "项目A_工艺流程图.dwg",
                              "project": "项目A"}).status_code == 404
    reviewed = client.patch(f"/v1/versions/{version_id}/name", headers=headers("chief"),
                            json={"current_title": "图纸.dwg", "title": "项目A_工艺流程图.dwg",
                                  "project": "项目A"})
    assert reviewed.status_code == 200, reviewed.text
    assert re.fullmatch(r"OPP-\d{4}-\d{6}_工艺流程图\.dwg", reviewed.json()["title"])
    assert reviewed.json()["filename"] == "图纸.dwg"
    assert reviewed.json()["capabilities"]["naming_review"]["needs_review"] is False
    assert str(Worker(db, client.app.state.settings).claim()["version_id"]) == version_id


def test_existing_project_name_is_available_for_new_alias_in_same_division(kb, monkeypatch):
    client, db, _ = kb
    created, _, _ = submit(client, title="阿根廷400TPD石灰项目_设备清单_ZH.xlsx",
                           project="阿根廷400TPD石灰项目")
    version_id = created.json()["version_id"]
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET processing_status='ready' WHERE id=%s", (version_id,))
        conn.execute("UPDATE jobs SET state='complete' WHERE version_id=%s", (version_id,))
    published = client.post(f"/v1/versions/{version_id}/publish", headers=headers("chief"),
                            json={"expected_revision": 0})
    assert published.status_code == 200, published.text

    seen = []

    def model(settings, filenames, references):
        seen.extend(references)
        return json.dumps({"items": [{
            "index": 0, "project": "阿根廷400TPD石灰项目", "object": "", "file_type": "工艺流程图",
            "topic": "", "language": "", "business_date": "", "version": "",
            "confidence": "high", "evidence": "Argentina 400TPD"
        }]}, ensure_ascii=False)

    monkeypatch.setattr(naming, "_call_model", model)
    response = client.post("/v1/naming/suggest", headers=headers(), json={
        "organization_id": "baiste", "filenames": ["Argentina 400TPD flow diagram.dwg"]})
    assert response.status_code == 200, response.text
    assert any(row["project"] == "阿根廷400TPD石灰项目" for row in seen)
    assert response.json()["items"][0]["project"] == "阿根廷400TPD石灰项目"
    assert response.json()["items"][0]["needs_review"] is False


def test_recent_draft_project_is_available_to_next_upload_batch(kb, monkeypatch):
    client, _, _ = kb
    created, _, _ = submit(client, title="阿根廷400TPD石灰项目_设备清单_ZH.xlsx",
                           project="阿根廷400TPD石灰项目")
    assert created.status_code == 201
    seen = []

    def model(settings, filenames, references):
        seen.extend(references)
        return json.dumps({"items": [{
            "index": 0, "project": "阿根廷400TPD石灰项目", "object": "", "file_type": "工艺流程图",
            "topic": "", "language": "", "business_date": "", "version": "",
            "confidence": "high", "evidence": "Argentina 400TPD"
        }]}, ensure_ascii=False)

    monkeypatch.setattr(naming, "_call_model", model)
    response = client.post("/v1/naming/suggest", headers=headers(), json={
        "organization_id": "baiste", "filenames": ["Argentina 400TPD flow diagram.dwg"]})
    assert response.status_code == 200, response.text
    assert any(row["project"] == "阿根廷400TPD石灰项目" for row in seen)
    assert response.json()["items"][0]["needs_review"] is False


def test_submit_then_ai_name_then_parse_in_that_order(kb, monkeypatch):
    client, db, _ = kb
    settings = client.app.state.settings
    original = "Argentina 400TPD lime project process flow.dwg"
    content = b"post submission naming synthetic content"
    prepared = client.post("/v1/uploads", headers=headers(key="post-submit-naming"),
                           json={"filename": original, "size": len(content)})
    upload_id = prepared.json()["upload_id"]
    assert client.put(f"/v1/uploads/{upload_id}/content", headers=headers(), content=content).status_code == 200
    submitted = client.post("/v1/submissions", headers=headers(), json={
        "upload_id": upload_id, "organization_id": "baiste", "category_id": "technical",
        "title": original, "visibility": "department", "naming_pending": True})
    assert submitted.status_code == 201, submitted.text
    version_id = submitted.json()["version_id"]
    assert submitted.json()["title"] == original
    assert submitted.json()["capabilities"]["naming_review"]["state"] == "pending"
    assert Worker(db, settings).claim() is None

    def model(settings, filenames, references):
        assert filenames == [original]
        return json.dumps({"items": [{
            "index": 0, "project": "Argentina 400TPD lime project", "object": "", "file_type": "工艺流程图",
            "topic": "", "language": "EN", "business_date": "", "version": "",
            "confidence": "high", "evidence": "Argentina 400TPD lime project"
        }]}, ensure_ascii=False)

    monkeypatch.setattr(naming, "_call_model", model)
    assert naming.process_pending(db, settings) == 1
    renamed = client.get(f"/v1/versions/{version_id}", headers=headers()).json()
    assert re.fullmatch(r"OPP-\d{4}-\d{6}_工艺流程图_EN\.dwg", renamed["title"])
    assert renamed["filename"] == original
    assert renamed["capabilities"]["naming_review"]["state"] == "done"
    worker = Worker(db, settings)
    claim = worker.claim()
    assert str(claim["version_id"]) == version_id
    monkeypatch.setattr(worker, "_parse_with_conversion", lambda _: Parsed([], "ready", []))
    assert worker.run_claim(claim) is True
    after_parse = client.get(f"/v1/versions/{version_id}", headers=headers()).json()
    assert after_parse["capabilities"]["naming_review"]["state"] == "done"
    assert after_parse["capabilities"]["naming_review"]["rules_version"] == naming.NAMING_RULES_VERSION


def test_post_submit_ambiguous_name_waits_for_human_before_parsing(kb, monkeypatch):
    client, db, _ = kb
    settings = client.app.state.settings
    content = b"another synthetic drawing"
    prepared = client.post("/v1/uploads", headers=headers(key="ambiguous-after-submit"),
                           json={"filename": "drawing.dwg", "size": len(content)})
    upload_id = prepared.json()["upload_id"]
    assert client.put(f"/v1/uploads/{upload_id}/content", headers=headers(), content=content).status_code == 200
    submitted = client.post("/v1/submissions", headers=headers(), json={
        "upload_id": upload_id, "organization_id": "baiste", "category_id": "technical",
        "title": "wrong title.dwg", "visibility": "department", "naming_pending": True})
    assert submitted.status_code == 201, submitted.text
    version_id = submitted.json()["version_id"]
    assert submitted.json()["title"] == "drawing.dwg"
    assert Worker(db, settings).claim() is None
    assert client.patch(f"/v1/versions/{version_id}/name", headers=headers("chief"), json={
        "current_title": "drawing.dwg", "title": "产品A_总装图.dwg", "project": ""
    }).status_code == 409

    monkeypatch.setattr(naming, "_call_model", lambda *args: json.dumps({"items": [{
        "index": 0, "project": "", "object": "", "file_type": "", "topic": "", "language": "",
        "business_date": "", "version": "", "confidence": "low", "evidence": "drawing"
    }]}))
    assert naming.process_pending(db, settings) == 1
    state = client.get(f"/v1/versions/{version_id}", headers=headers()).json()
    assert state["capabilities"]["naming_review"]["state"] == "review"
    assert Worker(db, settings).claim() is None
    reviewed = client.patch(f"/v1/versions/{version_id}/name", headers=headers("chief"), json={
        "current_title": "drawing.dwg", "title": "产品A_总装图.dwg", "project": ""
    })
    assert reviewed.status_code == 200, reviewed.text
    assert str(Worker(db, settings).claim()["version_id"]) == version_id


def test_post_submit_batch_uses_one_project_prefix(kb, monkeypatch):
    client, db, _ = kb
    settings = client.app.state.settings
    filenames = ["阿根廷400TPD石灰项目工艺流程图.dwg", "Argentina 400TPD lime equipment list.xlsx"]
    version_ids = []
    for index, filename in enumerate(filenames):
        content = f"unique synthetic content {index}".encode()
        prepared = client.post("/v1/uploads", headers=headers(key=f"post-submit-batch-{index}"),
                               json={"filename": filename, "size": len(content)})
        upload_id = prepared.json()["upload_id"]
        assert client.put(f"/v1/uploads/{upload_id}/content", headers=headers(), content=content).status_code == 200
        submitted = client.post("/v1/submissions", headers=headers(), json={
            "upload_id": upload_id, "organization_id": "baiste", "category_id": "technical",
            "title": filename, "visibility": "department", "naming_pending": True})
        assert submitted.status_code == 201, submitted.text
        version_ids.append(submitted.json()["version_id"])

    def model(settings, names, references):
        assert len(names) == 1
        if names[0] == filenames[0]:
            item = {"index": 0, "project": "阿根廷400TPD石灰项目", "object": "", "file_type": "工艺流程图",
                    "topic": "", "language": "ZH", "business_date": "", "version": "",
                    "confidence": "high", "evidence": "阿根廷400TPD石灰项目"}
        else:
            assert names[0] == filenames[1]
            item = {"index": 0, "project": "阿根廷400TPD石灰项目", "object": "", "file_type": "设备清单",
                    "topic": "", "language": "EN", "business_date": "", "version": "",
                    "confidence": "high", "evidence": "Argentina 400TPD lime"}
        return json.dumps({"items": [item]}, ensure_ascii=False)

    monkeypatch.setattr(naming, "_call_model", model)
    assert naming.process_pending(db, settings) == 1
    assert naming.process_pending(db, settings) == 1
    results = [client.get(f"/v1/versions/{version_id}", headers=headers()).json()
               for version_id in version_ids]
    assert len({item["project"] for item in results}) == 1
    assert re.fullmatch(r"OPP-\d{4}-\d{6}", results[0]["project"])
    assert all(item["title"].startswith(results[0]["project"] + "_") for item in results)
    assert [item["filename"] for item in results] == filenames


def test_unexpected_naming_error_ends_in_human_review(kb, monkeypatch):
    client, db, _ = kb
    original = "待确认资料.pdf"
    content = b"a read-only original"
    prepared = client.post("/v1/uploads", headers=headers(key="naming-unexpected-error"),
                           json={"filename": original, "size": len(content)})
    upload_id = prepared.json()["upload_id"]
    assert client.put(f"/v1/uploads/{upload_id}/content", headers=headers(), content=content).status_code == 200
    created = client.post("/v1/submissions", headers=headers(), json={
        "upload_id": upload_id, "organization_id": "baiste", "category_id": "technical",
        "title": original, "visibility": "department", "naming_pending": True})
    version_id = created.json()["version_id"]
    monkeypatch.setattr(naming, "_suggest_batch", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    assert naming.process_pending(db, client.app.state.settings) == 1
    result = client.get(f"/v1/versions/{version_id}", headers=headers()).json()
    assert result["capabilities"]["naming_review"]["state"] == "review"
    assert result["capabilities"]["naming_review"]["needs_review"] is True
    assert result["filename"] == original
    assert result["title"] == original
    assert naming.process_pending(db, client.app.state.settings) == 0


def test_project_resolution_failure_ends_in_human_review(kb, monkeypatch):
    client, db, _ = kb
    original = "阿根廷400TPD石灰项目工艺流程图.dwg"
    content = b"read-only cad original"
    prepared = client.post("/v1/uploads", headers=headers(key="naming-resolution-error"),
                           json={"filename": original, "size": len(content)})
    upload_id = prepared.json()["upload_id"]
    assert client.put(f"/v1/uploads/{upload_id}/content", headers=headers(), content=content).status_code == 200
    created = client.post("/v1/submissions", headers=headers(), json={
        "upload_id": upload_id, "organization_id": "baiste", "category_id": "technical",
        "title": original, "visibility": "department", "naming_pending": True})
    version_id = created.json()["version_id"]
    monkeypatch.setattr(naming, "_call_model", lambda *_: json.dumps({"items": [{
        "index": 0, "project": "阿根廷400TPD石灰项目", "object": "", "file_type": "工艺流程图",
        "topic": "", "language": "", "business_date": "", "version": "",
        "confidence": "high", "evidence": "阿根廷400TPD石灰项目",
    }]}, ensure_ascii=False))
    monkeypatch.setattr(naming, "resolve", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))

    assert naming.process_pending(db, client.app.state.settings) == 1
    result = client.get(f"/v1/versions/{version_id}", headers=headers()).json()
    assert result["capabilities"]["naming_review"]["state"] == "review"
    assert result["title"] == original


def test_legacy_generic_ai_title_is_reviewed_before_publish(kb):
    client, db, _ = kb
    created, _, _ = submit(client, title="Plant Concept and Production Process_技术文件章节_EN.docx")
    version_id = created.json()["version_id"]
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET processing_status='ready' WHERE id=%s", (version_id,))
        conn.execute("UPDATE jobs SET state='complete' WHERE version_id=%s", (version_id,))
    pending = client.get("/v1/submissions", headers=headers("chief")).json()
    item = next(row for row in pending["items"] if str(row["version_id"]) == version_id)
    assert item["naming_review"]["needs_review"] is True
    assert pending["reviewable_total"] == 0
    blocked = client.post(f"/v1/versions/{version_id}/publish", headers=headers("chief"),
                          json={"expected_revision": 0})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "NAMING_REVIEW_REQUIRED"


def test_generic_name_cannot_be_confirmed_unchanged(kb):
    client, db, _ = kb
    generic = "Plant Concept_技术文件章节.md"
    created, _, _ = submit(client, title=generic)
    version_id = created.json()["version_id"]
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET processing_status='ready' WHERE id=%s", (version_id,))
        conn.execute("UPDATE jobs SET state='complete' WHERE version_id=%s", (version_id,))
    rejected = client.patch(f"/v1/versions/{version_id}/name", headers=headers("chief"),
                            json={"current_title": generic, "title": generic})
    assert rejected.status_code == 422
    accepted = client.patch(f"/v1/versions/{version_id}/name", headers=headers("chief"),
                            json={"current_title": generic, "title": "Plant Concept_工艺说明.md"})
    assert accepted.status_code == 200


def test_later_project_registration_unifies_earlier_ai_named_draft(kb):
    client, db, _ = kb
    old_title = "Karabakh Cement Grinding Plant, Agdam, Azerbaijan_HSE Requirements_EN.docx"
    created, _, _ = submit(client, title=old_title)
    version_id = created.json()["version_id"]
    with db.connection(write=True) as conn:
        version = conn.execute("SELECT document_id,created_by FROM versions WHERE id=%s",
                               (version_id,)).fetchone()
        conn.execute(
            "INSERT INTO audit_events(actor_id,document_id,version_id,action,details) VALUES (%s,%s,%s,'auto_name','{}')",
            (version["created_by"], version["document_id"], version_id))
        project = confirm(conn, "baiste", version["created_by"], "department",
                          "Karabakh Cement Grinding Plant")

    assert naming.process_pending(db, client.app.state.settings) == 1
    result = client.get(f"/v1/versions/{version_id}", headers=headers()).json()
    assert result["title"] == project["code"] + "_HSE Requirements_EN.docx"
    assert result["project"] == project["code"]
    assert result["filename"] == "公司资料.md"
    assert naming.process_pending(db, client.app.state.settings) == 0
