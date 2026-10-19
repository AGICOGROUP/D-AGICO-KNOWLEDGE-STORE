from pathlib import Path

import pytest

from agico_kb import naming


@pytest.mark.parametrize('filename', [
    '菏泽双龙_工艺设备表_燃烧系统_ZH.xls',
    'AGICO_产品宣传册_EN.pdf',
    'Cedar Plant_Equipment List_20261015_EN_V2.xlsx',
    '回转窑_RK-081_总装图_ZH_R1.dwg',
    '石灰煅烧厂_总平面布置图.pdf',
])
def test_compliant_name_skips_model_and_opening_reads(monkeypatch, filename):
    def forbidden(*args, **kwargs):
        pytest.fail('Compliant names must not invoke model, reference lookup or opening reads')

    monkeypatch.setattr(naming, '_references', forbidden)
    monkeypatch.setattr(naming, '_bounded_content_hint', forbidden)
    result = naming._suggest_batch(None, None, 'org', 'author', [filename],
                                  call_model=forbidden, source_paths=[Path('not-read')])[0]
    assert result['title'] == filename
    assert result['needs_review'] is False
    assert result['project'] == ''  # A filename cannot create a project registration.
    assert result['method'] == 'filename_rules'


@pytest.mark.parametrize('filename', [
    'scan001.pdf', '对象_文件.pdf', '未知_设备清单.pdf', '对象__设备清单.pdf',
    '对象_设备清单_20260230_ZH.pdf', '对象_设备清单_ZH_20261015.pdf',
    '对象_设备清单_ZH_FR.pdf', '../对象_设备清单.pdf',
    '对象_设备清单_待确认.pdf', '对象_设备清单_中文.pdf',
])
def test_ambiguous_or_malformed_name_keeps_ai_route(filename):
    assert naming.compliant_filename(filename) is None


def test_mixed_batch_only_sends_unqualified_names_to_model(monkeypatch):
    calls = []
    monkeypatch.setattr(naming, '_references', lambda *args: [])
    monkeypatch.setattr(naming, '_bounded_content_hint',
                        lambda path, name: calls.append(('read', path, name)) or '')

    def model(settings, names, references):
        calls.append(('model', names))
        return '{"items":[]}'

    good = 'AGICO_产品宣传册_EN.pdf'
    result = naming._suggest_batch(None, None, 'org', 'author', [good, 'scan001.pdf'],
                                  call_model=model, source_paths=[Path('good'), Path('raw')])
    assert [row['title'] for row in result] == [good, 'scan001.pdf']
    assert result[0]['needs_review'] is False
    assert result[1]['needs_review'] is True
    assert calls == [('model', ['scan001.pdf']), ('read', Path('raw'), 'scan001.pdf')]


def test_shared_contract_and_prompt_come_from_same_document():
    rules = naming.shared_rules()
    assert rules['contract']['separator'] == '_'
    assert '产品宣传册' in rules['contract']['business_types']
    assert 'Skill' in rules['rules']
    assert len(rules['sha256']) == 64
