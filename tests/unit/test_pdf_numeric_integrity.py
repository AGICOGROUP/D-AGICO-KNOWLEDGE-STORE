from io import BytesIO

import pymupdf
from PIL import Image

from agico_kb import vision
from agico_kb.config import Settings
from agico_kb.ingestion import Parsed


def test_table_zero_values_are_not_treated_as_empty_cells():
    from agico_kb.ingestion import _table_text
    assert _table_text([[0, 0.0, None, False]]) == '0 | 0.0 |  | False'


def test_drawing_uses_300dpi_overlapping_regions_instead_of_small_whole_page():
    with pymupdf.open() as doc:
        page = doc.new_page()
        scale, regions = vision._pdf_visual_regions(page, drawing=True)
        assert scale == 300 / 72
        assert len(regions) > 1
        assert max(max(r.width, r.height) * scale for r in regions) <= 2201
        assert regions[0].x1 > regions[1].x0


def test_numeric_evidence_preserves_diameter_radius_sign_and_decimal():
    from agico_kb.pdf_integrity import numeric_gaps
    evidence = ['孔径 φ12；内侧 350', '外径 Ø675 h8；公差 -0.125；圆角 R2']
    gaps = numeric_gaps(evidence, '孔径 R1.2；内侧 330；外径 φ675 h8；公差 -0.125')
    assert {'φ12', '350', 'R2'} <= set(gaps)
    assert 'φ675' not in gaps
    assert '-0.125' not in gaps
    assert numeric_gaps(evidence, 'φ12 350 Ø675 h8 -0.125 R2') == []
    assert numeric_gaps(['内侧350，允许±0.3'], '内侧330，允许0.3') == ['350', '±0.3']


def test_numeric_conflict_has_one_bounded_source_repair_before_partial(tmp_path, monkeypatch):
    path = tmp_path / 'drawing.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_text((40, 80), 'R2 350')
        for i in range(50):
            page.draw_circle((20 + i % 10 * 25, 100 + i // 10 * 30), 5)
        doc.save(path)
    config = tmp_path / 'key.txt'
    config.write_text('api=test-secret')
    settings = Settings('unused', tmp_path / 'store', vision_config_file=config)
    monkeypatch.setattr(vision, '_has_image_text', lambda _: True)
    monkeypatch.setattr(vision, '_pdf_numeric_evidence', lambda *args: ['R2 350'])
    calls = []
    def model(key, name, image, prompt):
        calls.append(prompt)
        return 'R2 350' if '差异修复' in prompt else 'R1.2 330'
    parsed = vision.enrich_pdf(Parsed(status='partial'), path, settings, call_model=model)
    assert len(calls) == 3
    assert parsed.status == 'ready'
    assert parsed.chunks[0].locator['numeric_verification'] == 'token_coverage'
    assert 'R2 350' in parsed.chunks[0].text


def test_review_receives_independent_numeric_evidence_and_cache_changes_with_evidence(tmp_path):
    config = tmp_path / 'key.txt'
    config.write_text('api=test-secret')
    settings = Settings('unused', tmp_path / 'store', vision_config_file=config)
    out = BytesIO()
    Image.new('RGB', (100, 100), 'white').save(out, format='PNG')
    calls = []
    def model(key, name, image, prompt):
        calls.append(prompt)
        assert '独立读取' in prompt and 'φ12' in prompt
        return '孔径 φ12；内侧 350'
    for evidence in [['孔径 φ12'], ['孔径 φ12', '内侧 350']]:
        vision._review_pdf_region(out.getvalue(), 'R1.2 内侧330', settings,
                                  call_model=model, numeric_evidence=evidence)
    assert len(calls) == 2


def test_drawing_detection_does_not_relax_text_document_with_single_dimension():
    from agico_kb.pdf_integrity import drawing_page
    with pymupdf.open() as doc:
        page = doc.new_page()
        prose = Parsed()
        prose.add('The contract specifies diameter φ12 and delivery requirements.',
                  kind='ocr', page=1, pdf_assessment={'route': 'ocr_text', 'text_line_share': .99})
        assert not drawing_page(page, prose, 1)
        drawing = Parsed()
        drawing.add('φ12 R2 350 380 A-A', kind='ocr', page=1,
                    pdf_assessment={'route': 'vision', 'text_line_share': .1})
        assert drawing_page(page, drawing, 1)


def test_partial_visual_region_suppresses_overlapping_ocr_but_preserves_other_regions():
    parsed = Parsed()
    parsed.add('incorrect 330', kind='ocr', page=1, region=[0, 0, 100, 100])
    parsed.add('not yet reviewed', kind='ocr', page=1, region=[0, 200, 100, 300])
    parsed.add('correct 350', kind='page_visual', page=1, region=[0, 0, 110, 110])
    vision._prefer_pdf_visual(parsed, set())
    assert not any(c.text == 'incorrect 330' for c in parsed.chunks)
    assert any(c.text == 'not yet reviewed' for c in parsed.chunks)


def test_missing_numeric_evidence_marks_partial_instead_of_silent_ready(tmp_path, monkeypatch):
    path = tmp_path / 'drawing.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_text((40, 80), 'R2 350')
        for i in range(50):
            page.draw_circle((20 + i % 10 * 25, 100 + i // 10 * 30), 5)
        doc.save(path)
    config = tmp_path / 'key.txt'
    config.write_text('api=test-secret')
    settings = Settings('unused', tmp_path / 'store', vision_config_file=config)
    monkeypatch.setattr(vision, '_has_image_text', lambda _: True)
    monkeypatch.setattr(vision, '_pdf_numeric_evidence', lambda *args: ['R2 350'])
    parsed = vision.enrich_pdf(Parsed(status='partial'), path, settings,
                               call_model=lambda *_: 'R1.2 330')
    assert parsed.status == 'partial'
    assert any('数字或符号' in w for w in parsed.warnings)
    assert any(c.locator.get('numeric_verification') == 'unresolved' for c in parsed.chunks)
