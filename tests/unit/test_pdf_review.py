from agico_kb.config import Settings
from agico_kb.ingestion import Parsed
from agico_kb.vision import _prefer_pdf_visual, _review_pdf_region


def test_review_uses_image_and_draft_and_separate_cache(tmp_path):
    from io import BytesIO

    from PIL import Image
    config = tmp_path / "key.txt"
    config.write_text("api=test-secret")
    settings = Settings("unused", tmp_path / "storage", vision_config_file=config)
    out = BytesIO()
    Image.new("RGB", (100,100), "white").save(out, format="PNG")
    calls=[]
    def model(key, model, image, prompt):
        calls.append(prompt)
        assert "45°" in prompt
        return "轨脚厚度 16±0.3 mm；原图未给角度。"
    first=_review_pdf_region(out.getvalue(), "角度45°", settings, call_model=model)
    again=_review_pdf_region(out.getvalue(), "角度45°", settings, call_model=model)
    assert first==again and "16±0.3" in first
    assert len(calls)==1


def test_visual_replaces_ocr_only_when_entire_page_is_transcribed():
    parsed=Parsed()
    parsed.add("54.2", kind="ocr", page=1)
    parsed.add("retain second page", kind="ocr", page=2)
    parsed.add("native caption", kind="page", page=1)
    parsed.add("64.2 mm", kind="page_visual", page=1)
    _prefer_pdf_visual(parsed,{1})
    assert not any(c.locator.get("kind")=="ocr" and c.locator.get("page")==1 for c in parsed.chunks)
    assert any(c.text=="retain second page" for c in parsed.chunks)
    assert any(c.text=="native caption" for c in parsed.chunks)
