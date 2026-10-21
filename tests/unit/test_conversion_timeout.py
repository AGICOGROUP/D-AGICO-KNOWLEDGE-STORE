import subprocess
from pathlib import Path

import pytest

from agico_kb import convert


@pytest.mark.parametrize(('tree_stop_fails', 'final_wait_fails'), [(False, False), (True, False), (False, True)])
def test_hung_legacy_converter_raises_recoverable_error_and_stops_owned_process(tmp_path, monkeypatch, tree_stop_fails, final_wait_fails):
    source = tmp_path / 'fresh-source.blob'
    content = b'fresh synthetic legacy workbook'
    source.write_bytes(content)
    folder = tmp_path / 'converted'
    folder.mkdir()
    calls = []

    class HungProcess:
        pid = 123456
        returncode = None

        def wait(self, timeout):
            calls.append(('wait', timeout))
            if self.returncode is None or final_wait_fails:
                raise subprocess.TimeoutExpired('fresh-converter', timeout)
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            calls.append(('kill', self.pid))
            self.returncode = 1

    process = HungProcess()
    monkeypatch.setattr(convert, 'find_soffice', lambda: Path('fresh-converter.exe'))
    monkeypatch.setattr(subprocess, 'Popen', lambda *args, **kwargs: process)

    def stop_tree(args, **kwargs):
        if 'taskkill' not in str(args[0]).lower():
            raise subprocess.TimeoutExpired(args, kwargs.get('timeout', 2))
        calls.append(('tree', args))
        if tree_stop_fails:
            raise subprocess.TimeoutExpired(args, 10)
        process.returncode = 1
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, 'run', stop_tree)
    with pytest.raises(ValueError, match='转换超时'):
        convert.convert_to_modern(source, '测试数据.xls', folder, timeout=2)
    assert source.read_bytes() == content
    assert any(kind in {'tree', 'kill'} for kind, value in calls)
    assert process.returncode is not None
