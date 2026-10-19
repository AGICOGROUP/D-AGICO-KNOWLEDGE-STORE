from agico_kb.ingestion import Parsed, parse_file


def test_nul_separators_preserve_units_and_source_locator():
    parsed = Parsed()
    parsed.add("电流 A\x00kA\x00MA\n电导率 μS/cm\t12", page=8, kind="table")
    parsed.add("\x00\x00", page=9)
    assert len(parsed.chunks) == 1
    assert parsed.chunks[0].text == "电流 A kA MA\n电导率 μS/cm\t12"
    assert parsed.chunks[0].locator == {"page": 8, "kind": "table"}
    assert len(parsed.warnings) == 1


def test_text_file_nul_is_sanitized_by_project_entry(tmp_path):
    path = tmp_path / "fresh.txt"
    path.write_bytes(b"voltage 220\x00V\ncurrent 5\x00A")
    result = parse_file(path, path.name)
    assert result.status == "ready"
    assert all("\x00" not in c.text for c in result.chunks)
    assert "220 V" in result.chunks[0].text
    assert path.read_bytes() == b"voltage 220\x00V\ncurrent 5\x00A"
