"""Conservative page-level drawing preflight and original-bound lookup text.

Scans use the parser's local OCR evidence once, before semantic/API parsing.
No filename or isolated tolerance is enough to lower a document's parsing scope.
"""

import re

VERSION = 'drawing-lookup-v4-multilingual-fields'
NOTICE = '图纸检索线索，须结合原图读取；不用于确定尺寸、几何关系或工程结论。'
IDENTITY = re.compile(
    r'图名|图号|图纸编号|DRAWING\s*(?:NO|NUMBER|TITLE)|DWG\s*NO|'
    r'НАИМЕНОВАНИЕ|ОБОЗНАЧЕНИЕ|ЧЕРТ[ЕЁ]Ж|N[ÚU]MERO\s*(?:DE\s*)?PLANO|'
    r'NOMBRE\s*(?:DEL?\s*)?PLANO|T[ÍI]TUL[O0]|(?:DOCUMENT\s+)?TITLE\s*:?|'
    r'BR\.?\s*NACRTA|BROJ\s*(?:NACRTA|CRTE[ŽZ]A)|NAZIV\s*(?:CRTE[ŽZ]A|DIJELA)|'
    r'(?:ANEXO|FIGURA)\s*NO\b|NO\.?\s*DE\s*ESPECIFICACI[ÓO]N', re.IGNORECASE)
NOTE = re.compile(r'技术要求|技术说明|说明|NOTES?\b|ТЕХНИЧЕСК|ПРИМЕЧАН|NOTAS?\b', re.IGNORECASE)
DETAIL = re.compile(
    r'明细|材料表|零件表|PARTS?\s*LIST|BILL\s*OF\s*MATERIAL|\bBOM\b|'
    r'СПЕЦИФИКАЦ|МАТЕРИАЛ|LISTA\s*DE|CANTIDAD|QUANTITY|MATERIAL|MATERIJAL|KOMADA', re.IGNORECASE)
DIMENSION = re.compile(r'(?:[φΦØø⌀±]|\bR\s*)\s*\d+(?:[.,]\d+)?', re.IGNORECASE)
DIMENSION_ONLY = re.compile(
    r'^[\s\d.,+−–—\-±°º×xX*/=()\[\]<>φΦØø⌀RrHhMmKkFfGgJjSsPp]+$')
CODE = re.compile(r'^(?:\d+[._/-]){2,}\d+(?:[._/-]\w+)?$')
DIAGRAM_TITLE = re.compile(
    r'工艺(?:总)?流程图|(?:工艺)?仪表流程图|总平面布置图|PROCESS\s+FLOW\s+DIAGRAM|'
    r'PIPING\s+(?:AND\s+)?INSTRUMENTATION\s+DIAGRAM', re.IGNORECASE)
INDEX_TITLE = re.compile(r'日产.{0,30}生产线|\bPROCESS\s+FLOW\b', re.IGNORECASE)


def meaningful(text):
    """Keep descriptions and identifiers; detached dimensions have no lookup meaning."""
    text = text.strip()
    if not text:
        return False
    if CODE.fullmatch(text):
        return True
    if DIMENSION_ONLY.fullmatch(text):
        return False
    return sum(c.isalpha() for c in text) >= 2


def broken_mapping(text):
    """Detect corrupted glyph mappings, not legitimate Cyrillic or accented prose.

    A single NUL separator is recoverable. Dense replacement glyphs, or control
    bytes mixed with anomalous Latin Extended-B glyphs, require pixel recovery.
    """
    visible = [c for c in text if not c.isspace()]
    if not visible:
        return False
    controls = sum(ord(c) < 32 and c not in '\t\r\n' for c in text)
    anomalous = sum('\u0180' <= c <= '\u024f' for c in visible)
    replacements = visible.count('\ufffd')
    return (replacements >= 3 and replacements / len(visible) >= .05
            or anomalous >= 5 and anomalous / len(visible) >= .5
            or controls >= 2 and anomalous >= 5 and anomalous / len(visible) >= .15)


def native_records(page):
    import pymupdf

    records = []
    for bi, block in enumerate(page.get_text('dict', flags=pymupdf.TEXTFLAGS_DICT
                                            & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks']):
        if block.get('type') != 0:
            continue
        for line in block['lines']:
            text = ''.join(s['text'] for s in line['spans']).strip()
            if text:
                records.append({'text': text, 'region': list(line['bbox']),
                                'group': bi, 'source': 'native', 'confidence': 1.0})
    return records


def assess(records, width, height, *, vector=False, scan=False, vector_count=0):
    """Require independent layout and identity/dimension evidence; uncertain stays full."""
    lines = [r['text'] for r in records]
    total = sum(len(t) for t in lines) or 1
    prose_chars = sum(len(t) for t in lines if len(t) >= 65)
    long_share = prose_chars / total
    identity = sum(bool(IDENTITY.search(t)) for t in lines)
    dimensions = sum(len(DIMENSION.findall(t)) for t in lines)
    short_share = sum(len(t) <= 45 for t in lines) / max(1, len(lines))
    # A report can discuss drawings, and its footer can contain a drawing reference.
    if prose_chars >= 450 and long_share >= .5:
        kind, reason = 'document', 'continuous_prose'
    else:
        boxes = [r['region'] for r in records]
        span_x = (max(b[2] for b in boxes) - min(b[0] for b in boxes)) / max(width, 1) if boxes else 0
        span_y = (max(b[3] for b in boxes) - min(b[1] for b in boxes)) / max(height, 1) if boxes else 0
        dispersed = span_x >= .5 and span_y >= .35 and short_share >= .55
        coordinates = len(re.findall(r'\b[XY]\s*=\s*[-+]?\d', '\n'.join(lines), re.IGNORECASE))
        site_plan = vector_count >= 300 and coordinates >= 2 and bool(
            re.search(r'\bAREA\s*=|面积|高程|坡度|储存区', '\n'.join(lines), re.IGNORECASE))
        views = len(re.findall(r'\b(?:SECTION|VIEW|DETALJ|SECCI[OÓ0]N|ВИД|РАЗРЕЗ)\s+[A-ZА-Я]|\bCORTE\s*[A-Z]|\b[A-ZА-Я]\s*[-—]\s*[A-ZА-Я]\b|VISTA\s+(?:LATERAL|FRONTAL|SUPERIOR)|剖面|视图',
                               '\n'.join(lines), re.IGNORECASE))
        diagram = bool(DIAGRAM_TITLE.search('\n'.join(lines))) and (vector_count >= 300 or scan)
        # Native vectors or a scan must accompany clear drawing conventions.
        spatial_layout = dispersed or (site_plan and span_x >= .5 and span_y >= .25)
        titled_sheet = vector_count >= 300 and identity >= 2 and views >= 2 and any(
            DETAIL.search(t) or NOTE.search(t) for t in lines)
        # Outlined-font drawings may expose only their bilingual title block.
        # Require a large, very dense vector sheet and several independent
        # engineering metadata fields, rather than its filename or a tolerance.
        fields = re.findall(r'\b(?:PROJECT PHASE|EQUIPMENT NO|DOCUMENT TITLE|PLANT CODE|'
                            r'PLANT UNIT CODE|DOCUMENT TYPE|SHEET OF SHEETS|CUSTOMER DOC)\b',
                            '\n'.join(lines), re.IGNORECASE)
        outlined_sheet = (vector_count >= 1000 and max(width, height) >= 1200
                          and identity >= 2 and len({f.upper() for f in fields}) >= 5)
        scan_fields = re.findall(r'\b(?:MJERILO|KOMADA|MATERIJAL|POZICIJA|BR\.?\s*NACRTA)\b',
                                 '\n'.join(lines), re.IGNORECASE)
        scanned_sheet = (scan and identity >= 1 and dimensions >= 4 and views >= 1
                         and len({f.upper() for f in scan_fields}) >= 3)
        dimensioned_views = (scan and identity >= 1 and dimensions >= 2 and views >= 2
                             and bool(re.search(r'ACOTACI[ÓO0]N\s*[:：]\s*(?:mm|cm)\b',
                                                '\n'.join(lines), re.IGNORECASE)))
        # Test diagrams can use plain dimensions without phi/R glyphs. Require
        # both annex/figure title-block fields, multiple views and explicit
        # drawing units, rather than treating every illustrated report as CAD.
        appendix_fields = set(re.findall(r'\b(ANEXO|FIGURA)\s*NO\b',
                                        '\n'.join(lines), re.IGNORECASE))
        test_appendix = (scan and len({f.upper() for f in appendix_fields}) == 2
                         and views >= 2 and bool(re.search(
                             r'\bACOT\.?\s*[:：]?\s*EN\s*(?:mm|cm)\b',
                             '\n'.join(lines), re.IGNORECASE)))
        drawing = (vector or scan) and (spatial_layout or outlined_sheet) and long_share < .5 and (
            (identity >= 2 and dimensions >= 2)
            or (dimensions >= 8 and (vector_count >= 300 or identity >= 1 or views >= 2))
            or site_plan or diagram or titled_sheet or outlined_sheet or scanned_sheet
            or dimensioned_views or test_appendix)
        kind, reason = ('drawing', 'identity_dimensions_layout') if drawing else ('unknown', 'insufficient_evidence')
    return {'content_type': kind, 'reason': reason, 'classifier_version': VERSION,
            'identity_labels': identity, 'dimension_labels': dimensions,
            'long_text_share': round(long_share, 3)}


def preflight(page, records=None):
    from .ingestion import _image_coverage, _vector_content

    evidence = native_records(page) if records is None else records
    return assess(evidence, page.rect.width, page.rect.height,
                  vector=_vector_content(page), scan=_image_coverage(page) >= .2,
                  vector_count=len(page.get_cdrawings()))


def add_index(parsed, records, page_index, assessment):
    """Preserve fields and nearby cell values with source regions, never infer geometry."""
    groups = {}
    for record in records:
        groups.setdefault(record.get('group', len(groups)), []).append(record)
    anchors = [r for r in records if IDENTITY.search(r['text']) or DETAIL.search(r['text'])]
    columns = [r for r in records if DETAIL.search(r['text']) and len(r['text']) < 50]

    def detail_cell(record):
        if not re.fullmatch(r'[-+]?\d+(?:[.,]\d+)?', record['text']):
            return False
        box = record['region']
        in_column = any(box[1] > c['region'][3] and abs(box[0]-c['region'][0]) <= 40
                        for c in columns)
        # Numeric table cells must share a row with a readable item name. Do not
        # mistake every number below a parts-list heading for part of its table.
        named_row = any(meaningful(r['text']) and not IDENTITY.search(r['text'])
                        and not NOTE.search(r['text']) and r['region'][0] < box[0]
                        and abs(r['region'][1]-box[1]) <= max(8, (box[3]-box[1])*.5)
                        for r in records)
        return in_column and named_row

    for group in groups.values():
        has_context = any(IDENTITY.search(r['text']) or NOTE.search(r['text'])
                          or DETAIL.search(r['text']) for r in group)
        kept = []
        for record in group:
            box = record['region']
            # Title/parts-list cells often use separate native text objects. Retain
            # their neighbouring values without attaching them to a dimension.
            near_field = any(abs(box[1] - a['region'][1]) <= 2 * max(8, box[3]-box[1])
                             and abs(box[0] - a['region'][2]) <= 160 for a in anchors)
            if meaningful(record['text']) or has_context or near_field or detail_cell(record):
                kept.append(record)
        if not kept:
            continue
        region = [min(r['region'][0] for r in kept), min(r['region'][1] for r in kept),
                  max(r['region'][2] for r in kept), max(r['region'][3] for r in kept)]
        body = '\n'.join(r['text'] for r in kept)
        role = 'identity' if IDENTITY.search(body) or DIAGRAM_TITLE.search(body) or INDEX_TITLE.search(body) else 'details' if DETAIL.search(body) else 'notes' if NOTE.search(body) else 'label'
        if role == 'label':
            for anchor in anchors:
                box = anchor['region']
                if any(abs(r['region'][1]-box[1]) <= 2*max(8, box[3]-box[1])
                       and abs(r['region'][0]-box[2]) <= 160 for r in kept):
                    role = 'identity' if IDENTITY.search(anchor['text']) else 'details'
                    break
        confidence = min(r.get('confidence', 1.0) for r in kept)
        parsed.add(f'【{NOTICE}】\n{body}', kind='drawing_index', page=page_index,
                   region=[round(v, 2) for v in region], content_type='drawing',
                   index_role=role, requires_original=True, classification=assessment,
                   extraction='ocr' if any(r.get('source') == 'ocr' for r in kept) else 'native',
                   confidence=round(confidence, 3), verification='source_required')
    parsed.warnings.append(f'PDF 第 {page_index} 页按图纸建立检索索引；图名、图号、说明和明细请结合原图核对，不转述全图尺寸。')
