"""Pure-Python fallback when LibreOffice is unavailable or fails.

Only text/structure recovery is promised: these parsers keep the words and basic row order so
content stays searchable, with an honest warning that fidelity is below the LibreOffice path.
"""

from pathlib import Path

from .ingestion import Parsed


def _xls_fallback(path: Path, result: Parsed) -> None:
    """Recover cell values from legacy .xls with xlrd. No formulas/styles; values only."""
    import xlrd

    book = xlrd.open_workbook(str(path))
    for sheet in book.sheets():
        for row_index in range(sheet.nrows):
            cells = []
            for cell in sheet.row(row_index):
                if cell.ctype == xlrd.XL_CELL_NUMBER:
                    value = str(cell.value).removesuffix(".0")
                    cells.append(value)
                elif cell.ctype == xlrd.XL_CELL_DATE:
                    try:
                        converted = xlrd.xldate_as_datetime(float(cell.value), book.datemode)
                        stamp = converted.strftime("%Y-%m-%d %H:%M:%S")
                        cells.append(stamp.removesuffix(" 00:00:00"))
                    except Exception:  # noqa: BLE001 - a broken date must not kill the row
                        cells.append(str(cell.value))
                elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    cells.append("TRUE" if cell.value else "FALSE")
                elif cell.ctype == xlrd.XL_CELL_EMPTY or cell.value is None:
                    cells.append("")
                else:
                    cells.append(str(cell.value).replace("\n", " / "))
            if any(c.strip() for c in cells):
                result.add(
                    " | ".join(cells).strip(" |"),
                    kind="table",
                    sheet=sheet.name,
                    row=row_index + 1,
                )
    result.warnings.append(
        "旧版 .xls 经纯 Python 兜底提取：仅保留单元格文字，合并单元格/样式/公式未还原；关键表格请核对原文件。"
    )


def _doc_fallback(path: Path, result: Parsed) -> None:
    """Extract text from legacy .doc with antiword (external binary)."""
    import subprocess
    import sys

    antiword = _antiword_binary()
    if not antiword:
        raise ValueError(
            "无法解析 .doc：LibreOffice 与 antiword 均不可用；请另存为 .docx 后重新提交。"
        )
    completed = subprocess.run(
        [str(antiword), "-m", "UTF-8", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        check=False,
    )
    text = completed.stdout.decode("utf-8", errors="replace") if completed.stdout else ""
    if not text.strip():
        raise ValueError("antiword 未能从该 .doc 提取文字。")
    for paragraph, block in enumerate(text.split("\n\n"), 1):
        result.add(block, kind="paragraph", paragraph=paragraph)
    result.warnings.append(
        "旧版 .doc 经 antiword 兜底提取：仅纯文本，表格结构与图片未还原；关键内容请核对原文件。"
    )


def _ppt_fallback(path: Path, result: Parsed) -> None:
    raise ValueError(
        "无法解析 .ppt：LibreOffice 不可用且无纯 Python 兜底；请另存为 .pptx 后重新提交。"
    )


def _antiword_binary():
    import shutil

    if path := shutil.which("antiword"):
        return Path(path)
    for candidate in (
        Path("D:/AGICO-KNOWLEDGE-STORE/.local/antiword/antiword.exe"),
        Path("C:/Program Files/antiword/antiword.exe"),
    ):
        if candidate.exists():
            return candidate
    return None


FALLBACKS = {".xls": _xls_fallback, ".doc": _doc_fallback, ".ppt": _ppt_fallback}
