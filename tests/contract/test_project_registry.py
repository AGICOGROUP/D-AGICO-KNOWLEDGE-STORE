import json
import re

from psycopg.types.json import Jsonb

from agico_kb import naming
from agico_kb.project_registry import resolve, visible_projects
from tests.conftest import headers


def _pending(client, filename, key):
    content = ("synthetic " + key).encode()
    prepared = client.post("/v1/uploads", headers=headers(key=key),
                           json={"filename": filename, "size": len(content)})
    assert prepared.status_code == 201, prepared.text
    upload_id = prepared.json()["upload_id"]
    assert client.put(f"/v1/uploads/{upload_id}/content", headers=headers(),
                      content=content).status_code == 200
    submitted = client.post("/v1/submissions", headers=headers(), json={
        "upload_id": upload_id, "organization_id": "baiste", "category_id": "technical",
        "title": filename, "visibility": "department", "naming_pending": True,
    })
    assert submitted.status_code == 201, submitted.text
    return submitted.json()["version_id"]


def _answer(name, project, *, matched="", official="", confidence="high"):
    return json.dumps({"items": [{
        "index": 0, "project": project, "object": "", "file_type": "设备清单", "topic": "",
        "language": "", "business_date": "", "version": "", "confidence": confidence,
        "evidence": name, "matched_project_code": matched,
        "official_project_code": official,
    }]}, ensure_ascii=False)


def test_opportunity_id_is_persisted_and_reused_across_batches(kb, monkeypatch):
    client, db, _ = kb
    first = _pending(client, "阿根廷400TPD石灰项目设备清单.xlsx", "registry-first")
    monkeypatch.setattr(naming, "_call_model", lambda *_: _answer(
        "阿根廷400TPD石灰项目", "阿根廷400TPD石灰项目"))
    assert naming.process_pending(db, client.app.state.settings) == 1
    first_doc = client.get(f"/v1/versions/{first}", headers=headers()).json()
    code = first_doc["project"]
    assert re.fullmatch(r"OPP-\d{4}-\d{6}", code)

    second = _pending(client, "Argentina 400TPD lime new equipment list.xlsx", "registry-second")
    monkeypatch.setattr(naming, "_call_model", lambda _settings, _names, refs: (
        _answer("Argentina 400TPD lime", "Argentina 400TPD lime", matched=code)
        if any(ref.get("project_code") == code for ref in refs) else "{}"))
    assert naming.process_pending(db, client.app.state.settings) == 1
    second_doc = client.get(f"/v1/versions/{second}", headers=headers()).json()
    assert second_doc["project"] == code
    assert second_doc["title"].startswith(code + "_")
    with db.connection() as conn:
        projects = conn.execute("SELECT code,aliases FROM projects WHERE organization_id='baiste'").fetchall()
        links = conn.execute("SELECT DISTINCT project_id FROM documents WHERE project_id IS NOT NULL").fetchall()
    assert len(projects) == len(links) == 1
    assert "Argentina 400TPD lime" in projects[0]["aliases"]


def test_human_confirmed_formal_code_replaces_temporary_prefix_without_losing_record(kb):
    client, db, _ = kb
    first = _pending(client, "generic drawing.dwg", "registry-formal")
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET capabilities=jsonb_set(capabilities,'{naming_review}',%s,true) "
                     "WHERE id=%s", (Jsonb({"state": "review", "needs_review": True}), first))
    response = client.patch(f"/v1/versions/{first}/name", headers=headers("chief"), json={
        "current_title": "generic drawing.dwg", "title": "阿根廷石灰项目_工艺流程图.dwg",
        "project": "阿根廷石灰项目", "official_project_code": "AG-2026-001",
    })
    assert response.status_code == 200, response.text
    assert response.json()["project"] == "AG-2026-001"
    assert response.json()["title"] == "AG-2026-001_工艺流程图.dwg"
    assert response.json()["filename"] == "generic drawing.dwg"
    with db.connection() as conn:
        record = conn.execute("SELECT code,official_code,status FROM projects").fetchone()
    assert record == {"code": "AG-2026-001", "official_code": "AG-2026-001", "status": "won"}


def test_formal_code_promotes_existing_project_titles_but_keeps_original_files(kb, monkeypatch):
    client, db, _ = kb
    first = _pending(client, "阿根廷400TPD石灰项目设备清单.xlsx", "registry-promote-first")
    monkeypatch.setattr(naming, "_call_model", lambda *_: _answer(
        "阿根廷400TPD石灰项目", "阿根廷400TPD石灰项目"))
    assert naming.process_pending(db, client.app.state.settings) == 1
    initial = client.get(f"/v1/versions/{first}", headers=headers()).json()
    temporary_code = initial["project"]

    second = _pending(client, "untitled drawing.dwg", "registry-promote-second")
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET capabilities=jsonb_set(capabilities,'{naming_review}',%s,true) "
                     "WHERE id=%s", (Jsonb({"state": "review", "needs_review": True}), second))
    response = client.patch(f"/v1/versions/{second}/name", headers=headers("chief"), json={
        "current_title": "untitled drawing.dwg",
        "title": "阿根廷400TPD石灰项目_工艺流程图.dwg",
        "project": "阿根廷400TPD石灰项目", "official_project_code": "AG-2026-019",
    })
    assert response.status_code == 200, response.text
    promoted = client.get(f"/v1/versions/{first}", headers=headers()).json()
    assert promoted["project"] == "AG-2026-019"
    assert promoted["title"] == "AG-2026-019_设备清单.xlsx"
    assert promoted["filename"] == "阿根廷400TPD石灰项目设备清单.xlsx"
    with db.connection() as conn:
        record = conn.execute("SELECT code,official_code,status FROM projects").fetchone()
    assert record == {"code": temporary_code, "official_code": "AG-2026-019", "status": "won"}
    with db.connection(write=True) as conn:
        conn.execute("UPDATE versions SET processing_status='ready' WHERE id=%s", (first,))
        conn.execute("UPDATE jobs SET state='complete' WHERE version_id=%s", (first,))
    assert client.post(f"/v1/versions/{first}/publish", headers=headers("chief"),
                       json={"expected_revision": 0}).status_code == 200
    for mode in ("files", "content"):
        result = client.post("/v1/search", headers=headers(), json={
            "query": temporary_code, "mode": mode,
        })
        assert result.status_code == 200, result.text
        assert [item["version_id"] for item in result.json()["items"]] == [first]


def test_uncertain_project_does_not_allocate_an_id(kb, monkeypatch):
    client, db, _ = kb
    version = _pending(client, "阿根廷石灰设备清单.xlsx", "registry-uncertain")
    monkeypatch.setattr(naming, "_call_model", lambda *_: _answer(
        "阿根廷石灰", "阿根廷石灰项目", confidence="low"))
    assert naming.process_pending(db, client.app.state.settings) == 1
    state = client.get(f"/v1/versions/{version}", headers=headers()).json()
    assert state["capabilities"]["naming_review"]["needs_review"] is True
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM projects").fetchone()["n"] == 0


def test_explicit_official_number_in_filename_is_registered_by_system(kb, monkeypatch):
    client, db, _ = kb
    version = _pending(client, "阿根廷石灰项目_项目编号AG_2026_022_设备清单.xlsx",
                       "registry-official-ai")
    monkeypatch.setattr(naming, "_call_model", lambda *_: _answer(
        "阿根廷石灰项目", "阿根廷石灰项目", official="AG_2026_022"))
    assert naming.process_pending(db, client.app.state.settings) == 1
    result = client.get(f"/v1/versions/{version}", headers=headers()).json()
    assert result["project"] == "AG_2026_022"
    assert result["title"].startswith("AG_2026_022_设备清单")
    with db.connection() as conn:
        record = conn.execute("SELECT code,official_code,status FROM projects").fetchone()
    assert record == {"code": "AG_2026_022", "official_code": "AG_2026_022", "status": "won"}


def test_project_memory_is_unique_and_respects_private_visibility(kb):
    _, db, _ = kb
    with db.connection(write=True) as conn:
        first, error = resolve(conn, "baiste", "alice", "private",
                               {"project": "阿根廷石灰客户甲", "evidence": "客户甲"})
        second, error2 = resolve(conn, "baiste", "alice", "private",
                                 {"project": "阿根廷石灰客户乙", "evidence": "客户乙"})
        assert not error and not error2
        assert first["code"] != second["code"]
    with db.connection() as conn:
        assert visible_projects(conn, "baiste", "alice")
        assert visible_projects(conn, "baiste", "peer") == []
        assert visible_projects(conn, "xingyuan", "bob") == []
