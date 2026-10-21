"""Small, read-only image previews; original files are never rewritten."""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageOps, UnidentifiedImageError

from .errors import KBError

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
CAD_EXTENSIONS = {".dwg", ".dxf"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".mpeg", ".mpg"}
MODEL_EXTENSIONS = {".blend", ".glb", ".step", ".stp"}
OFFICE_EXTENSIONS = {
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
}
TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".tsv",
    ".json",
    ".log",
    ".xml",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".py",
    ".js",
    ".ts",
    ".sql",
    ".sh",
    ".ps1",
}
PREVIEW_EXTENSIONS = (
    IMAGE_EXTENSIONS
    | CAD_EXTENSIONS
    | VIDEO_EXTENSIONS
    | MODEL_EXTENSIONS
    | OFFICE_EXTENSIONS
    | TEXT_EXTENSIONS
    | {".pdf"}
)


def image_thumbnail(path: Path, filename: str) -> bytes:
    if Path(filename).suffix.lower() not in IMAGE_EXTENSIONS:
        raise KBError("INVALID_ARGUMENT", "此文件不支持图片预览。", 422)
    try:
        with Image.open(path) as source:
            # Decode the first frame only; use JPEG decoder downsampling when available.
            source.draft("RGB", (512, 512))
            preview = ImageOps.exif_transpose(source)
            preview.thumbnail((512, 512), Image.Resampling.LANCZOS)
            preview = preview.convert("RGBA")
            output = BytesIO()
            preview.save(output, format="PNG")
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise KBError("PREVIEW_UNAVAILABLE", "暂时无法预览，可下载原图查看。", 422) from None


def _run(command, timeout=90):
    startupinfo = None
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    subprocess.run(
        command,
        check=True,
        timeout=timeout,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        startupinfo=startupinfo,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def _render_cad(path, filename):
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, pymupdf

    with tempfile.TemporaryDirectory(prefix="cad-preview-") as folder:
        scratch = Path(folder)
        dxf = path
        if Path(filename).suffix.lower() == ".dwg":
            converter = Path(os.environ.get("AGICO_KB_CAD_CONVERTER", ""))
            if not converter.is_file():
                raise ValueError("CAD converter unavailable")
            input_dir, output_dir = scratch / "input", scratch / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            shutil.copyfile(path, input_dir / "drawing.dwg")
            _run([str(converter), str(input_dir), str(output_dir), "ACAD2018", "DXF", "0", "1"])
            dxf = output_dir / "drawing.dxf"
        doc = ezdxf.readfile(dxf)
        # Never load references outside the drawing; missing xrefs remain in the original.
        candidates = [item for item in doc.layouts if item.name != "Model" and len(item)]
        candidates.sort(key=lambda item: len(item), reverse=True)
        candidates.append(doc.modelspace())
        for drawing in candidates:
            if not len(drawing):
                continue
            backend = pymupdf.PyMuPdfBackend()
            options = config.Configuration(
                background_policy=config.BackgroundPolicy.WHITE,
                color_policy=config.ColorPolicy.BLACK,
                image_policy=config.ImagePolicy.IGNORE,
                hatching_timeout=2,
            )
            Frontend(RenderContext(doc), backend, config=options).draw_layout(drawing)
            try:
                content = backend.get_pixmap_bytes(layout.Page(135, 95), dpi=96)
            except ValueError:
                # Default paper layouts can contain only an inactive viewport.
                # Try the next sheet or model rather than caching an empty image.
                continue
            if content:
                return content
        raise ValueError("Empty drawing")


def _render_pdf(path):
    import pymupdf

    with pymupdf.open(path) as document:
        if document.needs_pass or not len(document):
            raise ValueError("PDF preview unavailable")
        page = document[0]
        scale = 512 / max(page.rect.width, page.rect.height)
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")


def office_converter():
    configured = os.environ.get("AGICO_KB_OFFICE_CONVERTER")
    candidates = [configured, shutil.which("soffice")]
    if sys.platform == "win32":
        for root in [
            os.environ.get("ProgramFiles", "C:/Program Files"),
            os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"),
        ]:
            candidates.extend(
                [
                    str(Path(root) / "LibreOffice/program/soffice.com"),
                    str(Path(root) / "LibreOffice/program/soffice.exe"),
                ]
            )
    return next((value for value in candidates if value and Path(value).is_file()), None)


def convert_office_pdf(path, filename, target, *, first_page=False):
    converter = office_converter()
    if not converter:
        raise ValueError("Office preview converter unavailable")
    suffix = Path(filename).suffix.lower()
    family = (
        "calc"
        if suffix in {".xls", ".xlsx", ".ods"}
        else "impress"
        if suffix in {".ppt", ".pptx", ".odp"}
        else "writer"
    )
    with tempfile.TemporaryDirectory(prefix="office-preview-") as folder:
        scratch = Path(folder)
        source = scratch / ("document" + suffix)
        shutil.copyfile(path, source)
        profile = scratch / "profile"
        user = profile / "user"
        user.mkdir(parents=True)
        # Separate profile avoids taking over a user's open Office session.
        # Never execute uploaded macros or update document links.
        (user / "registrymodifications.xcu").write_text(
            '<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting"><prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
            '<item oor:path="/org.openoffice.Office.Calc/Content/Update"><prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item>'
            "</oor:items>",
            encoding="utf-8",
        )
        output = scratch / "output"
        output.mkdir()
        if not first_page and family == 'calc':
            if suffix == '.xls':
                _run([converter, f'-env:UserInstallation={profile.as_uri()}', '--headless',
                      '--norestore', '--nodefault', '--nofirststartwizard', '--convert-to',
                      'xlsx', '--outdir', str(output), str(source)], timeout=90)
                source = output / 'document.xlsx'
            # Print areas are appropriate for printing, but can omit most of a workbook.
            # Clear them only in the temporary preview copy, never in an uploaded original.
            member = 'content.xml' if suffix == '.ods' else 'xl/workbook.xml'
            with zipfile.ZipFile(source) as archive:
                tree = ElementTree.fromstring(archive.read(member))
                for parent in tree.iter():
                    for child in list(parent):
                        if child.get('name') == '_xlnm.Print_Area':
                            parent.remove(child)
                    parent.attrib.pop('{urn:oasis:names:tc:opendocument:xmlns:table:1.0}print-ranges', None)
                adjusted = scratch / ('complete' + source.suffix)
                with zipfile.ZipFile(adjusted, 'w', compression=zipfile.ZIP_DEFLATED) as destination:
                    for entry in archive.infolist():
                        destination.writestr(entry, ElementTree.tostring(tree, encoding='utf-8', xml_declaration=True)
                                             if entry.filename == member else archive.read(entry))
            source = adjusted
        export = f'pdf:{family}_pdf_Export'
        if first_page:
            export += ':{"PageRange":{"type":"string","value":"1"}}'
        _run(
            [
                converter,
                f"-env:UserInstallation={profile.as_uri()}",
                "--headless",
                "--norestore",
                "--nodefault",
                "--nofirststartwizard",
                "--convert-to",
                export,
                "--outdir",
                str(output),
                str(source),
            ],
            timeout=90 if first_page else 180,
        )
        shutil.copyfile(output / (source.stem + '.pdf'), target)


def _render_office(path, filename):
    with tempfile.TemporaryDirectory(prefix="office-thumbnail-") as folder:
        target = Path(folder) / "document.pdf"
        convert_office_pdf(path, filename, target, first_page=True)
        return _render_pdf(target)


def _render_text(path):
    from PIL import ImageDraw, ImageFont

    with path.open("rb") as stream:
        data = stream.read(8192)
    text = None
    for encoding in [
        "utf-8-sig",
        "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8",
        "gb18030",
    ]:
        try:
            text = data.decode(encoding)
            break
        except UnicodeError:
            pass
    if text is None or "\x00" in text:
        raise ValueError("Text preview unavailable")
    font = None
    for candidate in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "DejaVuSans.ttf"]:
        try:
            font = ImageFont.truetype(candidate, 15)
            break
        except OSError:
            pass
    font = font or ImageFont.load_default()
    page = Image.new("RGB", (512, 640), "white")
    draw = ImageDraw.Draw(page)
    draw.text((24, 18), "内容摘览", fill="#526477", font=font)
    y = 55
    for paragraph in text.expandtabs(4).splitlines():
        line = ""
        for character in paragraph:
            if draw.textlength(line + character, font=font) > 464:
                draw.text((24, y), line, fill="#182b3d", font=font)
                y += 23
                line = ""
                if y > 590:
                    break
            line += character
        if y > 590:
            break
        draw.text((24, y), line, fill="#182b3d", font=font)
        y += 23
    output = BytesIO()
    page.save(output, format="PNG")
    return output.getvalue()


def blender_converter():
    configured = os.environ.get("AGICO_KB_BLENDER")
    candidates = [configured, shutil.which("blender")]
    if sys.platform == "win32":
        root = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Blender Foundation"
        candidates.extend(str(path) for path in sorted(root.glob("Blender */blender.exe"), reverse=True))
    return next((value for value in candidates if value and Path(value).is_file()), None)


def _step_to_stl(source: Path, target: Path):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.StlAPI import StlAPI_Writer

    reader = STEPControl_Reader()
    if reader.ReadFile(str(source)) != IFSelect_RetDone or not reader.TransferRoots():
        raise ValueError("STEP model unreadable")
    shape = reader.OneShape()
    if shape.IsNull():
        raise ValueError("STEP model empty")
    bounds = Bnd_Box()
    BRepBndLib.Add_s(shape, bounds)
    x1, y1, z1, x2, y2, z2 = bounds.Get()
    diagonal = ((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2) ** 0.5
    if not 0 < diagonal < 1e12:
        raise ValueError("STEP model bounds invalid")
    BRepMesh_IncrementalMesh(shape, max(diagonal * 0.002, 0.001), False, 0.5, True)
    writer = StlAPI_Writer()
    writer.ASCIIMode = False
    writer.Write(shape, str(target))
    if not target.is_file() or target.stat().st_size <= 84:
        raise ValueError("STEP model produced no triangles")


def _render_model(path: Path, suffix: str):
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Model exceeds static preview size limit")
    blender = blender_converter()
    if not blender:
        raise ValueError("Blender unavailable")
    with tempfile.TemporaryDirectory(prefix="model-preview-") as folder:
        scratch = Path(folder)
        if suffix in {".step", ".stp"}:
            source = scratch / "model.stl"
            _step_to_stl(path, source)
            render_suffix = ".stl"
        else:
            source = scratch / ("model" + suffix)
            shutil.copyfile(path, source)
            render_suffix = suffix
        output = scratch / "preview.png"
        script = Path(__file__).with_name("model_preview_blender.py")
        _run([blender, "--background", "--factory-startup", "--disable-autoexec",
              "--python-exit-code", "1", "--python", str(script), "--",
              str(source), str(output), render_suffix], timeout=90)
        return output.read_bytes()


def generate_thumbnail(path: Path, filename: str, target: Path):
    """Run only in an isolated process; bound slow decoders and CAD conversions."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        content = _render_pdf(path)
    elif suffix in OFFICE_EXTENSIONS:
        content = _render_office(path, filename)
    elif suffix in TEXT_EXTENSIONS:
        content = _render_text(path)
    elif suffix in CAD_EXTENSIONS:
        content = _render_cad(path, filename)
    elif suffix in MODEL_EXTENSIONS:
        content = _render_model(path, suffix)
    elif suffix in VIDEO_EXTENSIONS:
        ffmpeg = os.environ.get("AGICO_KB_FFMPEG") or shutil.which("ffmpeg")
        if not ffmpeg:
            raise ValueError("FFmpeg unavailable")
        with tempfile.TemporaryDirectory(prefix="video-preview-") as folder:
            frame = Path(folder) / "frame.png"
            _run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-format_whitelist",
                    "mov,matroska,avi,asf,mpeg,mpegts",
                    "-threads",
                    "1",
                    "-i",
                    str(path),
                    "-an",
                    "-sn",
                    "-vf",
                    "scale=512:512:force_original_aspect_ratio=decrease,thumbnail=30",
                    "-frames:v",
                    "1",
                    "-threads",
                    "1",
                    "-y",
                    str(frame),
                ],
                timeout=45,
            )
            content = frame.read_bytes()
    else:
        content = image_thumbnail(path, filename)
    # Normalize dimensions, and publish only a complete valid preview.
    with Image.open(BytesIO(content)) as image:
        image.thumbnail((512, 512), Image.Resampling.LANCZOS)
        temporary = target.with_suffix(".part")
        image.save(temporary, format="PNG")
        temporary.replace(target)


class ThumbnailService:
    def __init__(self, storage_root):
        self.root = Path(storage_root) / "previews" / "v1"
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="preview-fast")
        self.slow_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="preview-convert")
        self.jobs = {}
        self.lock = threading.Lock()

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.slow_executor.shutdown(wait=False, cancel_futures=True)

    def _generate(self, path, filename, target):
        try:
            _run(
                [sys.executable, "-m", "agico_kb.thumbnails", str(path), filename, str(target)],
                timeout=150,
            )
            return target.is_file()
        except (OSError, subprocess.SubprocessError):
            return False
        finally:
            target.with_suffix(".part").unlink(missing_ok=True)

    def get(self, path: Path, filename: str):
        suffix = Path(filename).suffix.lower()
        if suffix not in PREVIEW_EXTENSIONS:
            raise KBError("INVALID_ARGUMENT", "此文件不支持缩略图。", 422)
        stat = path.stat()
        key = hashlib.sha256(
            f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{suffix}".encode()
        ).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{key}.png"
        # Caller must authorize the original BEFORE every call, even for a cache hit.
        with self.lock:
            job = self.jobs.get(key)
            if target.is_file():
                self.jobs.pop(key, None)
                return target
            if job is not None and job.done():
                self.jobs.pop(key, None)
                raise KBError("PREVIEW_UNAVAILABLE", "暂时无法预览，可下载原文件查看。", 422)
            if job is None:
                # Completed entries need not occupy queue capacity indefinitely.
                self.jobs = {k: future for k, future in self.jobs.items() if not future.done()}
                if len(self.jobs) >= 100:
                    raise KBError("PREVIEW_BUSY", "预览正在排队，请稍后重试。", 503)
                executor = (
                    self.slow_executor
                    if suffix in CAD_EXTENSIONS | OFFICE_EXTENSIONS | VIDEO_EXTENSIONS | MODEL_EXTENSIONS
                    else self.executor
                )
                self.jobs[key] = executor.submit(self._generate, path, filename, target)
        return None


if __name__ == "__main__":
    try:
        generate_thumbnail(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
    except Exception:  # noqa: BLE001 - isolated renderer must not expose source diagnostics
        # No source paths or converter diagnostics in logs.
        sys.exit(1)
