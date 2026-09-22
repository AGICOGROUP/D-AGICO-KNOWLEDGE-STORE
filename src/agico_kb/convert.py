"""Legacy-format conversion for parsing: .xls/.doc/.ppt -> modern OOXML via LibreOffice.

Conversion happens in the worker before parsing, on a copy in a temp directory; the stored
original is never modified. LibreOffice is discovered once and cached.
"""

import shutil
import subprocess
from pathlib import Path

CONVERTIBLE = {".xls": ".xlsx", ".doc": ".docx", ".ppt": ".pptx"}


def find_soffice():
    """Locate soffice.exe: PATH first, then the default LibreOffice install dirs."""
    if path := shutil.which("soffice"):
        return Path(path)
    candidates = [
        Path("C:/Program Files/LibreOffice/program/soffice.exe"),
        Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def needs_conversion(filename: str) -> bool:
    return Path(filename).suffix.lower() in CONVERTIBLE


def convert_to_modern(blob_path: Path, filename: str, workdir: Path) -> tuple[Path, str]:
    """Convert a legacy Office blob to its OOXML equivalent inside workdir.

    Returns (converted_path, converted_filename). Raises ValueError with a user-safe message on
    failure - the caller keeps the original stored and marks the version failed/partial.
    """
    suffix = Path(filename).suffix.lower()
    target_suffix = CONVERTIBLE.get(suffix)
    if not target_suffix:
        raise ValueError(f"不支持的转换格式：{suffix}")
    soffice = find_soffice()
    if not soffice:
        raise ValueError(
            "本机未安装 LibreOffice，无法解析旧版 Office 格式；"
            f"请另存为 {target_suffix} 后重新提交，或联系管理员安装 LibreOffice。"
        )
    # -env:UserInstallation isolates the profile so concurrent workers do not fight over locks.
    completed = subprocess.run(
        [
            str(soffice),
            "-env:UserInstallation=file:///" + str(workdir / "lo-profile").replace("\\", "/"),
            "--headless",
            "--norestore",
            "--convert-to",
            target_suffix.lstrip("."),
            "--outdir",
            str(workdir),
            str(blob_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=240,
        creationflags=subprocess.CREATE_NO_WINDOW if __import__("sys").platform == "win32" else 0,
        check=False,
    )
    converted = workdir / (blob_path.stem + target_suffix)
    if completed.returncode != 0 or not converted.exists():
        raise ValueError(
            "旧格式转换失败，原文件保留；请尝试另存为 " + target_suffix + " 后重新提交。"
        )
    return converted, blob_path.stem + target_suffix
