"""Fresh drawing fixtures prove CAD indexing without editing uploaded originals."""

import hashlib
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import ezdxf
import pytest

from agico_kb.ingestion import parse_file


def drawing(path: Path, *, with_text: bool = True) -> None:
    doc = ezdxf.new("R2018")
    layout = doc.modelspace()
    if with_text:
        layout.add_text("FLOW-SHEET-731", dxfattribs={"layer": "TITLE", "insert": (2, 3)})
        layout.add_mtext("Kiln feed\\P400 TPD", dxfattribs={"layer": "NOTES", "insert": (4, 5)})
        block = doc.blocks.new("SHEET_TITLE")
        block.add_attdef("DRAWING_NO", insert=(0, 0), text="DEFAULT")
        insert = layout.add_blockref("SHEET_TITLE", (10, 20))
        insert.add_attrib("DRAWING_NO", "AG-731", (10, 20))
    else:
        layout.add_line((0, 0), (10, 10))
    doc.saveas(path)


def all_text(parsed):
    return "\n".join(chunk.text for chunk in parsed.chunks)


def test_dxf_extracts_native_text_attributes_and_source_location(tmp_path):
    source = tmp_path / "fresh.dxf"
    drawing(source)
    original = hashlib.sha256(source.read_bytes()).hexdigest()

    parsed = parse_file(source, source.name)

    assert parsed.status == "ready"
    assert "FLOW-SHEET-731" in all_text(parsed)
    assert "Kiln feed" in all_text(parsed)
    assert "400 TPD" in all_text(parsed)
    assert "DRAWING_NO: AG-731" in all_text(parsed)
    assert all(chunk.locator["kind"] == "cad_text" for chunk in parsed.chunks)
    assert any(chunk.locator["layout"] == "Model" for chunk in parsed.chunks)
    assert any(chunk.locator["layer"] == "TITLE" for chunk in parsed.chunks)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


def test_geometry_only_dxf_is_kept_as_file_without_invented_description(tmp_path):
    source = tmp_path / "fresh-lines.dxf"
    drawing(source, with_text=False)

    parsed = parse_file(source, source.name)

    assert parsed.status == "stored_only"
    assert parsed.chunks == []


def test_missing_dwg_converter_does_not_mark_file_as_parsed(tmp_path, monkeypatch):
    source = tmp_path / "fresh.dwg"
    source.write_bytes(b"AC1032" + os.urandom(64))
    monkeypatch.setenv("AGICO_KB_CAD_CONVERTER", str(tmp_path / "missing-converter.exe"))

    parsed = parse_file(source, source.name)

    assert parsed.status == "stored_only"
    assert parsed.chunks == []
    assert any("CAD 转换器" in warning for warning in parsed.warnings)


def test_dwg_conversion_repairs_truncated_dxf_in_audit_mode(tmp_path, monkeypatch):
    source = tmp_path / "fresh.dwg"
    source.write_bytes(b"AC1032" + os.urandom(64))
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    converter = tmp_path / "converter.exe"
    converter.touch()
    monkeypatch.setenv("AGICO_KB_CAD_CONVERTER", str(converter))

    def conversion(command, **_kwargs):
        output = Path(command[2]) / "drawing.dxf"
        if command[-1] == "1":
            drawing(output)
        else:
            output.write_text("0\nSECTION\n2\nHEADER\n", encoding="ascii")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agico_kb.cad.subprocess.run", conversion)
    parsed = parse_file(source, source.name)

    assert parsed.status == "ready", parsed.warnings
    assert "FLOW-SHEET-731" in all_text(parsed)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


def test_real_dwg_conversion_extracts_text_from_fresh_generated_file(tmp_path, monkeypatch):
    converter = Path(".local/cad-tools/portable/ODAFileConverter.exe").resolve()
    if not converter.is_file():
        pytest.skip("ODA converter is not provisioned on this host")
    source_dir, output_dir = tmp_path / "source", tmp_path / "output"
    source_dir.mkdir()
    output_dir.mkdir()
    fresh_dxf = source_dir / "fresh.dxf"
    drawing(fresh_dxf)
    completed = subprocess.run(
        [str(converter), str(source_dir), str(output_dir), "ACAD2018", "DWG", "0", "0"],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0
    fresh_dwg = output_dir / "fresh.dwg"
    assert fresh_dwg.is_file()
    original = hashlib.sha256(fresh_dwg.read_bytes()).hexdigest()
    monkeypatch.setenv("AGICO_KB_CAD_CONVERTER", str(converter))

    parsed = parse_file(fresh_dwg, fresh_dwg.name)

    assert parsed.status == "ready", parsed.warnings
    assert "FLOW-SHEET-731" in all_text(parsed)
    assert "DRAWING_NO: AG-731" in all_text(parsed)
    assert hashlib.sha256(fresh_dwg.read_bytes()).hexdigest() == original
