"""Format adapters preserve source coordinates; unavailable content is disclosed."""

import json
import os
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

PARSER_VERSION = "native-v2"


@dataclass
class Chunk:
    text: str
    locator: dict


@dataclass
class Parsed:
    chunks: list[Chunk] = field(default_factory=list)
    status: str = "ready"
    warnings: list[str] = field(default_factory=list)

    def add(self, text, **locator):
        if text.strip():
            self.chunks.append(Chunk(text.strip(), locator))


def _office_guard(path):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 20000 or sum(i.file_size for i in entries) > 256 * 1024 * 1024:
            raise ValueError("Office 文件展开大小超过解析保护上限；原文件仍保留。")


def _table_text(rows):
    return "\n".join(" | ".join(str(c or "").replace("\n", " / ") for c in row) for row in rows)


def _docx(path, result):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(path)
    section = ""
    paragraph = table = 0
    for block in doc.iter_inner_content():
        if isinstance(block, Paragraph):
            paragraph += 1
            if block.style and block.style.name.startswith("Heading"):
                section = block.text
            result.add(block.text, kind="paragraph", paragraph=paragraph, section=section)
        elif isinstance(block, Table):
            table += 1
            result.add(
                _table_text([[c.text for c in row.cells] for row in block.rows]),
                kind="table",
                table=table,
                section=section,
            )
    if doc.inline_shapes:
        result.warnings.append("Word 内嵌图片／图形未解释，请查看原文件。")
    if doc._element.xpath(".//w:txbxContent"):
        result.warnings.append("Word 文本框未纳入正文，可能包含适用条件，请查看原文件。")
    if doc._element.xpath(".//w:sdt | .//w:ins | .//w:del"):
        result.warnings.append("Word 内容控件或修订未完整解析，请核对原文件。")
    if any(cell.tables for table in doc.tables for row in table.rows for cell in row.cells):
        result.warnings.append("Word 嵌套表格未完整解析，请查看原文件。")
    # Headers, footers, notes and textboxes aren't part of the body traversal.
    if any(area.tables for s in doc.sections for area in (s.header, s.footer)) or any(
        p.text.strip()
        for s in doc.sections
        for area in (s.header, s.footer)
        for p in area.paragraphs
    ):
        result.warnings.append("Word 页眉页脚未纳入正文，需结合原文件。")
    with zipfile.ZipFile(path) as z:
        if any(n in z.namelist() for n in ["word/footnotes.xml", "word/endnotes.xml"]):
            result.warnings.append("Word 脚注／尾注未解析，内容可能缺少适用条件，请查看原文件。")


def _sheet_rows(sheet):
    if (sheet.max_row or 0) * (sheet.max_column or 0) > 1_000_000:
        raise ValueError("工作表范围超过解析保护上限；原文件仍保留。")
    rows = []
    width = 0
    # The XLSX dimension element is optional. Bound actual rows before accumulating,
    # including unsized sheets and sparse coordinates; don't rely on cached dimensions.
    for index, row in enumerate(sheet.iter_rows(values_only=True), 1):
        width = max(width, len(row))
        if index * max(1, width) > 1_000_000:
            raise ValueError("工作表范围超过解析保护上限；原文件仍保留。")
        rows.append(row)
    return rows


def _xlsx(path, result):
    from contextlib import ExitStack

    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    with ExitStack() as resources:
        # Storage uses opaque .blob names; the validated original filename chooses
        # this adapter. File handles avoid openpyxl's path-extension check.
        formulas = load_workbook(
            resources.enter_context(path.open("rb")), read_only=True, data_only=False
        )
        resources.callback(formulas.close)
        cached = load_workbook(
            resources.enter_context(path.open("rb")), read_only=True, data_only=True
        )
        resources.callback(cached.close)
        for sheet in formulas:
            values = _sheet_rows(cached[sheet.title])
            source_rows = _sheet_rows(sheet)
            width = max((len(row) for row in source_rows), default=1)
            rows = []
            has_formula = False
            for r, row in enumerate(source_rows):
                output = []
                for c, value in enumerate(row):
                    if isinstance(value, str) and value.startswith("="):
                        has_formula = True
                        saved = values[r][c] if r < len(values) and c < len(values[r]) else None
                        value = (
                            f"{value} [缓存结果: {saved}]"
                            if saved is not None
                            else f"{value} [缓存结果缺失]"
                        )
                    output.append("" if value is None else str(value))
                rows.append(output)
            # Keep each small sheet whole; large sheets repeat their header across row groups.
            for start in range(0, len(rows), 50):
                group = rows[start : start + 50]
                if start and rows:
                    group = [rows[0], *group]
                result.add(
                    _table_text(group),
                    kind="table",
                    sheet=sheet.title,
                    range=f"A{start + 1}:{get_column_letter(width)}{min(start + 50, len(rows))}",
                    repeated_header="1" if start else None,
                )
            if has_formula:
                result.warnings.append(
                    f"工作表“{sheet.title}”保留公式与已有缓存结果，未重新计算公式。"
                )
        with zipfile.ZipFile(path) as z:
            if any(n.startswith(("xl/charts/", "xl/drawings/", "xl/media/")) for n in z.namelist()):
                result.warnings.append("Excel 图表／图片未解析，请查看原文件。")


def _pptx(path, result):
    from pptx import Presentation

    for index, slide in enumerate(Presentation(path).slides, 1):
        for shape_index, shape in enumerate(slide.shapes, 1):
            if shape.has_table:
                result.add(
                    _table_text([[c.text for c in row.cells] for row in shape.table.rows]),
                    kind="table",
                    slide=index,
                    table=shape_index,
                )
            elif shape.has_text_frame:
                result.add(shape.text, kind="slide_text", slide=index, shape=shape_index)
            else:
                result.warnings.append(f"幻灯片 {index} 含未解释的图形／图片，需查看原文件。")
        if slide.has_notes_slide:
            text = slide.notes_slide.notes_text_frame.text
            result.add(text, kind="slide_notes", slide=index)


@lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr import RapidOCR

    params = {
        "EngineConfig.onnxruntime.intra_op_num_threads": 2,
        "EngineConfig.onnxruntime.inter_op_num_threads": 2,
    }
    if cache := os.environ.get("AGICO_KB_MODEL_CACHE"):
        params["Global.model_root_dir"] = str(Path(cache) / "ocr")
    return RapidOCR(params=params)


def _ocr(image_input, result, **locator):
    output = _ocr_engine()(image_input)
    texts = getattr(output, "txts", None)
    if texts:
        result.add("\n".join(texts), kind="ocr", ocr=True, **locator)
    result.warnings.append(
        "OCR 识别文字未经人工核对；表格结构与图形语义可能不完整，关键数字请核对原文件。"
    )


def _ocr_page(page, result, index):
    """Render a scanned page at higher DPI for better OCR accuracy, then OCR it."""
    import numpy as np
    import pymupdf

    pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csRGB, alpha=False)
    _ocr(
        np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3),
        result,
        page=index,
    )


def _pdf(path, result, first_page=None, last_page=None):
    """Parse a PDF. first_page/last_page (1-based, inclusive) bound the work so the worker can
    split huge scans into segments that each fit the parse timeout."""
    import pymupdf

    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("PDF 已加密，需提供可读取版本。")
        start = max(1, first_page or 1)
        end = min(doc.page_count, last_page or doc.page_count)
        for index in range(start, end + 1):
            page = doc[index - 1]
            text = page.get_text(sort=True).strip()
            if len(text) < 15 and page.get_images():
                _ocr_page(page, result, index)
                continue
            tables = page.find_tables().tables
            for ti, table in enumerate(tables, 1):
                result.add(_table_text(table.extract()), kind="table", page=index, table=ti)
            blocks = []
            for block in page.get_text("blocks", sort=True):
                if block[6] == 0 and not any(
                    pymupdf.Rect(t.bbox).contains(pymupdf.Rect(block[:4])) for t in tables
                ):
                    blocks.append(block[4])
            result.add("\n".join(blocks), kind="page", page=index)
            if page.get_images():
                result.warnings.append(f"PDF 第 {index} 页含图片，原生文字已提取，图片内容未解释。")
        if len(doc) > 1:
            result.warnings.append("PDF 表格按原页保留；跨页表头和脚注需结合相邻页读取。")


def parse_file(path: Path, filename: str, first_page=None, last_page=None) -> Parsed:
    result = Parsed()
    suffix = Path(filename).suffix.lower()
    if suffix in {".docx", ".xlsx", ".pptx"}:
        _office_guard(path)
    adapters = {".docx": _docx, ".xlsx": _xlsx, ".pptx": _pptx}
    if suffix == ".pdf":
        _pdf(path, result, first_page=first_page, last_page=last_page)
    elif suffix in adapters:
        adapters[suffix](path, result)
    elif suffix in {".xls", ".doc", ".ppt"}:
        # Pure-Python recovery path (LibreOffice absence or failure): values over fidelity.
        from .fallback_parse import FALLBACKS

        FALLBACKS[suffix](path, result)
    elif suffix in {".md", ".txt", ".csv", ".json"}:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030")
            result.warnings.append("文件按 GB18030 解码，请核对中文。")
        section = ""
        for index, block in enumerate(text.split("\n\n"), 1):
            if block.startswith("#"):
                section = block.splitlines()[0].lstrip("# ").strip()
            result.add(block, kind="text", paragraph=index, section=section)
    elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        _ocr(str(path), result, image=1)
    else:
        result.status = "stored_only"
        result.warnings.append(
            "此格式仅保存原文件，暂无正文索引；旧 Office 请另提交 .docx/.xlsx/.pptx，CAD／模型包请关联说明或参数文件。"
        )
        return result
    result.warnings = list(dict.fromkeys(result.warnings))
    if not result.chunks:
        result.status = "failed"
        result.warnings.append("未提取到可读取文字；原文件保留。")
    elif any(
        any(marker in w for marker in ["未解析", "未完整解析", "未解释", "OCR", "未纳入"])
        for w in result.warnings
    ):
        result.status = "partial"
    if len(result.warnings) > 20:
        result.warnings = result.warnings[:20] + [
            "还有未展开的解析提示；处理状态已包含这些限制，关键内容请核对原文件。"
        ]
    return result


if __name__ == "__main__":
    # The worker invokes this in a bounded subprocess, with server-generated paths only.
    # Optional argv[4]/argv[5] bound the page range for segmented PDF parsing.
    first = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else None
    last = int(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] else None
    parsed = parse_file(Path(sys.argv[1]), sys.argv[2], first_page=first, last_page=last)
    Path(sys.argv[3]).write_text(json.dumps(asdict(parsed), ensure_ascii=False), encoding="utf-8")
