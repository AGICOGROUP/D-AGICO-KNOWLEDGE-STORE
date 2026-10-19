"""Fresh fixtures check drawing lookup without weakening ordinary PDF parsing."""

import hashlib
from types import SimpleNamespace

import ezdxf
import numpy as np
import pymupdf

from agico_kb.config import Settings
from agico_kb.ingestion import parse_file
from agico_kb.vision import enrich_pdf


def sheet(doc):
    page = doc.new_page(width=840, height=595)
    for i in range(60):
        page.draw_circle((40 + i % 10 * 45, 50 + i // 10 * 55), 15)
    for i, text in enumerate(['Diameter phi12', 'R20', 'R30', 'R40', '350', '380']):
        page.insert_text((60 + i * 85, 230), text)
    page.insert_text((520, 500), 'DRAWING NO: AG-731')
    page.insert_text((520, 520), 'TITLE: Kiln feed assembly')
    page.insert_text((35, 440), 'TECHNICAL NOTES: Use stainless steel.')
    page.insert_text((35, 460), 'PARTS LIST: Bolt M12 | Quantity 4 | Material steel')
    return page


def test_mixed_pdf_classifies_before_tables_and_preserves_prose(tmp_path):
    source = tmp_path / 'mixed.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        for i in range(12):
            page.insert_text((40, 50 + i * 20),
                             'The delivery contract requires inspection of diameter R20 and +/-0.3 mm.')
        sheet(doc)
        doc.save(source)
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    parsed = parse_file(source, source.name)
    prose = [c for c in parsed.chunks if c.locator.get('page') == 1]
    drawings = [c for c in parsed.chunks if c.locator.get('page') == 2]
    assert prose and all(c.locator.get('content_type') != 'drawing' for c in prose)
    assert 'delivery contract' in '\n'.join(c.text for c in prose)
    assert drawings and all(c.locator.get('kind') == 'drawing_index' for c in drawings)
    assert all(c.locator['requires_original'] for c in drawings)
    text = '\n'.join(c.text for c in drawings)
    assert 'AG-731' in text and 'Kiln feed assembly' in text
    assert 'stainless steel' in text and 'Quantity 4' in text
    assert not any(c.text.strip() in {'350', '380', 'R30'} for c in drawings)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


def test_identified_drawing_never_runs_full_dimension_transcription(tmp_path):
    source = tmp_path / 'fresh-sheet.pdf'
    with pymupdf.open() as doc:
        sheet(doc)
        doc.save(source)
    parsed = parse_file(source, source.name)
    config = tmp_path / 'key.txt'
    config.write_text('api=test-secret', encoding='utf-8')
    settings = Settings('unused', tmp_path / 'store', vision_config_file=config)

    def forbidden(*args):
        raise AssertionError('Native drawing index must not require full-page API transcription')

    enriched = enrich_pdf(parsed, source, settings, call_model=forbidden)
    assert enriched.status == 'ready'
    assert all(c.locator.get('kind') == 'drawing_index' for c in enriched.chunks)
    assert not any('未解释' in w for w in enriched.warnings)


def test_scan_title_block_plus_dimensions_routes_to_lookup(tmp_path, monkeypatch):
    source = tmp_path / 'scan.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=840, height=595)
        pix = np.full((700, 1000, 3), 255, dtype=np.uint8)
        page.insert_image(page.rect, pixmap=pymupdf.Pixmap(pymupdf.csRGB, 1000, 700,
                                                          pix.tobytes(), False))
        doc.save(source)

    def read(image):
        h, w = image.shape[:2]
        lines = [('R20', .1, .1), ('R30', .5, .2), ('R40', .8, .4),
                 ('DRAWING NO: AG-852', .65, .85), ('TITLE: Bearing cover', .65, .9),
                 ('NOTES: Material steel', .05, .7)]
        boxes = [[[x*w, y*h], [(x+.15)*w, y*h], [(x+.15)*w, (y+.02)*h],
                  [x*w, (y+.02)*h]] for _, x, y in lines]
        return SimpleNamespace(txts=[t for t, _, _ in lines], boxes=np.array(boxes),
                               scores=[.99] * len(lines))

    monkeypatch.setattr('agico_kb.ingestion._ocr_engine', lambda: read)
    parsed = parse_file(source, source.name)
    assert parsed.status == 'ready'
    assert all(c.locator.get('kind') == 'drawing_index' for c in parsed.chunks)
    assert 'AG-852' in '\n'.join(c.text for c in parsed.chunks)


def test_ambiguous_illustration_is_not_downgraded_to_drawing(tmp_path):
    source = tmp_path / 'illustrated-report.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        for i in range(60):
            page.draw_circle((30 + i % 10 * 30, 330 + i // 10 * 30), 6)
        for i in range(10):
            page.insert_text((40, 50 + i * 20),
                             'This technical report specifies R20 inspection and requirements for delivery.')
        page.insert_text((350, 800), 'DRAWING NO: appendix reference only')
        doc.save(source)
    parsed = parse_file(source, source.name)
    assert not any(c.locator.get('kind') == 'drawing_index' for c in parsed.chunks)
    assert 'technical report' in '\n'.join(c.text for c in parsed.chunks)


def test_cad_excludes_detached_dimensions_keeps_notes_and_origin(tmp_path):
    source = tmp_path / 'fresh.dxf'
    doc = ezdxf.new()
    ms = doc.modelspace()
    ms.add_text('DRAWING NO: K-19')
    ms.add_mtext('TECHNICAL NOTES: Material steel; torque 330 Nm')
    ms.add_text('330', dxfattribs={'insert': (50, 50)})
    ms.add_text('R20', dxfattribs={'insert': (50, 80)})
    doc.saveas(source)
    parsed = parse_file(source, source.name)
    text = '\n'.join(c.text for c in parsed.chunks)
    assert 'K-19' in text and 'torque 330 Nm' in text
    assert '\n330' not in text and '\nR20' not in text
    assert all(c.locator.get('requires_original') for c in parsed.chunks)
    assert all(c.locator.get('entities') for c in parsed.chunks)


def test_scan_index_coordinates_remain_inside_original_page(tmp_path, monkeypatch):
    source = tmp_path / 'scan-coordinates.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=840, height=595)
        image = np.full((700, 1000, 3), 255, dtype=np.uint8)
        page.insert_image(page.rect, pixmap=pymupdf.Pixmap(pymupdf.csRGB, 1000, 700,
                                                          image.tobytes(), False))
        doc.save(source)
    def read(image):
        h, w = image.shape[:2]
        texts = ['R20', 'R30', 'R40', 'DRAWING NO: Z-10', 'TITLE: Rotor']
        boxes = [[[w*.1, h*.1], [w*.2, h*.1], [w*.2, h*.12], [w*.1, h*.12]],
                 [[w*.4, h*.3], [w*.5, h*.3], [w*.5, h*.32], [w*.4, h*.32]],
                 [[w*.8, h*.5], [w*.9, h*.5], [w*.9, h*.52], [w*.8, h*.52]],
                 [[w*.6, h*.8], [w*.8, h*.8], [w*.8, h*.82], [w*.6, h*.82]],
                 [[w*.6, h*.9], [w*.8, h*.9], [w*.8, h*.92], [w*.6, h*.92]]]
        return SimpleNamespace(txts=texts, boxes=np.array(boxes), scores=[.99]*5)
    monkeypatch.setattr('agico_kb.ingestion._ocr_engine', lambda: read)
    parsed = parse_file(source, source.name)
    assert parsed.chunks and all(c.locator['kind'] == 'drawing_index' for c in parsed.chunks)
    assert all(0 <= c.locator['region'][0] <= c.locator['region'][2] <= 840
               and 0 <= c.locator['region'][1] <= c.locator['region'][3] <= 595
               for c in parsed.chunks)


def test_vector_site_plan_with_coordinates_uses_lookup_without_dimension_labels(tmp_path):
    source = tmp_path / 'site-plan.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=1000, height=700)
        for i in range(400):
            page.draw_circle((20 + i % 40 * 20, 30 + i // 40 * 30), 4)
        page.insert_text((40, 450), 'Product Storage Area')
        page.insert_text((400, 550), 'X=2328.00')
        page.insert_text((600, 600), 'Y=0891.50')
        page.insert_text((800, 650), 'AREA = 45000 M2')
        doc.save(source)
    parsed = parse_file(source, source.name)
    assert parsed.chunks and all(c.locator.get('kind') == 'drawing_index' for c in parsed.chunks)
    assert all(c.locator.get('extraction') == 'native' for c in parsed.chunks)
    assert 'Product Storage Area' in '\n'.join(c.text for c in parsed.chunks)


def test_scanned_tolerance_table_is_not_a_drawing_just_because_it_has_many_dimensions():
    from agico_kb.drawing_index import assess
    records = [{'text': f'phi{i} ±0.3', 'region': [40+j*180, 80+i*30, 140+j*180, 95+i*30]}
               for i in range(12) for j in range(3)]
    assert assess(records, 600, 800, scan=True)['content_type'] != 'drawing'


def test_cad_title_and_bom_keep_numeric_cell_values(tmp_path):
    source = tmp_path / 'bom.dxf'
    doc = ezdxf.new()
    ms = doc.modelspace()
    ms.add_text('DRAWING NO', dxfattribs={'layer': 'TITLE'})
    ms.add_text('731', dxfattribs={'layer': 'TITLE', 'insert': (15, 0)})
    ms.add_text('PARTS LIST: Bolt', dxfattribs={'layer': 'BOM'})
    ms.add_text('4', dxfattribs={'layer': 'BOM', 'insert': (50, 0)})
    doc.saveas(source)
    parsed = parse_file(source, source.name)
    text = '\n'.join(c.text for c in parsed.chunks)
    assert '731' in text and '\n4' in text


def test_drawing_with_native_title_still_reads_scanned_notes(tmp_path, monkeypatch):
    source = tmp_path / 'hybrid-drawing.pdf'
    with pymupdf.open() as doc:
        page = sheet(doc)
        image = np.full((600, 1000, 3), 255, dtype=np.uint8)
        page.insert_image(page.rect, pixmap=pymupdf.Pixmap(pymupdf.csRGB, 1000, 600,
                                                          image.tobytes(), False), overlay=False)
        doc.save(source)
    def read(image):
        h, w = image.shape[:2]
        return SimpleNamespace(txts=['PARTS LIST: Washer M12 | Quantity 8'],
                               boxes=np.array([[[w*.1, h*.3], [w*.7, h*.3],
                                                [w*.7, h*.32], [w*.1, h*.32]]]), scores=[.99])
    monkeypatch.setattr('agico_kb.ingestion._ocr_engine', lambda: read)
    parsed = parse_file(source, source.name)
    assert 'Quantity 8' in '\n'.join(c.text for c in parsed.chunks)
    assert all(c.locator['kind'] == 'drawing_index' for c in parsed.chunks)


def test_named_process_flow_sheet_does_not_need_dimension_marks(tmp_path):
    source = tmp_path / 'flow.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=840, height=595)
        for i in range(400):
            page.draw_circle((20+i % 40*20, 40+i//40*30), 3)
        page.insert_text((40, 50), 'PROCESS FLOW DIAGRAM: Limestone plant')
        page.insert_text((300, 280), 'Kiln feed')
        page.insert_text((650, 510), 'NOTES: Feed capacity 400 TPD')
        doc.save(source)
    parsed = parse_file(source, source.name)
    assert parsed.chunks and all(c.locator['kind'] == 'drawing_index' for c in parsed.chunks)
    assert all(c.locator['extraction'] == 'native' for c in parsed.chunks)


def test_pdf_parts_list_keeps_numeric_cells_below_header(tmp_path):
    source = tmp_path / 'parts-list.pdf'
    with pymupdf.open() as doc:
        page = sheet(doc)
        page.insert_text((520, 60), 'PARTS LIST')
        page.insert_text((520, 80), 'Description')
        page.insert_text((700, 80), 'Quantity')
        for y, name, count in [(110, 'Bolt', '7'), (145, 'Nut', '13'), (180, 'Washer', '26')]:
            page.insert_text((520, y), name)
            page.insert_text((700, y), count)
        doc.save(source)
    parsed = parse_file(source, source.name)
    body = '\n'.join(c.text for c in parsed.chunks)
    assert all(value in body.splitlines() for value in ['7', '13', '26'])
