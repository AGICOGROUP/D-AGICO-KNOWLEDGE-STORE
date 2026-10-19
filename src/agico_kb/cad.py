"""Read-only CAD text indexing; geometry remains in the original drawing."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import ezdxf

from .drawing_index import DETAIL, IDENTITY, NOTICE, meaningful
from .ingestion import Parsed


def _position(entity):
    point = entity.dxf.get("insert")
    if point is None:
        return None
    return [round(float(point.x), 3), round(float(point.y), 3)]


def _read_entities(doc):
    for layout in doc.layouts:
        def walk(entity, parent=None, depth=0, layout_name=layout.name):
            kind = entity.dxftype()
            handle = entity.dxf.get("handle") or parent
            layer = entity.dxf.get("layer", "0")
            if kind == "INSERT" and depth < 8:
                for attribute in entity.attribs:
                    yield from walk(attribute, handle, depth + 1)
                try:
                    for child in entity.virtual_entities():
                        yield from walk(child, handle, depth + 1)
                except (ezdxf.DXFError, ValueError):
                    pass
                return
            if kind == "TEXT":
                text = entity.dxf.get("text", "")
            elif kind == "MTEXT":
                text = entity.plain_text()
            elif kind == "ATTRIB":
                value = entity.dxf.get("text", "")
                tag = entity.dxf.get("tag", "")
                text = f"{tag}: {value}" if tag else value
            elif kind == "DIMENSION":
                # Dimension entities belong to the original; never index detached
                # numbers or derive their geometric relationships.
                return
            else:
                return
            text = text.replace("\x00", " ").strip()
            if text:
                yield layout_name, layer, text, {
                    "handle": handle,
                    "entity_type": kind,
                    "position": _position(entity),
                    "parent_insert": parent,
                }

        for entity in layout:
            yield from walk(entity)


def _extract_dxf(path: Path, parsed: Parsed) -> Parsed:
    try:
        doc = ezdxf.readfile(path)
    except (OSError, ezdxf.DXFError, UnicodeError):
        parsed.status = "stored_only"
        parsed.warnings.append("CAD 文字读取失败，原文件仍可下载。")
        return parsed

    groups = defaultdict(list)
    for layout, layer, text, origin in _read_entities(doc):
        groups[(layout, layer)].append((text, origin))
    for (layout, layer), records in groups.items():
        contextual_layer = bool(re.search(r'TITLE|\bBOM\b|PARTS|明细|标题|图框|材料表', layer, re.IGNORECASE))
        context_parents = {origin['parent_insert'] for text, origin in records
                           if origin['parent_insert'] and (IDENTITY.search(text) or DETAIL.search(text))}
        # Bare numbers may be a drawing number or a table cell. Keep those in
        # explicit title/BOM layers or named blocks, but not loose dimension text.
        records = [(text, origin) for text, origin in records
                   if origin['entity_type'] == 'ATTRIB' or meaningful(text)
                   or ((contextual_layer or origin['parent_insert'] in context_parents)
                       and re.fullmatch(r'[-+]?\d+(?:[.,]\d+)?', text))]
        batch = []
        length = 0
        for text, origin in records:
            if batch and (len(batch) >= 12 or length + len(text) > 900):
                parsed.add(
                    "\n".join(item[0] for item in batch),
                    kind="cad_text",
                    layout=layout,
                    layer=layer,
                    entities=[item[1] for item in batch],
                    content_type='drawing', requires_original=True, index_scope='lookup',
                )
                batch, length = [], 0
            batch.append((text, origin))
            length += len(text)
        if batch:
            parsed.add(
                "\n".join(item[0] for item in batch),
                kind="cad_text",
                layout=layout,
                layer=layer,
                entities=[item[1] for item in batch],
                content_type='drawing', requires_original=True, index_scope='lookup',
            )

    if not parsed.chunks:
        parsed.status = "stored_only"
        parsed.warnings.append("图纸没有可提取的原生文字，原件保留。")
    else:
        parsed.warnings.append(NOTICE)
    return parsed


def parse_cad(path: Path, suffix: str, parsed: Parsed) -> Parsed:
    if suffix == ".dxf":
        return _extract_dxf(path, parsed)

    converter = Path(os.environ.get("AGICO_KB_CAD_CONVERTER", ""))
    if not converter.is_file():
        parsed.status = "stored_only"
        parsed.warnings.append("CAD 转换器未配置，原文件仍可下载；请配置后重新解析。")
        return parsed

    startupinfo = None
    if sys.platform == "win32":
        # ODA is a GUI application: CREATE_NO_WINDOW hides only console windows.
        # The Windows startup show flag keeps its conversion window invisible.
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

    with tempfile.TemporaryDirectory(prefix="cad-index-") as folder:
        scratch = Path(folder)
        input_dir, output_dir = scratch / "input", scratch / "output"
        input_dir.mkdir()
        output_dir.mkdir()
        shutil.copyfile(path, input_dir / "drawing.dwg")
        try:
            completed = subprocess.run(
                # ODA can return 0 while writing a truncated DXF when Audit is off.
                # Audit repairs the drawing during conversion without changing the source.
                [str(converter), str(input_dir), str(output_dir), "ACAD2018", "DXF", "0", "1"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=90,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                startupinfo=startupinfo,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        converted = output_dir / "drawing.dxf"
        if completed is None or completed.returncode != 0 or not converted.is_file():
            parsed.status = "stored_only"
            parsed.warnings.append("CAD 转换失败或超时，原文件仍可下载；请核对图纸格式。")
            return parsed
        return _extract_dxf(converted, parsed)
