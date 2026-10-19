"""Fresh regressions for font recovery, conservative routing and numeric formats."""

import pymupdf

from agico_kb.ingestion import Parsed, parse_file


def test_equal_decimal_formats_do_not_trigger_repairs_but_changed_values_do():
    from agico_kb.pdf_integrity import numeric_gaps

    assert numeric_gaps(['20.00 φ12.0 R2.00 ± 0.30 - 0.125'],
                        '20 φ12 R2 ±0.3 -0.125') == []
    assert numeric_gaps(['330 ±0.3 φ12 -0.125'], '350 0.3 R12 0.125') == [
        '-0.125', '330', '±0.3', 'φ12']
    assert numeric_gaps(['Drawing 1.8.2'], 'Drawing 1.82') == ['1.8.2']


def test_prose_with_tolerances_is_not_full_drawing_transcription():
    from agico_kb.pdf_integrity import drawing_page

    with pymupdf.open() as doc:
        page = doc.new_page()
        parsed = Parsed()
        parsed.add('Manufacture and acceptance: the allowed tolerances are ±1% and ±0.3 mm.\n'
                   'Inspection must confirm these tolerances before the parts are delivered.\n'
                   'The supervisor shall inspect the manufacturing records and material certificates.',
                   kind='ocr', page=1,
                   pdf_assessment={'route': 'vision', 'text_line_share': .4})
        assert not drawing_page(page, parsed, 1)


def test_font_quality_does_not_confuse_real_multilingual_text_with_broken_mapping():
    from agico_kb.drawing_index import broken_mapping

    assert broken_mapping('\x01\x10ǒǋǗǐǘǪǐǝ ǛǫǲǷǰǻ ǗǫǼȃǽǫǬ')
    assert not broken_mapping('Наименование: Футеровка торца загрузки; 材料表; Gómez Farías; ±0.3')
    assert not broken_mapping('正常文本\x00仍保留分隔字符')


def test_broken_native_mapping_uses_ocr_and_never_indexes_garbled_text(tmp_path, monkeypatch):
    from agico_kb import ingestion

    source = tmp_path / 'font-map.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 50), 'Original visible source')
        doc.save(source)
    original = source.read_bytes()
    native = [{'text': '\x01\x10ǒǋǗǐǘǪǐǝ ǛǫǲǷǰǻ' * 30,
               'region': [40, 40, 500, 500], 'group': 0, 'source': 'native'}]
    monkeypatch.setattr('agico_kb.drawing_index.native_records', lambda _: native)
    calls = []

    def scan(page, index):
        calls.append(index)
        parsed = Parsed()
        parsed.add('Recovered manufacturing instructions and material notes.', kind='ocr', page=index,
                   pdf_assessment={'route': 'ocr_text'})
        return parsed, []

    monkeypatch.setattr(ingestion, '_pdf_scan_records', scan)
    parsed = parse_file(source, source.name)
    assert calls == [1]
    assert 'Recovered manufacturing' in '\n'.join(c.text for c in parsed.chunks)
    assert not any('ǒ' in c.text for c in parsed.chunks)
    assert source.read_bytes() == original


def test_large_font_outlined_document_title_needs_independent_sheet_evidence():
    from agico_kb.drawing_index import assess

    texts = ['DOCUMENT TITLE: Lining arrangement', 'DRAWING NO: AG-491',
             'SECTION A-A', 'SECTION B-B', 'PARTS LIST', 'NOTES: material steel']
    records = [{'text': text, 'region': [40 + i * 120, 40 + i * 85, 140 + i * 120, 55 + i * 85]}
               for i, text in enumerate(texts)]
    assert assess(records, 840, 595, vector=True, vector_count=4000)['content_type'] == 'drawing'
    assert assess(records[:2], 840, 595, vector=True, vector_count=4000)['content_type'] != 'drawing'


def test_ocr_drawing_title_is_pixel_reviewed_without_full_dimension_transcription(tmp_path, monkeypatch):
    from agico_kb import vision
    from agico_kb.config import Settings

    source = tmp_path / 'outlined-flow.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=1600, height=1000)
        page.insert_text((100, 90), '300t process flow diagram')
        doc.save(source)
    parsed = Parsed()
    parsed.add('OCR 3001 process flow diagram', kind='drawing_index', page=1,
               region=[90, 70, 400, 100], index_role='identity', extraction='ocr',
               requires_original=True)
    parsed.add('Isolated label near kiln', kind='drawing_index', page=1,
               region=[1000, 500, 1100, 520], index_role='label', extraction='ocr')
    key = tmp_path / 'key.txt'
    key.write_text('api=test-secret')
    settings = Settings('unused', tmp_path / 'fresh-cache', vision_config_file=key)
    calls = []

    def full_page(*args, **kwargs):
        raise AssertionError('Drawing lookup must never run full-sheet dimension transcription')

    def model(key, model, image, prompt):
        calls.append(prompt)
        assert '3001' in prompt and '图名' in prompt and '不转写孤立尺寸' in prompt
        return '图名：300t process flow diagram'

    monkeypatch.setattr(vision, '_pdf_visual_regions', full_page)
    result = vision.enrich_pdf(parsed, source, settings, call_model=model)
    assert len(calls) == 1
    assert all(c.locator['kind'] == 'drawing_index' for c in result.chunks)
    text = '\n'.join(c.text for c in result.chunks)
    assert '300t' in text and '3001' not in text and 'Isolated label near kiln' in text


def test_unreadable_drawing_identity_stays_partial_instead_of_hiding_a_gap(tmp_path):
    from agico_kb.config import Settings
    from agico_kb.vision import enrich_pdf

    source = tmp_path / 'scan.pdf'
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(source)
    parsed = Parsed()
    parsed.add('TITLE: unclear', kind='drawing_index', page=1, region=[40, 40, 140, 80],
               index_role='identity', extraction='ocr')
    result = enrich_pdf(parsed, source, Settings('unused', tmp_path / 'cache'))
    assert result.status == 'partial'
    assert any('尚未核对' in w for w in result.warnings)


def test_corrected_field_survives_when_an_adjacent_field_is_unreadable(tmp_path):
    from agico_kb.config import Settings
    from agico_kb.vision import enrich_pdf

    source = tmp_path / 'fields.pdf'
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(source)
    parsed = Parsed()
    parsed.add('TITLE: 3001 kiln', kind='drawing_index', page=1,
               region=[40, 40, 300, 80], index_role='identity', extraction='ocr')
    key = tmp_path / 'key.txt'
    key.write_text('api=test-secret')
    result = enrich_pdf(parsed, source, Settings('unused', tmp_path / 'fresh', vision_config_file=key),
                        call_model=lambda *_: '图名：300t kiln；图号存在但无法辨认。')
    body = '\n'.join(c.text for c in result.chunks)
    assert '300t' in body and '3001' not in body
    assert result.status == 'partial'
    assert any('尚未核对' in w for w in result.warnings)


def test_corrupted_flow_title_still_receives_field_review():
    from agico_kb.drawing_index import add_index

    parsed = Parsed()
    add_index(parsed, [{'text': '日产500:石灰生产线二艺流理则',
                        'region': [40, 40, 400, 80], 'source': 'ocr', 'confidence': .8}],
              1, {'content_type': 'drawing'})
    assert parsed.chunks[0].locator['index_role'] == 'identity'


def test_croatian_scanned_title_block_and_distributed_dimensions_identify_drawing():
    from agico_kb.drawing_index import assess

    texts = ['R15', 'R15', 'R15', 'R45', 'R3', 'φ675',
             'DETALJ C', 'Br. nacrta: 1002-0204-25', 'Materijal: C.1531.4', 'Komada: 2']
    records = [{'text': text, 'region': [40+i%3*160, 80+i*55, 170+i%3*160, 100+i*55]}
               for i, text in enumerate(texts)]
    assert assess(records, 595, 845, scan=True)['content_type'] == 'drawing'


def test_database_connections_have_a_finite_connection_timeout(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from agico_kb import db
    from agico_kb.config import Settings

    captured = {}

    def pool(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    pool.check_connection = lambda _: None
    monkeypatch.setattr(db, 'ConnectionPool', pool)
    db.Database(Settings('unused', tmp_path))
    assert captured['kwargs']['connect_timeout'] == 5


def test_short_wrapped_chinese_prose_with_tolerances_is_not_dimension_workflow():
    from agico_kb.pdf_integrity import drawing_page

    with pymupdf.open() as doc:
        page = doc.new_page()
        parsed = Parsed()
        parsed.add('\n'.join(['制造方必须保证原材料的可追溯性，并在交货前核对验收记录。']*8)
                   + '\n允许的制造公差为±1%，装配公差为±0.3毫米。',
                   kind='ocr', page=1,
                   pdf_assessment={'route': 'vision', 'text_line_share': .4})
        assert not drawing_page(page, parsed, 1)


def test_table_of_contents_mentioning_drawings_is_not_a_drawing_page():
    from agico_kb.pdf_integrity import drawing_page

    with pymupdf.open() as doc:
        page = doc.new_page()
        parsed = Parsed()
        parsed.add('目录\nI.1. 目的\nI.2. 供货范围\nII.2. 图纸\nIII.1. 验收',
                   kind='ocr', page=1,
                   pdf_assessment={'route': 'vision', 'text_line_share': .1})
        assert not drawing_page(page, parsed, 1)


def test_scanned_spanish_sections_with_many_dimensions_are_drawing_evidence():
    from agico_kb.drawing_index import assess

    texts = ['R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7', 'R8',
             'Corte A', 'SECCION B', 'Material: AISI steel']
    records = [{'text': text, 'region': [30+i%3*160, 50+i*55, 100+i%3*160, 70+i*55]}
               for i, text in enumerate(texts)]
    assert assess(records, 595, 845, scan=True)['content_type'] == 'drawing'


def test_scanned_drawing_with_ocr_damaged_dimensions_uses_title_views_and_unit_evidence():
    from agico_kb.drawing_index import assess

    texts = ['TOPE AISLANTE', 'Corte A - A', 'SECCION B-8', 'R1', 'R2', 'R1204',
             'TITULO: ESPECIFICACION TECNICA', 'Acotación : mm']
    records = [{'text': t, 'region': [30+i%3*160, 50+i*85, 200+i%3*160, 70+i*85]}
               for i, t in enumerate(texts)]
    assert assess(records, 595, 845, scan=True)['content_type'] == 'drawing'
    records[-1]['text'] = 'Other text without drawing unit convention'
    assert assess(records, 595, 845, scan=True)['content_type'] != 'drawing'


def test_segment_transient_failure_recovers_once_without_a_missing_page_warning(tmp_path, monkeypatch):
    from uuid import uuid4

    from agico_kb.config import Settings
    from agico_kb.worker import Worker

    worker = Worker(None, Settings('unused', tmp_path))
    monkeypatch.setattr(worker, '_pdf_page_count', lambda _: 1)
    monkeypatch.setattr(worker, '_progress', lambda *_: None)
    calls = []

    def parse(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ValueError('Transient subprocess failure')
        result = Parsed()
        result.add('Fresh recovered source page', page=1)
        return result

    monkeypatch.setattr(worker, '_parse', parse)
    parsed = worker._parse_segmented({'version_id': uuid4()})
    assert len(calls) == 2
    assert parsed.status == 'ready'
    assert not any('解析未完成' in w for w in parsed.warnings)


def test_persistent_segment_failure_is_bounded_and_not_hidden(tmp_path, monkeypatch):
    from uuid import uuid4

    from agico_kb.config import Settings
    from agico_kb.worker import Worker

    worker = Worker(None, Settings('unused', tmp_path))
    monkeypatch.setattr(worker, '_pdf_page_count', lambda _: 2)
    monkeypatch.setattr(worker, '_progress', lambda *_: None)
    calls = []

    def parse(*args, **kwargs):
        calls.append(kwargs['first_page'])
        if kwargs['first_page'] == 1:
            raise ValueError('Unreadable source page')
        result = Parsed()
        result.add('Fresh readable page', page=2)
        return result

    monkeypatch.setattr(worker, '_parse', parse)
    parsed = worker._parse_segmented({'version_id': uuid4()})
    assert calls == [1, 1, 2]
    assert parsed.status == 'partial'
    assert any('第 1-1 页解析未完成' in w for w in parsed.warnings)


def test_short_column_manual_with_screenshots_is_not_full_dimension_workflow():
    from agico_kb.pdf_integrity import drawing_page

    with pymupdf.open() as doc:
        page = doc.new_page()
        parsed = Parsed()
        parsed.add('Connect the probe to the inlet.\nOpen the calibration menu.\n'
                   'Check the sample flow displayed.\nUse the pump selection button.\n'
                   'Record the concentration after calibration.\n'
                   'The allowed flow is 1.0 ±0.1 litre.\n'
                   'Refer to the displayed status indicator.',
                   kind='ocr', page=1,
                   pdf_assessment={'route': 'vision', 'text_line_share': .4})
        assert not drawing_page(page, parsed, 1)


def test_spanish_test_appendix_requires_title_block_views_and_dimension_units():
    from agico_kb.drawing_index import assess

    texts = ['PRUEBA DE FLEXION', 'VISTA LATERAL', 'CORTEA - A',
             '100', '300', 'APOYOS DE NEOPRENO DE 3 mm DE ESPESOR',
             'ACOT: EN mm.', 'ANEXO No. 1', 'FIGURA No. 4']
    records = [{'text': t, 'region': [30+i%3*160, 50+i*75, 200+i%3*160, 70+i*75]}
               for i, t in enumerate(texts)]
    assert assess(records, 610, 783, scan=True)['content_type'] == 'drawing'
    records[6]['text'] = 'The annex and figure are discussed in this report'
    assert assess(records, 610, 783, scan=True)['content_type'] != 'drawing'
    records[6]['text'] = 'ACOT: EN mm.'
    records[7]['text'] = 'Appendix 1'
    assert assess(records, 610, 783, scan=True)['content_type'] != 'drawing'
