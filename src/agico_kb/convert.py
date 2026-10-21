"""Legacy-format conversion for parsing: .xls/.doc/.ppt -> modern OOXML via LibreOffice.

Conversion happens in the worker before parsing, on a copy in a temp directory; the stored
original is never modified. LibreOffice is discovered once and cached.
"""

import subprocess
import sys
from pathlib import Path

CONVERTIBLE = {".xls": ".xlsx", ".doc": ".docx", ".ppt": ".pptx"}


def find_soffice():
    """Use the same configured converter as browser previews."""
    from .thumbnails import office_converter

    path = office_converter()
    return Path(path) if path else None


def needs_conversion(filename: str) -> bool:
    return Path(filename).suffix.lower() in CONVERTIBLE


def convert_to_modern(blob_path: Path, filename: str, workdir: Path, *, timeout=240) -> tuple[Path, str]:
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
    budget = min(timeout, 60) if blob_path.stat().st_size <= 8 * 1024 * 1024 else timeout
    process = subprocess.Popen(
        [
            str(soffice),
            "-env:UserInstallation=file:///" + str(workdir / "lo-profile").replace("\\", "/"),
            "--headless",
            "--norestore",
            "--nodefault",
            "--nofirststartwizard",
            "--convert-to",
            target_suffix.lstrip("."),
            "--outdir",
            str(workdir),
            str(blob_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    # Small legacy files should not occupy a parsing slot for four minutes when
    # LibreOffice hangs at exit. The caller already has a source-preserving parser
    # fallback for ValueError; a raw TimeoutExpired used to bypass that fallback.
    try:
        returncode = process.wait(timeout=budget)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass  # Still terminate our direct child and activate the parser fallback.
        try:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass  # Cleanup must not bypass the caller's source-preserving fallback.
        raise ValueError("旧格式转换超时，转入备用解析流程；原文件保留。") from None
    converted = workdir / (blob_path.stem + target_suffix)
    if returncode != 0 or not converted.exists():
        raise ValueError(
            "旧格式转换失败，原文件保留；请尝试另存为 " + target_suffix + " 后重新提交。"
        )
    return converted, blob_path.stem + target_suffix
