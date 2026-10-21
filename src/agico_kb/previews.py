"""Authenticated callers request read-only full-document derivatives, never rewritten originals."""

import hashlib
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from .errors import KBError
from .thumbnails import IMAGE_EXTENSIONS, OFFICE_EXTENSIONS, _run, convert_office_pdf

DOCUMENT_EXTENSIONS = IMAGE_EXTENSIONS | OFFICE_EXTENSIONS | {".pdf"}


def prepare_document(source, filename, folder):
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        with Image.open(source) as image:
            # TIFF contains document pages; animated images have a single static preview.
            pages = image.n_frames if suffix in {".tif", ".tiff"} else 1
        manifest = {"kind": "image", "pages": pages}
    else:
        pdf = source
        if suffix in OFFICE_EXTENSIONS:
            pdf = folder / "document.pdf"
            temporary = folder / "document.part"
            convert_office_pdf(source, filename, temporary)
            temporary.replace(pdf)
        elif suffix != '.pdf':
            raise ValueError('Unsupported format')
        with pymupdf.open(pdf) as document:
            if document.needs_pass or not len(document):
                raise ValueError("PDF unavailable")
            manifest = {"kind": "document", "pages": len(document)}
    temporary = folder / "manifest.part"
    temporary.write_text(json.dumps(manifest), encoding="utf-8")
    temporary.replace(folder / "manifest.json")
    return manifest


def render_page(source, filename, folder, page, width, target):
    suffix = Path(filename).suffix.lower()
    temporary = target.with_suffix('.part')
    if suffix in IMAGE_EXTENSIONS:
        with Image.open(source) as image:
            pages = image.n_frames if suffix in {'.tif', '.tiff'} else 1
            if not 1 <= page <= pages:
                raise ValueError('Page outside document')
            image.seek(page - 1)
            frame = ImageOps.exif_transpose(image).convert('RGBA')
            frame.thumbnail((width, width), Image.Resampling.LANCZOS)
            frame.save(temporary, format='PNG')
    else:
        pdf = folder / 'document.pdf' if suffix in OFFICE_EXTENSIONS else source
        with pymupdf.open(pdf) as document:
            if not 1 <= page <= len(document):
                raise ValueError('Page outside document')
            sheet = document[page - 1]
            scale = width / max(sheet.rect.width, sheet.rect.height)
            # Only render one page on demand; huge documents never allocate all page pixels.
            pixels = sheet.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            temporary.write_bytes(pixels.tobytes('png'))
    temporary.replace(target)


class DocumentPreviewService:
    def __init__(self, storage_root):
        self.root = Path(storage_root) / 'previews' / 'documents-v1'
        self.converter = ThreadPoolExecutor(max_workers=2, thread_name_prefix='document-convert')
        self.renderer = ThreadPoolExecutor(max_workers=3, thread_name_prefix='document-page')
        self.lock = threading.Lock()
        self.jobs = {}
        self.failed = {}

    def close(self):
        self.converter.shutdown(wait=False, cancel_futures=True)
        self.renderer.shutdown(wait=False, cancel_futures=True)

    def _folder(self, source, filename):
        suffix = Path(filename).suffix.lower()
        if suffix not in DOCUMENT_EXTENSIONS:
            raise KBError('PREVIEW_UNSUPPORTED', '此格式暂不支持全文预览，可下载原文件查看。', 422)
        stat = source.stat()
        key = hashlib.sha256(
            f'{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{suffix}'.encode()
        ).hexdigest()
        folder = self.root / key
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    @staticmethod
    def _generate(command, target, timeout):
        try:
            _run([sys.executable, '-m', 'agico_kb.previews', *command], timeout=timeout)
            return target.is_file()
        except (OSError, subprocess.SubprocessError):
            return False
        finally:
            target.with_suffix('.part').unlink(missing_ok=True)

    def _request(self, target, command, executor, timeout):
        with self.lock:
            if target.is_file():
                self.jobs.pop(target, None)
                self.failed.pop(target, None)
                return target
            job = self.jobs.get(target)
            if job is not None and job.done():
                self.jobs.pop(target, None)
                self.failed[target] = time.monotonic()
            self.failed = {key: stamp for key, stamp in self.failed.items()
                           if time.monotonic() - stamp < 60}
            if target in self.failed:
                raise KBError('PREVIEW_UNAVAILABLE', '暂时无法生成预览，可稍后重试或下载原文件查看。', 422)
            if job is None:
                # Bound queued jobs independently of document size; no page-count truncation.
                for key, future in list(self.jobs.items()):
                    if future.done():
                        self.jobs.pop(key)
                        if not key.is_file():
                            self.failed[key] = time.monotonic()
                if len(self.jobs) >= 100:
                    raise KBError('PREVIEW_BUSY', '预览正在排队，请稍后重试。', 503)
                self.jobs[target] = executor.submit(self._generate, command, target, timeout)
        return None

    def manifest(self, source, filename):
        folder = self._folder(source, filename)
        target = folder / 'manifest.json'
        result = self._request(target, ['prepare', str(source), filename, str(folder)],
                               self.converter, 210)
        return json.loads(result.read_text(encoding='utf-8')) if result else None

    def page(self, source, filename, page, width):
        manifest = self.manifest(source, filename)
        if manifest is None:
            return None
        if not 1 <= page <= manifest['pages']:
            raise KBError('NOT_FOUND', '页码超出文件范围。', 404)
        folder = self._folder(source, filename)
        target = folder / f'page-{page}-{width}.png'
        return self._request(target, ['page', str(source), filename, str(folder), str(page),
                                     str(width), str(target)], self.renderer, 60)


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'prepare':
            prepare_document(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]))
        else:
            render_page(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]), int(sys.argv[5]),
                        int(sys.argv[6]), Path(sys.argv[7]))
    except Exception:  # noqa: BLE001 - no document data or converter diagnostics in logs
        sys.exit(1)
