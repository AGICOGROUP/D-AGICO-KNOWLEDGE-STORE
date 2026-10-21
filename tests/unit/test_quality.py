import json

from docx import Document

from agico_kb.ingestion import Chunk, Parsed
from agico_kb.quality import evaluate_manifest, score_parsed


def test_score_requires_source_location_and_detects_omission():
    parsed = Parsed([
        Chunk("电压 220 V", {"page": 1}),
        Chunk("电压 380 V", {"page": 2}),
    ], "ready", [])
    result = score_parsed(parsed, [
        {"id": "voltage", "locator": {"page": 1}, "contains": "电压 220 V"},
        {"id": "wrong-page", "locator": {"page": 1}, "contains": "380 V"},
        {"id": "no-claim", "locator": {"page": 1}, "excludes": "电压 500 V"},
        {"id": "missing-page", "locator": {"page": 3}, "excludes": "电压 500 V"},
    ])
    assert result["score_percent"] == 50
    assert [item["passed"] for item in result["checks"]] == [True, False, True, False]
    assert "220 V" in result["checks"][1]["observed_excerpt"]


def test_manifest_processes_source_fresh_and_requires_complete_references(tmp_path):
    path = tmp_path / "fresh.docx"
    document = Document()
    document.add_paragraph("型号 AX-210，工作电压 220 V。")
    document.save(path)
    case = {
        "source": str(path),
        "checks": [{"id": "voltage", "locator": {"paragraph": 1}, "contains": "220 V"}],
    }
    sample = evaluate_manifest({"files": [case]})
    assert sample["score_percent"] == 100
    assert not sample["qualified"]
    assert not sample["reference_complete"]
    assert sample["files"][0]["parser_complete"]
    case.update(reference_scope="full", visual_reference_complete=True)
    full = evaluate_manifest({"files": [case]})
    assert full["qualified"]
    assert full["reference_complete"]
    assert json.dumps(full, ensure_ascii=False)


def test_drawing_lookup_requires_complete_checks_instead_of_dimension_percentage(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, 'is_file', lambda _: True)
    parsed = Parsed([Chunk('right', {'page': 1, 'content_type': 'drawing'})])
    monkeypatch.setattr('agico_kb.quality.process_fresh', lambda *args, **kwargs: parsed)
    checks = [{'id': str(i), 'locator': {'page': 1}, 'contains': 'right' if i < 19 else 'missing'}
              for i in range(20)]
    case = {'source': 'drawing.pdf', 'content_type': 'drawing', 'checks': checks,
            'reference_scope': 'full', 'visual_reference_complete': True}
    result = evaluate_manifest({'files': [case]})
    assert not result['qualified']
    assert result['files'][0]['threshold_percent'] is None
    assert result['files'][0]['acceptance_basis'] == 'drawing_lookup'
    checks[-1]['contains'] = 'right'
    assert evaluate_manifest({'files': [case]})['qualified']
    case['content_type'] = 'document'
    checks[-1]['contains'] = 'missing'
    assert not evaluate_manifest({'files': [case]})['qualified']  # strictly greater than 95
    case['content_type'] = 'drawing'
    checks[-1]['critical'] = True
    result = evaluate_manifest({'files': [case]})
    assert not result['qualified']
    assert result['files'][0]['critical_failures'] == ['19']


def test_mixed_pdf_cannot_use_whole_file_drawing_acceptance(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, 'is_file', lambda _: True)
    parsed = Parsed([Chunk('drawing', {'page': 1, 'content_type': 'drawing'}),
                     Chunk('ordinary body', {'page': 2})])
    monkeypatch.setattr('agico_kb.quality.process_fresh', lambda *args, **kwargs: parsed)
    case = {'source': 'mixed.pdf', 'content_type': 'drawing', 'checks': [
        {'id': 'title', 'locator': {'page': 1}, 'contains': 'drawing'}],
        'reference_scope': 'full', 'visual_reference_complete': True}
    assert not evaluate_manifest({'files': [case]})['qualified']
    case['content_type'] = 'document'
    case['drawing_pages'] = [1]
    assert not evaluate_manifest({'files': [case]})['qualified']
    case['checks'].append({'id': 'body', 'locator': {'page': 2}, 'contains': 'missing'})
    assert not evaluate_manifest({'files': [case]})['qualified']
