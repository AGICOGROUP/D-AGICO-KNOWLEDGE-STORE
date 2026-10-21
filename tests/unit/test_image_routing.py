from types import SimpleNamespace

from PIL import Image

from agico_kb.config import Settings
from agico_kb.ingestion import parse_file
from agico_kb.vision import enrich_image


def test_text_scan_uses_ocr_without_api(tmp_path, monkeypatch):
    path = tmp_path / 'scan.png'
    Image.new('RGB', (500, 700), 'white').save(path)
    output = SimpleNamespace(txts=['Readable source specification 1435 mm'], boxes=None, scores=None)
    monkeypatch.setattr('agico_kb.ingestion._ocr_engine', lambda: lambda _: output)
    monkeypatch.setattr('agico_kb.pdf_screening.text_scan_assessment', lambda *_: {'route':'ocr_text', 'footer_excluded':False})
    parsed = parse_file(path, path.name)
    def forbidden(*_):
        raise AssertionError('Ordinary OCR prose must not call vision')
    config = tmp_path / 'vision.txt'
    config.write_text('api=test-key')
    enrich_image(parsed, path, Settings('unused', tmp_path, vision_config_file=config), call_model=forbidden)
    assert parsed.status == 'ready'
    assert [c.text for c in parsed.chunks] == output.txts
    assert parsed.chunks[0].locator['image_assessment']['route'] == 'ocr_text'


def test_no_text_is_stored_without_vision_config(tmp_path, monkeypatch):
    path = tmp_path / 'photo.png'
    Image.new('RGB', (500, 700), 'blue').save(path)
    monkeypatch.setattr('agico_kb.ingestion._ocr_engine', lambda: lambda _: SimpleNamespace(txts=[], boxes=None, scores=None))
    parsed = parse_file(path, path.name)
    assert parsed.status == 'stored_only'
    assert not parsed.chunks
    assert not any('失败' in w for w in parsed.warnings)


def test_diagram_keeps_vision_fallback(tmp_path, monkeypatch):
    path = tmp_path / 'diagram.png'
    Image.new('RGB', (500, 700), 'white').save(path)
    output = SimpleNamespace(txts=['AX-210'], boxes=None, scores=None)
    monkeypatch.setattr('agico_kb.ingestion._ocr_engine', lambda: lambda _: output)
    monkeypatch.setattr('agico_kb.pdf_screening.text_scan_assessment', lambda *_: {'route':'vision'})
    monkeypatch.setattr('agico_kb.vision._has_image_text', lambda _: True)
    config = tmp_path / 'vision.txt';config.write_text('api=test-key')
    calls=[]
    parsed=parse_file(path,path.name)
    enrich_image(parsed,path,Settings('unused',tmp_path,vision_config_file=config),call_model=lambda *_: calls.append(1) or 'AX-210 connected to outlet')
    assert calls == [1]
    assert any(c.locator['kind']=='image_description' for c in parsed.chunks)
