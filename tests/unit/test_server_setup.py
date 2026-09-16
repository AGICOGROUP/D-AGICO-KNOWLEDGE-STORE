import json
from pathlib import Path

import pytest

from agico_kb.server_setup import installation_lock, load_deployment, write_json_once


def deployment(tmp_path):
    app = tmp_path / "application"
    data = tmp_path / "knowledge"
    app.mkdir()
    (data / "config").mkdir(parents=True)
    marker = {
        "schema_version": 1,
        "app_root": str(app),
        "data_root": str(data),
        "api_port": 18770,
        "database_port": 15433,
        "service_prefix": "AgicoVerify",
        "stage": "preparing",
    }
    (data / "config/deployment.json").write_text(json.dumps(marker), encoding="utf-8")
    return app, data, marker


def test_setup_rejects_unowned_or_changed_target(tmp_path):
    app, data, marker = deployment(tmp_path)
    assert load_deployment(app, data, 18770, 15433, "AgicoVerify") == marker
    with pytest.raises(ValueError):
        load_deployment(app, data, 18771, 15433, "AgicoVerify")
    marker["app_root"] = str(app.parent)
    (data / "config/deployment.json").write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(ValueError):
        load_deployment(app, data, 18770, 15433, "AgicoVerify")


def test_setup_credentials_are_create_once_and_atomic(tmp_path):
    path = tmp_path / "runtime.json"
    write_json_once(path, {"password": "first-test-placeholder"})
    with pytest.raises(FileExistsError):
        write_json_once(path, {"password": "second-test-placeholder"})
    assert json.loads(path.read_text())["password"] == "first-test-placeholder"
    assert list(tmp_path.iterdir()) == [path]


def test_initializer_refuses_concurrent_owner_and_releases_lock(tmp_path):
    (tmp_path / "config").mkdir()
    with (
        installation_lock(tmp_path),
        pytest.raises(RuntimeError, match="already running"),
        installation_lock(tmp_path),
    ):
        pytest.fail("second initializer acquired the same lock")
    with installation_lock(tmp_path):
        pass


def test_ocr_uses_configured_external_model_directory(tmp_path, monkeypatch):
    import rapidocr

    from agico_kb.ingestion import _ocr_engine

    captured = {}
    monkeypatch.setenv("AGICO_KB_MODEL_CACHE", str(tmp_path / "models"))
    monkeypatch.setattr(rapidocr, "RapidOCR", lambda **kwargs: captured.update(kwargs))
    _ocr_engine.cache_clear()
    try:
        _ocr_engine()
        assert Path(captured["params"]["Global.model_root_dir"]) == tmp_path / "models/ocr"
    finally:
        _ocr_engine.cache_clear()
