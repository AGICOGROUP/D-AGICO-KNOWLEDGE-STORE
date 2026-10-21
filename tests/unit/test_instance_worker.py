"""An independently deployed API must have a worker using that same instance."""
import importlib.util
import json
import os
from pathlib import Path

import pytest


def load_launcher():
    path = Path(__file__).resolve().parents[2] / 'deploy/windows/run-instance-worker.py'
    assert path.is_file(), 'Independent instance worker launcher is missing'
    spec = importlib.util.spec_from_file_location('instance_worker', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_instance_worker_and_parser_children_use_same_source_and_database(tmp_path, monkeypatch):
    source = tmp_path / 'instance' / 'src'
    source.mkdir(parents=True)
    originals = tmp_path / 'originals'
    originals.mkdir()
    config = tmp_path / 'runtime.json'
    config.write_text(json.dumps({'AGICO_KB_DATABASE_URL': 'fresh-isolated-test-database',
                                 'AGICO_KB_STORAGE_ROOT': str(originals),
                                 'AGICO_KB_MODEL_CACHE': str(tmp_path / 'models'),
                                 'AGICO_KB_MODEL_OFFLINE': 'true',
                                 'AGICO_KB_EXPECTED_MODEL_IDENTITY': 'fresh-model'}))
    monkeypatch.setenv('AGICO_KB_DATABASE_URL', 'wrong-inherited-database')
    monkeypatch.setenv('PYTHONPATH', 'wrong-inherited-source')
    monkeypatch.setattr(os, 'environ', os.environ.copy())
    monkeypatch.setattr('sys.path', list(__import__('sys').path))
    monkeypatch.chdir(tmp_path)
    module = load_launcher()
    module.configure(source.parent, config)
    assert os.environ['AGICO_KB_DATABASE_URL'] == 'fresh-isolated-test-database'
    assert os.environ['AGICO_KB_STORAGE_ROOT'] == str(originals)
    assert os.environ['PYTHONPATH'] == str(source)
    assert __import__('sys').path[0] == str(source)
    assert Path.cwd() == source.parent


def test_incomplete_instance_config_does_not_start_worker(tmp_path):
    source = tmp_path / 'src'
    source.mkdir()
    config = tmp_path / 'runtime.json'
    config.write_text(json.dumps({'AGICO_KB_DATABASE_URL': 'fresh-fixture'}))
    module = load_launcher()
    with pytest.raises(ValueError, match='Incomplete'):
        module.configure(tmp_path, config)
