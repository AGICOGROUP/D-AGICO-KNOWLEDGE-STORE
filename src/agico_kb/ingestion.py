"""Format adapters preserve source coordinates; unavailable content is disclosed."""

import json
import math
import os
import re
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


WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _xml_paragraph_text(paragraph):
    return "".join(node.text or "" for node in paragraph.iter(f"{WORD_NS}t"))


def _xml_blocks(region):
    """Paragraphs and tables directly inside an XML region (text box, content control)."""
    for child in region.iterchildren():
        if child.tag == f"{WORD_NS}tbl":
            rows = []
            for row in child.findall(f"{WORD_NS}tr"):
                cells = [
                    " ".join(_xml_paragraph_text(p) for p in cell.findall(f"{WORD_NS}p")).strip()
                    for cell in row.findall(f"{WORD_NS}tc")
                ]
                if any(cells):
                    rows.append(cells)
            if rows:
                yield ("table", _table_text(rows))
        elif child.tag == f"{WORD_NS}p":
            text = _xml_paragraph_text(child).strip()
            if text:
                yield ("paragraph", text)


def _docx(path, result):
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(path)
    section = ""
    paragraph = table = 0
    # Stacked and artistic titles reach us as one character per paragraph (the .doc -> .docx
    # conversion splits 方案简介 into 方/案/简/介). Held here and joined, they read as the title
    # they were; left alone they become four one-character chunks that match everything.
    pending = []
    pending_at = 0

    def flush_pending():
        if pending:
            result.add("".join(pending), kind="paragraph", paragraph=pending_at, section=section)
            pending.clear()

    for block in doc.iter_inner_content():
        if isinstance(block, Paragraph):
            paragraph += 1
            if block.style and block.style.name.startswith("Heading"):
                section = block.text
            text = block.text.strip()
            if len(text) <= 2 and not any(character.isdigit() for character in text):
                if not pending:
                    pending_at = paragraph
                pending.append(text)
                continue
            flush_pending()
            result.add(block.text, kind="paragraph", paragraph=paragraph, section=section)
        elif isinstance(block, Table):
            flush_pending()
            table += 1
            result.add(
                _table_text([[c.text for c in row.cells] for row in block.rows]),
                kind="table",
                table=table,
                section=section,
            )
    flush_pending()
    # Text boxes (callouts, boxed tables) and content controls (w:sdt — a generated table of
    # contents, template fields) sit outside the body traversal, so python-docx never reaches them;
    # read both from the XML instead of losing the content to a warning. A field-generated TOC is
    # how a whole table of contents went missing from a .doc while its body parsed fine.
    regions = [
        ("textbox", box, index)
        for index, box in enumerate(doc._element.xpath(".//w:txbxContent"), start=1)
    ]
    regions += [
        ("control", content, index)
        for index, content in enumerate(doc._element.xpath(".//w:sdtContent"), start=1)
    ]
    seen_regions = set()
    counted = {"textbox": 0, "control": 0}
    for kind, region, index in regions:
        blocks = list(_xml_blocks(region))
        if not blocks:
            continue
        # mc:AlternateContent stores the same region twice (Choice and Fallback); index it once.
        signature = "\n".join(text for _, text in blocks)
        if signature in seen_regions:
            continue
        seen_regions.add(signature)
        counted[kind] += 1
        for block_kind, text in blocks:
            result.add(text, kind=block_kind, **{kind: index}, section=section)
    if doc.inline_shapes:
        result.warnings.append("Word 内嵌图片／图形未解释，请查看原文件。")
    if counted["textbox"]:
        result.warnings.append(
            f"Word 文本框内容已收录（{counted['textbox']} 个文本框），版面位置与原文件不同。"
        )
    elif doc._element.xpath(".//w:txbxContent"):
        result.warnings.append("Word 文本框内只有图形，未解释，请查看原文件。")
    if counted["control"]:
        result.warnings.append(
            f"Word 内容控件内容已收录（{counted['control']} 处），版面位置可能与原文件不同。"
        )
    if doc._element.xpath(".//w:ins | .//w:del"):
        result.warnings.append("Word 修订标记未完整解析，请核对原文件。")
    if any(cell.tables for table in doc.tables for row in table.rows for cell in row.cells):
        result.warnings.append("Word 嵌套表格未完整解析，请查看原文件。")
    # Headers and footers stay out of the index on purpose. Measured across the corpus they carry
    # only the company name, contact block, project line and page number — identical across dozens
    # of files — so indexing them adds repeated noise to retrieval while losing nothing. Flagging
    # them as a gap was worse than the gap: it marked 38 of 44 Word versions "partially parsed",
    # which is the signal that is supposed to mean content actually went missing.
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


def _ocr_rect(box):
    xs = [float(point[0]) for point in box]
    ys = [float(point[1]) for point in box]
    return min(xs), min(ys), max(xs), max(ys)


def _ocr_gaps(intervals, minimum):
    """Split points where whitespace wider than `minimum` separates covered spans."""
    ordered = sorted(intervals)
    found = []
    reach = ordered[0][1]
    for start, end in ordered[1:]:
        if start - reach > minimum:
            found.append((reach, start))
        reach = max(reach, end)
    return found


def _ocr_blocks(items, width, height, depth=0):
    """Recursive XY-cut: split a region on whitespace that spans it entirely, so multi-column
    and staggered layouts (timelines, radial charts) do not interleave their text."""
    if len(items) <= 1 or depth > 8:
        return [items]
    for axis in ("v", "h"):
        if axis == "v":
            found = _ocr_gaps([(item[0], item[2]) for item in items], max(14.0, width * 0.02))
        else:
            found = _ocr_gaps([(item[1], item[3]) for item in items], max(10.0, height * 0.015))
        if not found:
            continue
        cut = (found[0][0] + found[0][1]) / 2
        index = 0 if axis == "v" else 1
        before = [i for i in items if (i[index] + i[index + 2]) / 2 < cut]
        after = [i for i in items if (i[index] + i[index + 2]) / 2 >= cut]
        if not before or not after:
            continue
        return _ocr_blocks(before, width, height, depth + 1) + _ocr_blocks(
            after, width, height, depth + 1
        )
    return [items]


def _ocr_block_text(block):
    """Order one block into lines, stitching lines the layout wrapped mid-word."""
    lines = []
    for item in sorted(block, key=lambda i: (i[1], i[0])):
        for line in lines:
            overlap = min(line["y1"], item[3]) - max(line["y0"], item[1])
            if overlap > 0.5 * min(line["y1"] - line["y0"], item[3] - item[1]):
                line["items"].append(item)
                line["y0"] = min(line["y0"], item[1])
                line["y1"] = max(line["y1"], item[3])
                break
        else:
            lines.append({"y0": item[1], "y1": item[3], "items": [item]})
    lines.sort(key=lambda line: line["y0"])
    left = min(item[0] for item in block)
    right = max(item[2] for item in block)
    margin = 0.12 * (right - left)
    text = ""
    for index, line in enumerate(lines):
        line["items"].sort(key=lambda i: i[0])
        joined = "".join(item[4] for item in line["items"])
        if index == 0:
            text = joined
            continue
        previous = lines[index - 1]
        wrapped = (
            max(item[2] for item in previous["items"]) >= right - margin
            and min(item[0] for item in line["items"]) <= left + margin
        )
        text += ("" if wrapped else "\n") + joined
    return text


def _ocr_reading_order(boxes, texts):
    """Rebuild OCR reading order from detection geometry instead of trusting detector order."""
    items = []
    for box, text in zip(boxes, texts):
        x0, y0, x1, y1 = _ocr_rect(box)
        items.append((x0, y0, x1, y1, text))
    if not items:
        return "\n".join(texts)
    width = max(item[2] for item in items)
    height = max(item[3] for item in items)
    blocks = _ocr_blocks(items, width, height)
    return "\n".join(_ocr_block_text(block) for block in blocks if block)


def _squash(text):
    """Whitespace-insensitive key, so OCR and native text of the same line compare equal."""
    return "".join(text.split())


# A folio: "3", "- 3 -", "第 3 页", "3 / 10", "Page 3 of 10". A trailing dot is excluded on
# purpose — "2." at the top of a page is a list number, not a page number.
PAGE_NUMBER = re.compile(
    r"^[\s\-–—_·/]*(?:第\s*)?(?:page\s*)?\d{1,4}(?:\s*(?:/|of|共)\s*\d{1,4})?\s*(?:页)?[\s\-–—_·/]*$",
    re.IGNORECASE,
)


def _page_lines(page):
    """Text lines with their vertical centre as a fraction of page height."""
    height = page.rect.height or 1.0
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                lines.append((text, (line["bbox"][1] + line["bbox"][3]) / 2 / height))
    return lines


def _in_margin(position):
    """True for a line sitting in the top or bottom band of the page — where furniture lives."""
    return position <= 0.12 or position >= 0.88


def _furniture(pages_lines):
    """Running heads and footers: short text repeated in the top or bottom band of most pages.

    They are the largest single source of retrieval noise in a PDF — a footer's phone number gets
    indexed once per page — while carrying almost nothing: page numbers, company name, contact
    block. Dropping them is safe; keeping them is not. Position is part of the test on purpose: a
    table header row repeated in the middle of a page is content and stays, and a dense document
    whose body text runs into the margins keeps that text because it does not repeat.
    """
    pages_lines = list(pages_lines)
    if len(pages_lines) < 3:
        return frozenset()
    threshold = max(3, math.ceil(0.6 * len(pages_lines)))
    seen = {}
    folio_pages = set()
    for index, lines in enumerate(pages_lines):
        for text, position in lines:
            if not _in_margin(position):
                continue
            if PAGE_NUMBER.match(text):
                folio_pages.add(index)
            elif len(text) <= 120:
                seen.setdefault(_squash(text), set()).add(index)
    drop = {key for key, pages in seen.items() if len(pages) >= threshold}
    if len(folio_pages) >= threshold:
        # A folio differs on every page, so repetition can never see it; left alone it becomes a
        # chunk whose entire text is "3". Require the pattern on most pages before dropping, so a
        # lone number at the bottom of one page is treated as content.
        for lines in pages_lines:
            for text, position in lines:
                if _in_margin(position) and PAGE_NUMBER.match(text):
                    drop.add(_squash(text))
    return frozenset(drop)


def _drop_furniture(text, drop):
    """Same text, minus the lines already identified as running heads or footers."""
    if not drop:
        return text
    kept = [line for line in text.splitlines() if _squash(line) not in drop]
    return "\n".join(kept)


def _ocr(image_input, result, drop=frozenset(), **locator):
    output = _ocr_engine()(image_input)
    texts = getattr(output, "txts", None)
    if texts:
        boxes = getattr(output, "boxes", None)
        if boxes is not None and len(boxes) == len(texts):
            keep = [index for index, text in enumerate(texts) if _squash(text) not in drop]
            texts = [texts[index] for index in keep]
            boxes = [boxes[index] for index in keep]
        else:
            texts = [text for text in texts if _squash(text) not in drop]
            boxes = None
        try:
            text = (
                _ocr_reading_order(boxes, texts)
                if boxes is not None and len(boxes)
                else "\n".join(texts)
            )
        except (AttributeError, IndexError, TypeError, ValueError):
            # Geometry from an unexpected detector build must never lose the recognised text.
            text = "\n".join(texts)
        if text.strip():
            result.add(text, kind="ocr", ocr=True, **locator)
    result.warnings.append(
        "OCR 识别文字未经人工核对；表格结构与图形语义可能不完整，关键数字请核对原文件。"
    )


def _ocr_page(page, result, index, drop=frozenset()):
    """Render a scanned page at higher DPI for better OCR accuracy, then OCR it."""
    import numpy as np
    import pymupdf

    pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csRGB, alpha=False)
    _ocr(
        np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3),
        result,
        drop=drop,
        page=index,
    )


def _image_coverage(page):
    """Fraction of the page area covered by placed images (0..1), used to spot picture pages."""
    import pymupdf

    rect = page.rect
    area = abs(rect.width * rect.height) or 1.0
    covered = 0.0
    for item in page.get_image_info():
        box = pymupdf.Rect(item["bbox"]).intersect(rect)
        if not box.is_empty:
            covered += abs(box.width * box.height)
    return min(1.0, covered / area)


def _pdf(path, result, first_page=None, last_page=None):
    """Parse a PDF. first_page/last_page (1-based, inclusive) bound the work so the worker can
    split huge scans into segments that each fit the parse timeout."""
    import pymupdf

    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("PDF 已加密，需提供可读取版本。")
        start = max(1, first_page or 1)
        end = min(doc.page_count, last_page or doc.page_count)
        pages = [doc[index - 1] for index in range(start, end + 1)]
        # Running heads and footers repeat on every page; dropping them is the difference between
        # one phone number in the index and one per page. The page text layer of a picture-dominant
        # document is nothing but this furniture, so the same keys also filter the OCR output.
        drop = _furniture(_page_lines(page) for page in pages)
        for index, page in zip(range(start, end + 1), pages):
            text = _drop_furniture(str(page.get_text(sort=True)), drop).strip()
            # An image-dominant page (slide decks exported as pictures, full-bleed scans) still
            # carries a thin text layer: the header/footer furniture. Judging by "is there any
            # text" would skip the whole document, so judge by how little text there is against
            # how much of the page is picture.
            coverage = _image_coverage(page)
            image_dominant = bool(page.get_images()) and coverage >= 0.2 and len(text) < 400
            if (len(text) < 15 and page.get_images()) or image_dominant:
                _ocr_page(page, result, index, drop=drop)
                if len(text) < 15:
                    result.warnings.append(
                        f"PDF 第 {index} 页为扫描页，已 OCR 识别；关键数字请核对原文件。"
                    )
                else:
                    result.warnings.append(
                        f"PDF 第 {index} 页以图片为主（图片覆盖 {coverage * 100:.0f}%），"
                        f"已对整页做 OCR；图形语义仍需核对原文件。"
                    )
                continue
            tables = page.find_tables().tables
            for ti, table in enumerate(tables, 1):
                result.add(_table_text(table.extract()), kind="table", page=index, table=ti)
            blocks = []
            for block in page.get_text("blocks", sort=True):
                if block[6] == 0 and not any(
                    pymupdf.Rect(t.bbox).contains(pymupdf.Rect(block[:4])) for t in tables
                ):
                    body = _drop_furniture(block[4], drop).strip()
                    if body:
                        blocks.append(body)
            if blocks:
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
