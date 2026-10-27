import json
from pathlib import Path
from types import SimpleNamespace

from agico_kb import naming
from agico_kb.naming import build_suggestions


def test_batch_aliases_share_canonical_project_prefix_without_touching_originals():
    originals = [
        "阿根廷400TPD石灰项目总工艺流程图.dwg",
        "Argentina 400TPD lime project equipment list.xlsx",
    ]
    answer = json.dumps({"items": [
        {"index": 0, "project": "阿根廷400TPD石灰项目", "object": "", "file_type": "工艺流程图",
         "topic": "总工艺", "language": "ZH", "business_date": "", "version": "",
         "confidence": "high", "evidence": "阿根廷400TPD石灰项目"},
        {"index": 1, "project": "阿根廷400TPD石灰项目", "object": "", "file_type": "设备清单",
         "topic": "", "language": "EN", "business_date": "", "version": "",
         "confidence": "high", "evidence": "Argentina 400TPD lime project"},
    ]}, ensure_ascii=False)

    items = build_suggestions(originals, answer, [])

    assert [item["original_filename"] for item in items] == originals
    assert all(item["project"] == "阿根廷400TPD石灰项目" for item in items)
    assert items[0]["title"] == "阿根廷400TPD石灰项目_工艺流程图_总工艺_ZH.dwg"
    assert items[1]["title"] == "阿根廷400TPD石灰项目_设备清单_EN.xlsx"
    assert not any(item["needs_review"] for item in items)


def test_generic_drawing_and_unsupported_model_claims_require_review():
    originals = ["Drawing1.dwg", "10TPH氢氧化钙工艺流程图.dwg"]
    answer = json.dumps({"items": [
        {"index": 0, "project": "阿根廷400TPD石灰项目", "object": "", "file_type": "总装图",
         "topic": "", "language": "ZH", "business_date": "", "version": "R1",
         "confidence": "high", "evidence": "Drawing1"},
        {"index": 1, "project": "", "object": "10TPH氢氧化钙", "file_type": "工艺流程图",
         "topic": "", "language": "", "business_date": "20261012", "version": "",
         "confidence": "high", "evidence": "10TPH氢氧化钙"},
    ]}, ensure_ascii=False)

    items = build_suggestions(originals, answer, [])

    assert items[0]["needs_review"] is True
    assert items[0]["project"] == ""
    assert "R1" not in items[0]["title"]
    assert items[1]["title"] == "10TPH氢氧化钙_工艺流程图.dwg"
    assert items[1]["needs_review"] is False


def test_existing_project_may_be_reused_only_when_reference_is_in_scope():
    original = ["Argentina 400TPD equipment list.xlsx"]
    answer = json.dumps({"items": [
        {"index": 0, "project": "阿根廷400TPD石灰项目", "object": "", "file_type": "设备清单",
         "topic": "", "language": "", "business_date": "", "version": "",
         "confidence": "high", "evidence": "Argentina 400TPD"},
    ]}, ensure_ascii=False)

    reviewed = build_suggestions(original, answer, [])
    matched = build_suggestions(original, answer, [{"project": "阿根廷400TPD石灰项目", "title": "项目资料"}])

    assert reviewed[0]["needs_review"] is True
    assert matched[0]["needs_review"] is False
    assert matched[0]["project"] == "阿根廷400TPD石灰项目"


def test_bad_or_missing_model_response_keeps_original_and_routes_to_human():
    for raw in ("not json", '{"items":[]}', '{}'):
        item = build_suggestions(["图纸.dwg"], raw, [])[0]
        assert item["title"] == "图纸.dwg"
        assert item["needs_review"] is True


def test_text_naming_request_uses_low_reasoning_and_returns_visible_json(monkeypatch):
    seen = []
    monkeypatch.setattr(naming, "_credentials", lambda path: ("test-key", "glm-5.3-flash"))

    def post(url, **kwargs):
        seen.append(kwargs["json"])
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"choices": [{"message": {"content": '{"items":[]}'}}]})

    monkeypatch.setattr(naming.httpx, "post", post)
    settings = SimpleNamespace(vision_config_file=Path("config.txt"),
                               vision_fallback_config_file=None)
    settings.provider_profile = lambda profile="primary": settings.vision_config_file if profile == "primary" else settings.vision_fallback_config_file
    naming._call_model(settings, ["a.pdf"], [])
    assert seen[0]["reasoning_effort"] == "low"
    assert "未成交" in seen[0]["messages"][1]["content"]


def test_unlisted_project_code_or_unmarked_official_code_needs_review():
    original = "阿根廷石灰项目设备清单.xlsx"
    answer = json.dumps({"items": [{
        "index": 0, "project": "阿根廷石灰项目", "object": "", "file_type": "设备清单",
        "topic": "", "language": "", "business_date": "", "version": "",
        "confidence": "high", "evidence": "阿根廷石灰项目",
        "matched_project_code": "OPP-2026-999999", "official_project_code": "AG-001",
    }]}, ensure_ascii=False)
    item = build_suggestions([original], answer, [])[0]
    assert item["needs_review"] is True
    assert item["matched_project_code"] == ""
    assert item["official_project_code"] == ""


def test_customer_name_is_an_object_not_an_unconfirmed_project():
    name = "1.工艺技术方案（菏泽双龙）.doc"
    answer = json.dumps({"items": [{
        "index": 0, "project": "菏泽双龙", "object": "", "file_type": "工艺技术方案",
        "topic": "工艺技术方案", "language": "中文", "business_date": "", "version": "",
        "confidence": "high", "evidence": "工艺技术方案（菏泽双龙）",
    }]}, ensure_ascii=False)

    item = build_suggestions([name], answer, [])[0]

    assert item["project"] == ""
    assert item["title"] == "菏泽双龙_工艺技术方案.doc"
    assert item["needs_review"] is False


def test_business_type_must_be_supported_by_chinese_filename():
    name = "7.公用工程消耗清单（菏泽双龙）.doc"
    answer = json.dumps({"items": [{
        "index": 0, "project": "菏泽双龙", "object": "", "file_type": "设备清单",
        "topic": "公用工程消耗清单", "language": "中文", "business_date": "", "version": "",
        "confidence": "high", "evidence": "公用工程消耗清单（菏泽双龙）",
    }]}, ensure_ascii=False)

    item = build_suggestions([name], answer, [])[0]

    assert item["project"] == ""
    assert item["needs_review"] is True
    assert item["reason"] == "文件类型与原文件名不符，请核对"


def test_generic_training_plan_explains_missing_subject_not_missing_type():
    name = "9.培训计划 - 副本.docx"
    answer = json.dumps({"items": [{
        "index": 0, "project": "", "object": "", "file_type": "培训计划",
        "topic": "", "language": "", "business_date": "", "version": "",
        "confidence": "low", "evidence": "培训计划 - 副本",
    }]}, ensure_ascii=False)

    item = build_suggestions([name], answer, [])[0]

    assert item["needs_review"] is True
    assert item["reason"] == "无法从文件名或文内线索确定适用对象或项目"


def test_structured_sales_inquiry_uses_clear_document_heading_when_model_is_uncertain(monkeypatch):
    names = [
        "C1_Kenya_CementRollerPress_PaulAllan_销_202610120841.pdf",
        "C1_Tunisia_CarbonAnodePlateProductionLine_Unknown_销_202610120452.pdf",
    ]
    heading = "Part 1｜外销员询盘分析报告\n询盘时间：2026-10-12 08:41\n邮件原文和技术澄清问题。"
    monkeypatch.setattr(naming, "_bounded_content_hint", lambda path, filename: heading)
    monkeypatch.setattr(naming, "_references", lambda *args: [])

    def model(settings, filenames, references, hints=None):
        # The first filename-only AI answer cannot identify the business type.
        return json.dumps({"items": [{"index": 0, "project": "", "object": "",
            "file_type": "", "topic": "", "language": "", "business_date": "",
            "version": "", "confidence": "low", "evidence": ""}]})

    for name in names:
        suggestion = naming._suggest_batch(None, None, "org", "author", [name],
                                           call_model=model, source_paths=[Path("unused.blob")])[0]
        assert suggestion["needs_review"] is False
        assert "_询盘分析报告_" in suggestion["title"]
        assert suggestion["title"].endswith("_20261012_ZH.pdf")
        assert suggestion["project"] == ""
    ambiguous = naming._suggest_batch(None, None, "org", "author", ["C1_Kenya_CementRollerPress.pdf"],
                                      call_model=model, source_paths=[Path("unused.blob")])[0]
    assert ambiguous["needs_review"] is True


def test_empty_primary_naming_response_uses_fallback(monkeypatch):
    seen = []
    monkeypatch.setattr(naming, "_credentials", lambda path: ("test-key", "test-model"))

    def post(url, **kwargs):
        seen.append(url)
        content = "" if len(seen) == 1 else '{"items":[]}'
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: {"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(naming.httpx, "post", post)
    settings = SimpleNamespace(vision_config_file=Path("primary.txt"),
                               vision_fallback_config_file=Path("fallback.txt"),
                               vision_fallback_api_url="https://fallback.example/v1")
    settings.provider_profile = lambda profile="primary": settings.vision_config_file if profile == "primary" else settings.vision_fallback_config_file
    result = naming._call_model(settings, ["a.pdf"], [])

    assert result == '{"items":[]}'
    assert seen == [naming.API_URL, settings.vision_fallback_api_url]


def test_existing_project_is_not_accepted_without_source_identity():
    answer = json.dumps({"items": [{
        "index": 0, "project": "阿根廷400TPD石灰项目", "object": "", "file_type": "设备清单",
        "topic": "", "language": "", "business_date": "", "version": "",
        "confidence": "high", "evidence": "设备清单",
    }]}, ensure_ascii=False)
    item = build_suggestions(["德国设备清单.xlsx"], answer, [
        {"project": "阿根廷400TPD石灰项目", "project_code": "OPP-2026-000001", "aliases": []}
    ])[0]
    assert item["needs_review"] is True
    assert item["project"] == ""


def test_project_registry_has_reserved_context_even_after_many_documents():
    references = [{"project": "", "title": f"无关资料{i}", "filename": f"无关{i}.pdf"}
                  for i in range(120)]
    references.append({"project": "阿根廷400TPD石灰项目", "project_code": "OPP-2026-000001",
                       "aliases": ["Argentina 400TPD lime project"], "title": "项目", "filename": ""})
    selected = naming._select_references(references, ["Argentina 400TPD lime equipment list.xlsx"])
    assert selected[0]["project_code"] == "OPP-2026-000001"
    assert len(selected) <= 110


def test_formal_project_code_can_reuse_visible_project():
    references = [{"project": "阿根廷石灰项目", "project_code": "OPP-2026-000001",
                   "official_project_code": "AG-2026-07", "aliases": []}]
    name = "AG-2026-07_设备清单.xlsx"
    answer = json.dumps({"items": [{"index": 0, "project": "阿根廷石灰项目",
        "object": "", "file_type": "设备清单", "topic": "", "language": "",
        "business_date": "", "version": "", "confidence": "high",
        "evidence": "AG-2026-07", "matched_project_code": "AG-2026-07"}]}, ensure_ascii=False)
    item = build_suggestions([name], answer, references)[0]
    assert item["needs_review"] is False
    assert item["matched_project_code"] == "AG-2026-07"
    assert naming._select_references(references, [name]) == references


def test_ambiguous_docx_uses_bounded_internal_heading(tmp_path, monkeypatch):
    from docx import Document
    document = Document()
    document.add_paragraph("菏泽双龙 员工培训计划")
    path = tmp_path / "source.blob"
    document.save(path)
    monkeypatch.setattr(naming, "_references", lambda *args: [])
    calls = []

    def model(settings, filenames, references, hints=None):
        calls.append(hints)
        if not hints:
            return '{"items":[{"index":0,"confidence":"low","evidence":"培训计划"}]}'
        return json.dumps({"items": [{"index": 0, "project": "", "object": "菏泽双龙",
            "file_type": "培训计划", "topic": "", "language": "", "business_date": "",
            "version": "", "confidence": "high", "evidence": "菏泽双龙"}]}, ensure_ascii=False)

    item = naming._suggest_batch(None, None, "org", "author", ["培训计划.docx"],
                                 call_model=model, source_paths=[path])[0]
    assert len(calls) == 2
    assert "菏泽双龙" in calls[1][0]
    assert item["title"] == "菏泽双龙_培训计划.docx"
    assert item["needs_review"] is False


def test_verbose_evidence_with_original_filename_is_still_verifiable():
    name = "Annex6-Performance_Guarantee_Requirements.docx"
    hint = "KARABAKH CEMENT GRINDING PLANT\nPERFORMANCE GUARANTEE REQUIREMENTS"
    answer = json.dumps({"items": [{"index": 0, "project": "",
        "object": "KARABAKH CEMENT GRINDING PLANT", "file_type": "Performance Guarantee Requirements",
        "topic": "", "language": "EN", "business_date": "", "version": "",
        "confidence": "high", "evidence": "原文件名：Annex6-Performance_Guarantee_Requirements.docx；文内标题：KARABAKH CEMENT GRINDING PLANT"}]}, ensure_ascii=False)
    item = build_suggestions([name], answer, [], {0: hint})[0]
    assert item["needs_review"] is False
    assert item["title"] == "KARABAKH CEMENT GRINDING PLANT_Performance Guarantee Requirements_EN.docx"


def test_contractual_plant_heading_is_a_project_and_location_is_not_name():
    name = "Annex5-HSE_Requirements.docx"
    hint = ("KARABAKH CEMENT GRINDING PLANT\nAgdam, Azerbaijan\nANNEXURE-5\n"
            "HEALTH, SAFETY AND ENVIRONMENT (HSE) REQUIREMENTS\n"
            "These requirements apply to the EPC contract.")
    answer = json.dumps({"items": [{"index": 0,
        "project": "Karabakh Cement Grinding Plant, Agdam, Azerbaijan", "object": "",
        "file_type": "HSE Requirements", "topic": "", "language": "EN",
        "business_date": "", "version": "", "confidence": "high",
        "evidence": "KARABAKH CEMENT GRINDING PLANT"}]}, ensure_ascii=False)
    item = build_suggestions([name], answer, [], {0: hint})[0]
    assert item["needs_review"] is False
    assert item["project"] == "Karabakh Cement Grinding Plant"
    assert item["title"] == "Karabakh Cement Grinding Plant_HSE Requirements_EN.docx"


def test_chapter_title_cannot_masquerade_as_subject_and_generic_type():
    name = "Ch02 Plant Concept and Production Process.docx"
    answer = json.dumps({"items": [{"index": 0, "project": "",
        "object": "Plant Concept and Production Process", "file_type": "技术文件章节",
        "topic": "", "language": "EN", "business_date": "", "version": "",
        "confidence": "high", "evidence": "Plant Concept and Production Process"}]}, ensure_ascii=False)
    item = build_suggestions([name], answer, [])[0]
    assert item["needs_review"] is True
    assert item["title"] == name


def test_object_heading_reuses_unique_registered_project():
    name = "Annex6-Performance_Guarantee_Requirements.docx"
    hint = "KARABAKH CEMENT GRINDING PLANT\nAgdam, Azerbaijan\nPERFORMANCE GUARANTEE REQUIREMENTS"
    references = [{"project": "Karabakh Cement Grinding Plant",
                   "project_code": "OPP-2026-000001", "aliases": []}]
    answer = json.dumps({"items": [{"index": 0, "project": "",
        "object": "Karabakh Cement Grinding Plant, Agdam, Azerbaijan",
        "file_type": "Performance Guarantee Requirements",
        "topic": "Performance Guarantee Requirements and Acceptance Test Procedures",
        "language": "EN", "business_date": "", "version": "", "confidence": "high",
        "evidence": "KARABAKH CEMENT GRINDING PLANT"}]}, ensure_ascii=False)
    item = build_suggestions([name], answer, references, {0: hint})[0]
    assert item["needs_review"] is False
    assert item["project"] == "Karabakh Cement Grinding Plant"
    assert item["matched_project_code"] == "OPP-2026-000001"
