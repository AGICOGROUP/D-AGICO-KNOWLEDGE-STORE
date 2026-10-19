import numpy as np

from agico_kb.pdf_screening import text_scan_assessment


def scan():
    image = np.full((1200, 900, 3), 255, dtype=np.uint8)
    boxes, texts = [], []
    for y in range(150, 950, 45):
        image[y:y + 12, 100:750] = 0
        boxes.append([[95, y-3], [755, y-3], [755, y+15], [95, y+15]])
        texts.append('The supplier shall manufacture and test the equipment before delivery.')
    return image, boxes, texts, [.98]*len(texts)


def test_regular_high_confidence_scan_uses_ocr():
    assert text_scan_assessment(*scan())['route'] == 'ocr_text'


def test_diagram_beside_text_keeps_visual_route():
    image, boxes, texts, scores = scan()
    image[990:1100, 100:700] = 0
    assert text_scan_assessment(image, boxes, texts, scores)['route'] == 'vision'


def test_page_frame_and_identified_footer_do_not_require_visual_narration():
    import cv2
    image, boxes, texts, scores = scan()
    cv2.rectangle(image, (50, 50), (840, 1160), (0, 0, 0), 3)
    cv2.rectangle(image, (50, 1020), (840, 1160), (0, 0, 0), 3)
    boxes.append([[100, 1040], [500, 1040], [500, 1060], [100, 1060]])
    texts.append('CHOJA:3DE20ESPECIFICACION TECNICA')
    scores.append(.99)
    assert text_scan_assessment(image, boxes, texts, scores)['route'] == 'ocr_text'


def test_body_diagram_is_not_hidden_by_a_footer():
    import cv2
    image, boxes, texts, scores = scan()
    image[460:580, 300:720] = 0
    boxes.append([[100, 1040], [500, 1040], [500, 1060], [100, 1060]])
    texts.append('HOJA 3 DE 20 ESPECIFICACION TECNICA')
    scores.append(.99)
    cv2.rectangle(image, (50, 1020), (840, 1160), (0, 0, 0), 3)
    assert text_scan_assessment(image, boxes, texts, scores)['route'] == 'vision'


def test_footer_without_page_label_needs_two_metadata_fields():
    import cv2
    image, boxes, texts, scores = scan()
    cv2.rectangle(image, (50, 1020), (840, 1160), (0, 0, 0), 3)
    for y, text in [(1040, 'ESPECIFICACION TECNICA'), (1100, 'No. DE MODIFICACION: 0')]:
        boxes.append([[100, y], [600, y], [600, y+20], [100, y+20]])
        texts.append(text)
        scores.append(.99)
    assert text_scan_assessment(image, boxes, texts, scores)['route'] == 'ocr_text'


def test_single_specification_heading_does_not_hide_graphics():
    image, boxes, texts, scores = scan()
    image[1020:1160, 100:600] = 0
    boxes.append([[100, 1040], [600, 1040], [600, 1060], [100, 1060]])
    texts.append('ESPECIFICACION TECNICA')
    scores.append(.99)
    assert text_scan_assessment(image, boxes, texts, scores)['route'] == 'vision'


def test_thin_table_rules_require_structure_recognition():
    import cv2
    image, boxes, texts, scores = scan()
    cv2.rectangle(image, (100, 980), (720, 1130), (0, 0, 0), 1)
    cv2.line(image, (100, 1050), (720, 1050), (0, 0, 0), 1)
    assert text_scan_assessment(image, boxes, texts, scores)['route'] == 'vision'


def test_low_confidence_scan_falls_back_to_vision():
    image, boxes, texts, scores = scan()
    scores[3:7] = [.55]*4
    assert text_scan_assessment(image, boxes, texts, scores)['route'] == 'vision'


def test_short_low_confidence_number_cannot_hide_in_high_confidence_prose():
    image, boxes, texts, scores = scan()
    texts[3] = '350'
    scores[3] = .82
    assert text_scan_assessment(image, boxes, texts, scores)['route'] == 'vision'


def test_drawing_labels_are_not_regular_body_text():
    image, boxes, texts, scores = scan()
    texts = ['R25 620 +0.520 -0.450'] * len(texts)
    assert text_scan_assessment(image, boxes, texts, scores)['route'] == 'vision'


def test_missing_geometry_or_confidence_does_not_skip_api():
    image, boxes, texts, scores = scan()
    assert text_scan_assessment(image, boxes, texts, [])['route'] == 'vision'
    assert text_scan_assessment(image, [], texts, scores)['route'] == 'vision'


def test_screening_backend_failure_preserves_visual_fallback(monkeypatch):
    import cv2
    def fail(*args, **kwargs):
        raise cv2.error('invalid geometry')
    monkeypatch.setattr(cv2, 'fillPoly', fail)
    assert text_scan_assessment(*scan())['route'] == 'vision'


def test_formula_heavy_page_keeps_visual_route():
    image, boxes, texts, scores = scan()
    texts[2] = 'x = sqrt(y) + z / (1 + dt)'
    texts[3] = 'P = I * I * R'
    assert text_scan_assessment(image, boxes, texts, scores)['route'] == 'vision'


def test_enrichment_keeps_ocr_body_without_any_api_call(tmp_path):
    import pymupdf

    from agico_kb.config import Settings
    from agico_kb.ingestion import Parsed
    from agico_kb.vision import enrich_pdf
    path = tmp_path/'scan.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40))
        pix.clear_with(240)
        page.insert_image(page.rect, stream=pix.tobytes('png'))
        doc.save(path)
    config = tmp_path/'vision.txt'
    config.write_text('api=test-secret\n', encoding='utf-8')
    parsed = Parsed(status='partial', warnings=[
        'PDF 第 1 页为扫描页，已 OCR 识别；关键数字请核对原文件。',
        'OCR 识别文字未经人工核对；表格结构与图形语义可能不完整，关键数字请核对原文件。'])
    parsed.add('The supplier shall test all products before delivery.', kind='ocr', page=1,
               pdf_assessment={'route':'ocr_text'})
    def forbidden(*args):
        raise AssertionError('Prose must not call the API')
    enriched = enrich_pdf(parsed, path, Settings('test', tmp_path, vision_config_file=config), call_model=forbidden)
    assert enriched.status == 'ready'
    assert enriched.chunks[0].text.startswith('The supplier')
    assert not any('未解释' in w for w in enriched.warnings)
