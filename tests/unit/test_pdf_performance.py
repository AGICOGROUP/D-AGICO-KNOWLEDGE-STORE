import threading
import time

import pymupdf

from agico_kb.config import Settings
from agico_kb.ingestion import Parsed
from agico_kb.vision import _pdf_visual_regions, enrich_pdf


def test_regular_paper_page_keeps_readable_resolution_without_margin_tiles():
    with pymupdf.open() as doc:
        page = doc.new_page(width=724, height=1033)
        scale, regions = _pdf_visual_regions(page)
        assert len(regions) == 1
        assert scale == 200/72
        assert regions[0] == page.rect


def test_pdf_can_use_fast_model_for_both_transcription_and_source_review(tmp_path, monkeypatch):
    from dataclasses import replace

    from agico_kb.vision import PDF_REVIEW_PROMPT
    path, settings = one_page_scan(tmp_path)
    settings = replace(settings, vision_pdf_model='gemini-3.8-flash')
    monkeypatch.setattr('agico_kb.vision._has_image_text', lambda _: True)
    calls = []
    def model(key, selected, image, prompt):
        calls.append((selected, prompt))
        return 'Verified 620 mm'
    parsed = enrich_pdf(Parsed(), path, settings, call_model=model)
    assert [m for m, _ in calls] == ['gemini-3.8-flash'] * 2
    assert calls[1][1].startswith(PDF_REVIEW_PROMPT)
    assert len(parsed.chunks) == 1
    enrich_pdf(Parsed(), path, settings, call_model=lambda *_: (_ for _ in ()).throw(AssertionError('completed review should resume')))


def test_large_engineering_sheet_still_uses_readable_tiles():
    with pymupdf.open() as doc:
        page = doc.new_page(width=2400, height=1600)
        _, regions = _pdf_visual_regions(page)
        assert len(regions) > 1


def test_pdf_balance_failure_never_returns_partial_as_completed(tmp_path, monkeypatch):
    import pytest

    from agico_kb.vision import VisionQuotaError
    path, settings = one_page_scan(tmp_path)
    monkeypatch.setattr('agico_kb.vision._has_image_text', lambda _: True)
    def unavailable(*args):
        raise VisionQuotaError()
    with pytest.raises(VisionQuotaError):
        enrich_pdf(Parsed(), path, settings, call_model=unavailable)


def test_pages_transcribe_concurrently_and_keep_second_review(tmp_path, monkeypatch):
    monkeypatch.setattr('agico_kb.vision._has_image_text', lambda _: True)
    path = tmp_path/'five.pdf'
    with pymupdf.open() as doc:
        for i in range(5):
            page = doc.new_page()
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0,0,40,40))
            pix.clear_with(60+i*20)
            page.insert_image(page.rect, stream=pix.tobytes('png'))
        doc.save(path)
    config = tmp_path/'key.txt'
    config.write_text('api=test-secret')
    settings = Settings('unused', tmp_path/'storage', vision_config_file=config)
    active = peak = calls = 0
    lock = threading.Lock()
    def model(*args):
        nonlocal active, peak, calls
        with lock:
            active += 1; peak = max(peak, active); calls += 1
        time.sleep(.05)
        with lock: active -= 1
        return 'Specification: 620 mm'
    parsed = enrich_pdf(Parsed(), path, settings, call_model=model)
    assert peak > 1 and peak <= 5
    assert calls == 10
    assert {c.locator['page'] for c in parsed.chunks} == set(range(1,6))
    assert [c.locator['page'] for c in parsed.chunks] == list(range(1,6))
    assert not any('未解释' in w for w in parsed.warnings)


def test_performance_upgrade_keeps_same_source_checkpoint_only():
    from agico_kb.worker import _native_checkpoint_matches
    current = 'a'*64 + 'native-v6-ocr-paper'
    assert _native_checkpoint_matches('a'*64 + 'native-v5-ocr-prose-v2', current)
    assert not _native_checkpoint_matches('b'*64 + 'native-v5-ocr-prose-v2', current)
    assert not _native_checkpoint_matches('a'*64 + 'native-v4-ocr-prose', current)


def test_ordinary_scan_ocr_is_one_full_resolution_page(monkeypatch):
    from agico_kb.ingestion import _ocr_page
    calls = []
    def ocr(array, parsed, **locator):
        calls.append((array.shape, locator))
    monkeypatch.setattr('agico_kb.ingestion._ocr', ocr)
    with pymupdf.open() as doc:
        page = doc.new_page(width=724, height=1033)
        _ocr_page(page, Parsed(), 1)
    assert len(calls) == 1
    assert calls[0][0][0] > 4000
    assert 'tile' not in calls[0][1]


def test_saved_crop_geometry_is_resumed_without_reinterpreting_as_whole_page(tmp_path):
    import hashlib

    from agico_kb.vision import PDF_INTEGRITY_VERSION, _credentials, _store
    path = tmp_path/'paper.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=724, height=1033)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0,0,40,40))
        pix.clear_with(100)
        page.insert_image(page.rect, stream=pix.tobytes('png'))
        doc.save(path)
    config = tmp_path/'key.txt'
    config.write_text('api=test-secret')
    settings = Settings('unused', tmp_path/'storage', vision_config_file=config)
    _, model = _credentials(config)
    file_key = hashlib.sha256(path.read_bytes()+model.encode()+PDF_INTEGRITY_VERSION).hexdigest()
    with pymupdf.open(path) as doc:
        scale, regions = _pdf_visual_regions(doc[0], max_side=2400)
    with _store(settings.storage_root) as conn:
        conn.execute('CREATE TABLE pdf_regions (key TEXT PRIMARY KEY, summary TEXT NOT NULL)')
        for i in range(1,len(regions)+1):
            conn.execute('INSERT INTO pdf_regions VALUES (?,?)',
                         (f'{file_key}:pdf-region-reviewed-v5:1:{i}:{scale:.8f}', f'Saved region {i}'))
    def forbidden(*args):
        raise AssertionError('Completed regions must not be sent again')
    parsed = enrich_pdf(Parsed(), path, settings, call_model=forbidden)
    assert len(parsed.chunks) == 4
    assert [c.locator['region'] for c in parsed.chunks] == [[round(v,2) for v in r] for r in regions]


def test_global_request_slots_are_shared_between_files(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from agico_kb.vision import _store, _vision_call
    active = peak = 0
    lock = threading.Lock()
    settings = Settings('unused', tmp_path)
    def invoke():
        nonlocal active, peak
        with lock: active += 1; peak = max(peak,active)
        time.sleep(.04)
        with lock: active -= 1
        return 'ok'
    def run(i):
        with _store(tmp_path/str(i)) as conn:
            return _vision_call(conn, settings, invoke)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(run,range(8))) == ['ok']*8
    assert 1 < peak <= 5


def one_page_scan(tmp_path):
    path = tmp_path/'scan.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0,0,40,40))
        pix.clear_with(100)
        page.insert_image(page.rect, stream=pix.tobytes('png'))
        doc.save(path)
    config = tmp_path/'key.txt'
    config.write_text('api=test-secret')
    return path, Settings('unused', tmp_path/'storage', vision_config_file=config)


def test_active_region_can_finish_review_after_admission_window(tmp_path, monkeypatch):
    from agico_kb.vision import PDF_REVIEW_PROMPT
    path, settings = one_page_scan(tmp_path)
    monkeypatch.setattr('agico_kb.vision._has_image_text', lambda _: True)
    clock = [0]
    monkeypatch.setattr('agico_kb.vision.time.monotonic', lambda: clock[0])
    calls = []
    def model(key, model, image, prompt):
        calls.append(prompt)
        clock[0] = 130
        return 'Dimensions 620 mm'
    parsed = enrich_pdf(Parsed(), path, settings, call_model=model)
    assert len(calls) == 2
    assert calls[1].startswith(PDF_REVIEW_PROMPT)
    assert len(parsed.chunks) == 1


def test_transient_review_retry_reuses_screened_pixels_without_repeating_ocr(tmp_path, monkeypatch):
    import httpx
    import pytest

    from agico_kb import vision
    from agico_kb.vision import PDF_REVIEW_PROMPT, VisionDeferred, _store
    path, settings = one_page_scan(tmp_path)
    monkeypatch.setattr('agico_kb.vision._has_image_text', lambda _: True)
    render = vision._pdf_text_image_render
    renders = []
    def counted(*args, **kwargs):
        renders.append(1)
        return render(*args, **kwargs)
    monkeypatch.setattr('agico_kb.vision._pdf_text_image_render', counted)
    def failure(key, model, image, prompt):
        if prompt.startswith(PDF_REVIEW_PROMPT):
            raise httpx.ReadTimeout('temporary test failure')
        return 'Dimensions 620 mm'
    with pytest.raises(VisionDeferred):
        enrich_pdf(Parsed(), path, settings, call_model=failure)
    parsed = enrich_pdf(Parsed(), path, settings, call_model=lambda *_: 'Dimensions 620 mm')
    assert len(renders) == 1
    assert len(parsed.chunks) == 1
    with _store(settings.storage_root) as conn:
        assert conn.execute('SELECT count(*) FROM pdf_inputs').fetchone()[0] == 0


def test_truncated_output_retries_with_larger_budget_and_never_caches_partial(tmp_path, monkeypatch):
    import json
    from io import BytesIO

    import httpx
    from PIL import Image

    from agico_kb.vision import PDF_TRANSCRIPTION_PROMPT, _call_model, _describe, _store
    _, settings = one_page_scan(tmp_path)
    image = BytesIO()
    Image.new('RGB', (40,40), 'gray').save(image,format='PNG')
    from dataclasses import replace
    settings = replace(settings, vision_fallback_model="")
    budgets = []
    def response(request):
        payload = json.loads(request.content)
        budgets.append(payload['max_tokens'])
        return httpx.Response(200, json={'choices':[{'message':{'content':'PARTIAL' if len(budgets)==1 else 'Complete 620 mm'},
                                                    'finish_reason':'length' if len(budgets)==1 else 'stop'}]})
    factory = httpx.Client
    monkeypatch.setattr('agico_kb.vision.httpx.Client', lambda **kwargs: factory(transport=httpx.MockTransport(response),**kwargs))
    result = _describe(image.getvalue(), settings, version='pdf-budget-test', prompt=PDF_TRANSCRIPTION_PROMPT,
                       call_model=_call_model, screen=False)
    assert result == 'Complete 620 mm'
    assert budgets == [4096,8192]
    with _store(settings.storage_root) as conn:
        assert conn.execute('SELECT summary FROM summaries').fetchall() == [('Complete 620 mm',)]
        assert conn.execute('SELECT tokens FROM output_budgets').fetchone()[0] == 8192


def test_exhausted_provider_output_limit_is_not_an_identical_retry_loop():
    from agico_kb.vision import VisionOutputTruncated, _retryable
    assert _retryable(VisionOutputTruncated(16384))
    assert not _retryable(VisionOutputTruncated(131072))


def test_repeated_truncation_splits_source_with_overlap_and_reviews_each_part(tmp_path, monkeypatch):
    import hashlib
    import io

    from PIL import Image

    from agico_kb.vision import _credentials, _store
    path, settings = one_page_scan(tmp_path)
    output = io.BytesIO()
    source_image = Image.new('RGB', (200, 600), 'gray')
    source_image.paste('blue', (0, 0, 200, 300))
    source_image.save(output, format='PNG')
    blob = output.getvalue()
    monkeypatch.setattr('agico_kb.vision._pdf_text_image_render', lambda *_args, **_kwargs: (blob, True))
    monkeypatch.setattr('agico_kb.vision._has_image_text', lambda _: True)
    _, model_name = _credentials(settings.vision_config_file)
    digest = hashlib.sha256(model_name.encode() + b'pdf-region-transcription-v4' + blob).hexdigest()
    with _store(settings.storage_root) as conn:
        conn.execute('INSERT INTO output_budgets VALUES (?,?)', (digest, 65536))
        conn.commit()
    sizes = []
    def model(_key, _name, image, _prompt):
        with Image.open(io.BytesIO(image)) as im:
            sizes.append(im.size)
        return 'Verified dimensions 620 mm'
    parsed = enrich_pdf(Parsed(), path, settings, call_model=model)
    assert sizes == [(200, 360)] * 4  # Two overlapping halves, each independently reviewed.
    assert len(parsed.chunks) == 1
    assert parsed.chunks[0].text.count('Verified dimensions 620 mm') == 2
    assert not any('未解释' in warning for warning in parsed.warnings)
    enrich_pdf(Parsed(), path, settings, call_model=lambda *_: (_ for _ in ()).throw(AssertionError('cached region must be reused')))


def test_repeated_pdf_truncation_yields_before_another_whole_page_attempt(tmp_path, monkeypatch):
    import io
    import json

    import httpx
    import pytest
    from PIL import Image

    from agico_kb.vision import (
        PDF_TRANSCRIPTION_PROMPT,
        VisionDeferred,
        _call_model,
        _describe,
        _store,
    )
    _, settings = one_page_scan(tmp_path)
    output = io.BytesIO()
    Image.new('RGB', (60, 80), 'blue').save(output, format='PNG')
    from dataclasses import replace
    settings = replace(settings, vision_fallback_model="")
    budgets = []
    def response(request):
        budgets.append(json.loads(request.content)['max_tokens'])
        return httpx.Response(200, json={'choices': [{'message': {'content': 'partial'}, 'finish_reason': 'length'}]})
    factory = httpx.Client
    monkeypatch.setattr('agico_kb.vision.httpx.Client', lambda **kwargs: factory(transport=httpx.MockTransport(response), **kwargs))
    with pytest.raises(VisionDeferred):
        _describe(output.getvalue(), settings, version='pdf-region-transcription-v4',
                  prompt=PDF_TRANSCRIPTION_PROMPT, call_model=_call_model, screen=False)
    assert budgets == [4096, 8192]
    with _store(settings.storage_root) as conn:
        assert conn.execute('SELECT tokens FROM output_budgets').fetchone()[0] == 16384
        assert conn.execute('SELECT count(*) FROM summaries').fetchone()[0] == 0


def test_pdf_truncation_switches_model_and_requires_independent_source_review(tmp_path, monkeypatch):
    import hashlib
    import io
    import json

    import httpx
    from PIL import Image

    from agico_kb.vision import PDF_REVIEW_PROMPT, _credentials, _store
    path, settings = one_page_scan(tmp_path)
    output = io.BytesIO()
    Image.new('RGB', (200, 400), 'gray').save(output, format='PNG')
    blob = output.getvalue()
    monkeypatch.setattr('agico_kb.vision._pdf_text_image_render', lambda *_args, **_kwargs: (blob, True))
    monkeypatch.setattr('agico_kb.vision._has_image_text', lambda _: True)
    _, primary = _credentials(settings.vision_config_file)
    digest = hashlib.sha256(primary.encode() + b'pdf-region-transcription-v4' + blob).hexdigest()
    with _store(settings.storage_root) as conn:
        conn.execute('INSERT INTO output_budgets VALUES (?,?)', (digest, 32768))
        conn.commit()
    requests = []
    def response(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Reviewed 620 mm'}, 'finish_reason': 'stop'}]})
    factory = httpx.Client
    monkeypatch.setattr('agico_kb.vision.httpx.Client', lambda **kwargs: factory(transport=httpx.MockTransport(response), **kwargs))
    parsed = enrich_pdf(Parsed(), path, settings)
    assert [r['model'] for r in requests] == [settings.vision_fallback_model] * 2
    assert requests[1]['messages'][1]['content'][0]['text'].startswith(PDF_REVIEW_PROMPT)
    assert len(parsed.chunks) == 1
    with _store(settings.storage_root) as conn:
        assert conn.execute('SELECT model FROM pdf_region_models').fetchone()[0] == settings.vision_fallback_model
    enrich_pdf(Parsed(), path, settings)
    assert len(requests) == 2


def test_alternate_provider_output_limit_is_respected(monkeypatch):
    import json

    import httpx
    import pytest

    from agico_kb.vision import VisionOutputTruncated, _call_model, _retryable
    budgets = []
    def response(request):
        budgets.append(json.loads(request.content)['max_tokens'])
        return httpx.Response(200, json={'choices': [{'message': {'content': 'partial'}, 'finish_reason': 'length'}]})
    factory = httpx.Client
    monkeypatch.setattr('agico_kb.vision.httpx.Client', lambda **kwargs: factory(transport=httpx.MockTransport(response), **kwargs))
    with pytest.raises(VisionOutputTruncated) as error:
        _call_model('test', 'gemini-3.8-flash', b'png', max_tokens=131072)
    assert budgets == [65536]
    assert error.value.exhausted
    assert not _retryable(error.value)
