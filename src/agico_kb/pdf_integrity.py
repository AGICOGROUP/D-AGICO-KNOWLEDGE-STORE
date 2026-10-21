"""Source-bound PDF numeric checks; confidence and token coverage are not accuracy."""

import re
import unicodedata
from decimal import Decimal

NUMBER = re.compile(r'(?<![A-Za-z0-9_.])(?:[φR]\s*)?(?:[+±−-]\s*)?\d+(?:[.,]\d+)*(?:\s*°)?', re.IGNORECASE)
ENGINEERING = re.compile(r'[φΦØø⌀]\s*\d|\bR\s*\d|[±]\s*\d|\b[A-Z]—[A-Z]\b')
DRAWING_TITLE = re.compile(r'工艺流程图|仪表流程图|尺寸图|(?:图名|图号)\s*[:：]|'
                           r'\b(?:PLANO|DRAWING\s*(?:NO|TITLE|:)|PROCESS FLOW|SECTION [A-Z])\b',
                           re.IGNORECASE)


def numeric_tokens(text):
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'[φΦØø⌀]', 'φ', text).replace('−', '-')
    return {re.sub(r'\s+', '', m.group()).replace('r', 'R') for m in NUMBER.finditer(text)}


def numeric_gaps(evidence, summary):
    """Flag missing source readings; never force uncertain OCR into a model answer.

    Matching tokens do not certify dimensions' spatial association. Signed values,
    diameter/radius prefixes remain distinct. Only unambiguous decimal-point
    formats are compared by value; multipart drawing codes and commas stay exact.
    """
    expected = set().union(*(numeric_tokens(line) for line in evidence)) if evidence else set()
    def equivalent(token):
        match = re.fullmatch(r'([φR]?)([+±-]?)(\d+(?:\.\d+)?)(°?)', token)
        if not match:
            return token
        prefix, sign, value, angle = match.groups()
        return prefix + sign + format(Decimal(value).normalize(), 'f') + angle

    actual = {equivalent(token) for token in numeric_tokens(summary)}
    return sorted(token for token in expected if equivalent(token) not in actual)


def drawing_page(page, parsed, index):
    from .ingestion import _vector_content

    chunks = [c for c in parsed.chunks if c.locator.get('page') == index]
    if any(c.locator.get('pdf_assessment', {}).get('route') == 'ocr_text' for c in chunks):
        return False
    text = '\n'.join(c.text for c in chunks) + '\n' + page.get_text()
    # Prose can contain several tolerance symbols and sparse OCR. Neither
    # turns an ordinary specification into a full-sheet dimension workflow.
    lines = text.splitlines()
    prose_chars = sum(len(t) for t in lines if len(t) >= 65)
    # CJK prose wraps into much shorter lines than English. Sentence evidence
    # prevents ordinary translated specifications from becoming drawing checks.
    cjk = len(re.findall(r'[\u3400-\u9fff]', text))
    sentences = len(re.findall(r'[。；]', text))
    words = len(re.findall(r'\b[A-Za-z]{3,}\b', text))
    latin_sentences = len(re.findall(r'[.!?](?=\s|$)', text))
    if (prose_chars >= 180 and prose_chars / max(1, len(text)) >= .5
            or cjk >= 120 and sentences >= 3
            or words >= 30 and latin_sentences >= 3):
        return False
    labels = len(ENGINEERING.findall(text))
    assessments = [c.locator.get('pdf_assessment', {}) for c in chunks
                   if c.locator.get('kind') == 'ocr']
    sparse = any(a.get('text_line_share', 1) < .65 for a in assessments)
    vector = _vector_content(page)
    return bool(DRAWING_TITLE.search(text) and (vector or sparse)
                or labels and vector or labels >= 2 and sparse)
