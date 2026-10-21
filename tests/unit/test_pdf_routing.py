import pymupdf

from agico_kb.config import Settings
from agico_kb.ingestion import parse_file
from agico_kb.vision import enrich_pdf


def settings(tmp_path):
    config = tmp_path / 'vision.txt'
    config.write_text('api=test-secret\n', encoding='utf-8')
    return Settings('test', tmp_path, vision_config_file=config)


def test_outlined_vector_drawing_routes_to_ocr_and_vision(tmp_path, monkeypatch):
    path = tmp_path / 'drawing.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=1600, height=1000)
        page.insert_text((40, 40), 'Process flow')
        for i in range(90):
            x, y = 50 + i % 15 * 80, 100 + i // 15 * 100
            page.draw_circle((x, y), 12)
        doc.save(path)
    calls = []
    def ocr(page, parsed, index, drop=frozenset()):
        calls.append(index)
        parsed.add('300 t/day rotary kiln', kind='ocr', page=index)
    monkeypatch.setattr('agico_kb.ingestion._ocr_page', ocr)
    parsed = parse_file(path, path.name)
    assert calls == [1]
    assert parsed.status == 'partial'
    parsed = enrich_pdf(parsed, path, settings(tmp_path), call_model=lambda *_: 'Flow connects the kiln and cooler.')
    assert any(c.locator['kind'] == 'page_visual' for c in parsed.chunks)


def test_pdf_vision_inventory_is_not_limited_to_twenty_display_warnings(tmp_path, monkeypatch):
    monkeypatch.setattr('agico_kb.vision._has_image_text', lambda _: True)
    path = tmp_path / 'long.pdf'
    with pymupdf.open() as doc:
        for i in range(23):
            page = doc.new_page()
            page.insert_textbox(pymupdf.Rect(40, 80, 560, 450), (f'Readable native text on page {i} with enough content. ' * 25))
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20))
            pix.clear_with(255)
            page.insert_image(pymupdf.Rect(50, 500, 450, 740), stream=pix.tobytes('png'))
        doc.save(path)
    parsed = parse_file(path, path.name)
    assert len(parsed.warnings) == 23  # Every page gap retained; no generic table note/truncation.
    from agico_kb.vision import VisionDeferred
    for _ in range(12):
        parsed = parse_file(path, path.name)
        try:
            parsed = enrich_pdf(parsed, path, settings(tmp_path), call_model=lambda *_: 'Diagram of equipment.')
            break
        except VisionDeferred:
            continue
    else:
        raise AssertionError('PDF did not finish across bounded continuation passes')
    assert sum(c.locator['kind'] == 'page_visual' for c in parsed.chunks) == 23
    assert not any('未展开' in w for w in parsed.warnings)


def test_plain_table_does_not_trigger_vector_vision(tmp_path):
    path = tmp_path / 'table.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        for y in range(100, 701, 10):
            page.draw_line((40, y), (550, y))
        for x in range(40, 551, 30):
            page.draw_line((x, 100), (x, 700))
        page.insert_text((50, 90), 'A plain table with values')
        doc.save(path)
    parsed = parse_file(path, path.name)
    assert not any('图形' in w for w in parsed.warnings)


def test_layout_spaces_do_not_disable_outlined_text_ocr(tmp_path, monkeypatch):
    path = tmp_path / 'spaces.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=3400, height=2400)
        page.insert_text((30, 30), 'M')
        page.insert_text((3300, 2300), 'Company')
        for i in range(50):
            page.draw_circle((100 + i * 50, 300), 12)
        doc.save(path)
    with pymupdf.open(path) as doc:
        assert len(doc[0].get_text(sort=True)) > 400
    calls = []
    monkeypatch.setattr('agico_kb.ingestion._ocr_page', lambda page, parsed, index, **kwargs: calls.append(index))
    parse_file(path, path.name)
    assert calls == [1]


def test_scan_base_is_ocrd_even_with_long_native_annotation(tmp_path, monkeypatch):
    path = tmp_path / 'overlay.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100))
        pix.clear_with(180)
        page.insert_image(page.rect, stream=pix.tobytes('png'))
        page.insert_textbox(pymupdf.Rect(20, 20, 550, 500), 'Translation annotation ' * 100)
        doc.save(path)
    calls = []
    def ocr(page, parsed, index, **kwargs):
        calls.append(index)
        parsed.add('620 mm base drawing', kind='ocr', page=index)
    monkeypatch.setattr('agico_kb.ingestion._ocr_page', ocr)
    parsed = parse_file(path, path.name)
    assert calls == [1]
    assert any('Translation annotation' in c.text for c in parsed.chunks)
    assert any('620 mm' in c.text for c in parsed.chunks)


def test_pdf_per_pass_limit_resumes_uncached_regions(tmp_path, monkeypatch):
    from dataclasses import replace

    import pytest

    from agico_kb.vision import VisionDeferred
    monkeypatch.setattr('agico_kb.vision._has_image_text', lambda _: True)
    path = tmp_path / 'resume.pdf'
    with pymupdf.open() as doc:
        for i in range(3):
            page = doc.new_page()
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100))
            pix.clear_with(100 + i * 30)
            page.insert_image(page.rect, stream=pix.tobytes('png'))
            page.insert_text((30, 30), f'Page {i}')
        doc.save(path)
    cfg = replace(settings(tmp_path), vision_max_images_per_file=1)
    calls = []
    def model(*args):
        calls.append(1)
        return 'Dimensions 620 mm'
    for _ in range(5):
        with pytest.raises(VisionDeferred) as exc:
            enrich_pdf(parse_file(path, path.name), path, cfg, call_model=model)
        assert exc.value.delay_seconds == 30
    parsed = enrich_pdf(parse_file(path, path.name), path, cfg, call_model=model)
    assert len(calls) == 6
    assert {c.locator['page'] for c in parsed.chunks if c.locator['kind'] == 'page_visual'} == {1, 2, 3}
    assert not any('未解释' in w for w in parsed.warnings)


def test_pdf_daily_budget_defers_without_calling_model(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    import pytest

    from agico_kb.vision import VisionDeferred, _store
    monkeypatch.setattr('agico_kb.vision._has_image_text', lambda _: True)
    path = tmp_path / 'quota.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        for i in range(50):
            page.draw_circle((50 + i % 10 * 40, 100 + i // 10 * 80), 10)
        doc.save(path)
    from dataclasses import replace
    cfg = replace(settings(tmp_path), vision_max_calls_per_day=120)
    conn = _store(cfg.storage_root)
    conn.execute('INSERT INTO daily_calls VALUES (?,?)', (datetime.now(UTC).date().isoformat(), cfg.vision_max_calls_per_day))
    conn.commit()
    conn.close()
    calls = []
    with pytest.raises(VisionDeferred) as exc:
        enrich_pdf(parse_file(path, path.name), path, cfg, call_model=lambda *_: calls.append(1))
    assert 1 <= exc.value.delay_seconds <= 86405
    assert calls == []


def test_large_visual_regions_preserve_resolution_and_cover_source(tmp_path):
    from agico_kb.vision import _pdf_visual_regions
    with pymupdf.open() as doc:
        page = doc.new_page(width=3400, height=2400)
        scale, regions = _pdf_visual_regions(page)
        assert scale >= 2
        assert len(regions) <= 64
        assert all(max(r.width, r.height) * scale <= 2001 for r in regions)
        assert regions[0].tl == page.rect.tl
        assert regions[-1].br == page.rect.br


def test_pdf_transient_api_failure_defers_and_reports_page_progress(tmp_path, monkeypatch):
    import httpx
    import pytest

    from agico_kb.vision import VisionDeferred
    monkeypatch.setattr('agico_kb.vision._has_image_text', lambda _: True)
    monkeypatch.setattr('agico_kb.vision.time.sleep', lambda _: None)
    path = tmp_path / 'temporary.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        for i in range(50):
            page.draw_circle((50 + i % 10 * 40, 100 + i // 10 * 80), 10)
        doc.save(path)
    updates = []
    def fail(*args):
        raise httpx.ReadTimeout('temporary')
    with pytest.raises(VisionDeferred) as exc:
        enrich_pdf(parse_file(path, path.name), path, settings(tmp_path), call_model=fail, progress=updates.append)
    assert exc.value.delay_seconds == 30
    assert updates[0]['page'] == 1 and updates[0]['pages'] == 1
