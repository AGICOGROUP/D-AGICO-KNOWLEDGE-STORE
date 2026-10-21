"""Fresh-file, reference-based parsing checks. Status and warnings are not accuracy scores."""

import re
import shutil
import tempfile
from pathlib import Path

from .config import Settings
from .worker import Worker


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def score_parsed(parsed, checks: list[dict]) -> dict:
    """Compare independently labeled facts at their source location."""
    results = []
    for check in checks:
        locator = check["locator"]
        if not locator or not isinstance(locator, dict):
            raise ValueError("每个评分点必须指定页、幻灯片或工作表等定位信息")
        matching_chunks = [
            chunk for chunk in parsed.chunks
            if all(chunk.locator.get(key) == value for key, value in locator.items())
        ]
        text = "\n".join(chunk.text for chunk in matching_chunks)
        expected = check.get("contains")
        forbidden = check.get("excludes")
        if bool(expected) == bool(forbidden):
            raise ValueError("评分点必须且只能设置 contains 或 excludes")
        matched = _normalized(expected or forbidden) in _normalized(text)
        passed = bool(matching_chunks) and (matched if expected else not matched)
        result = {"id": check["id"], "passed": passed, "locator": locator}
        result['critical'] = bool(check.get('critical'))
        if not passed:
            result["observed_excerpt"] = text[:1200]
        results.append(result)
    passed_count = sum(item["passed"] for item in results)
    return {
        "passed": passed_count,
        "total": len(results),
        "score_percent": round(100 * passed_count / len(results), 2) if results else None,
        "checks": results,
    }


def process_fresh(path: Path, *, vision_config_file: Path | None = None):
    """Run the worker's parse path on a new source copy, never a published result."""
    with tempfile.TemporaryDirectory(prefix="kb-quality-") as folder:
        storage = Path(folder)
        settings = Settings(
            "quality-only", storage, vision_config_file=vision_config_file,
            vision_max_calls_per_day=0,
        )
        blob = storage / "source.blob"
        shutil.copyfile(path, blob)
        claim = {"blob_key": blob.name, "filename": path.name, "size": blob.stat().st_size}
        worker = Worker(None, settings)
        try:
            parsed = worker._parse_with_conversion(claim)
        except ValueError as error:
            if "解析超时" not in str(error) or path.suffix.lower() != ".pdf":
                raise
            parsed = worker._parse_segmented(claim)
        if path.suffix.lower() == ".pdf":
            from .vision import enrich_pdf

            enrich_pdf(parsed, blob, settings)
        return parsed


def evaluate_manifest(manifest: dict, *, vision_config_file: Path | None = None) -> dict:
    """Full-file 95% approval requires complete independent text and visual labels."""
    files = []
    for case in manifest["files"]:
        source = Path(case["source"])
        if not source.is_file():
            raise FileNotFoundError(source)
        parsed = process_fresh(source, vision_config_file=vision_config_file)
        result = score_parsed(parsed, case["checks"])
        content_type = case.get('content_type', 'document')
        if content_type not in {'document', 'drawing'}:
            raise ValueError('content_type 必须是 document 或 drawing')
        drawing_pages = set(case.get('drawing_pages', []))
        if content_type == 'drawing':
            drawing_checks, document_checks = case['checks'], []
        else:
            drawing_checks = [c for c in case['checks'] if c['locator'].get('page') in drawing_pages]
            document_checks = [c for c in case['checks'] if c not in drawing_checks]
        drawing_score = score_parsed(parsed, drawing_checks)
        document_score = score_parsed(parsed, document_checks)
        # The reference list, not an automatic classifier, sets the audit scope.
        # A mixed PDF cannot declare its body to be a drawing to avoid the 95% gate.
        drawing_scope_valid = content_type != 'drawing' or (
            source.suffix.lower() in {'.dwg', '.dxf'} or bool(parsed.chunks) and all(
                c.locator.get('content_type') == 'drawing' for c in parsed.chunks))
        if drawing_pages:
            drawing_scope_valid = drawing_scope_valid and all(
                not (c.locator.get('page') in drawing_pages)
                or c.locator.get('content_type') == 'drawing' for c in parsed.chunks)
            if any(c.locator.get('page') not in drawing_pages for c in parsed.chunks):
                drawing_scope_valid = drawing_scope_valid and bool(document_checks)
        threshold = None if content_type == 'drawing' else 95
        checks_pass = (not drawing_checks or drawing_score['passed'] == drawing_score['total']) and (
            not document_checks or document_score['score_percent'] > 95)
        critical_failures = [c['id'] for c in result['checks'] if c['critical'] and not c['passed']]
        result.update({
            "source": str(source), "parser_status": parsed.status,
            "parser_complete": parsed.status == "ready",
            "reference_scope": case.get("reference_scope", "sample"),
            "visual_reference_complete": case.get("visual_reference_complete", False),
            'content_type': content_type, 'threshold_percent': threshold,
            'acceptance_basis': 'drawing_lookup' if content_type == 'drawing' else 'mixed' if drawing_pages else 'document_content',
            'drawing_lookup': drawing_score if drawing_checks else None,
            'document_content': document_score if document_checks else None,
            'drawing_scope_valid': drawing_scope_valid,
            'critical_failures': critical_failures,
            'qualified': bool(result['total'] and checks_pass and drawing_scope_valid
                              and not critical_failures and parsed.status == 'ready'
                              and case.get('reference_scope') == 'full'
                              and case.get('visual_reference_complete')),
        })
        files.append(result)
    passed = sum(item["passed"] for item in files)
    total = sum(item["total"] for item in files)
    score = round(100 * passed / total, 2) if total else None
    references_complete = bool(files) and all(
        item["total"] and item["reference_scope"] == "full"
        and item["visual_reference_complete"] for item in files
    )
    parser_complete = all(item["parser_complete"] for item in files)
    return {
        "score_percent": score, "threshold_percent": 95 if all(f['content_type']=='document' for f in files) else None,
        'thresholds_percent': {'document': 95, 'drawing': None},
        'drawing_acceptance': 'correct_file_identity_notes_details; all reference checks must pass',
        "qualified": bool(files and all(item['qualified'] for item in files)),
        "reference_complete": references_complete,
        "parser_complete": parser_complete,
        "passed": passed, "total": total, "files": files,
    }
