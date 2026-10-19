"""Conservative local routing of scanned prose; uncertainty keeps visual fallback."""

import re

import numpy as np

ROUTE_VERSION = 'ocr-prose-v3-numeric'


def text_scan_assessment(image, boxes, texts, scores):
    """OCR confidence is a routing signal, never an accuracy certificate.

    Long horizontal language lines must explain nearly all page ink. Short labels,
    formulas, grid lines and connected graphics retain the visual route.
    """
    result = {'route': 'vision', 'route_version': ROUTE_VERSION}
    if boxes is None or scores is None or not texts:
        return result
    if len(boxes) != len(texts) or len(scores) != len(texts) or len(texts) < 6:
        return result
    try:
        import cv2

        pixels = np.asarray(image)
        if pixels.ndim != 3 or min(pixels.shape[:2]) < 200:
            return result
        h, w = pixels.shape[:2]
        geometry = [np.asarray(box, dtype=float) for box in boxes]
        if any(coords.shape != (4, 2) or not np.isfinite(coords).all() for coords in geometry):
            return result
        # Metadata in a lower title block is not a drawing in the body. Only a
        # positively identified footer permits excluding that lower region.
        footer = h
        for coords, text in zip(geometry, texts):
            y0 = float(coords[:, 1].min())
            if y0 > h*.78 and re.search(
                    r'H[O0]JA\s*(?:[:：]|\d)|\b(?:REVISO|APROBO)\b|第\s*\d+\s*页',
                    text, re.IGNORECASE):
                footer = min(footer, max(int(h*.78), int(y0-h*.04)))
        lower_labels = [(float(coords[:, 1].min()), text) for coords, text in zip(geometry, texts)
                        if float(coords[:, 1].min()) > h*.80]
        # OCR can split or misread the page-number label. Require two distinct
        # metadata fields before accepting a title block without that label.
        has_title = any(re.search(r'ESPECIFICACI[OÓ]N\s+TECNICA', t, re.IGNORECASE)
                        for _, t in lower_labels)
        has_field = any(re.search(r'No\.\s*DE\s*(?:ESPECIFICACI[OÓ]N|MODIFICACI[OÓ]N)|D\.?\s*P\.?\s*E\.?',
                                 t, re.IGNORECASE) for _, t in lower_labels)
        if footer == h and has_title and has_field:
            footer = min(int(y-h*.02) for y, t in lower_labels
                         if re.search(r'ESPECIFICACI[OÓ]N\s+TECNICA', t, re.IGNORECASE))
        if footer < h:
            for coords, text in zip(geometry, texts):
                y0 = float(coords[:, 1].min())
                if y0 > h*.80 and re.search(
                        r'ESPECIFICACION\s+TECNICA|No\.\s*DE\s*(?:ESPECIFICACION|MODIFICACION)|'
                        r'\b(?:ENERO|FEBRERO|MARZO|ABRIL|MAYO|JUNIO|JULIO|AGOST[O0]|'
                        r'SEPTIEMBRE|OCTUBRE|NOVIEMBRE|DICIEMBRE)\s*[/ -]\s*\d{2,4}',
                        text, re.IGNORECASE):
                    footer = min(footer, int(y0-h*.01))
        body_indices = [i for i, coords in enumerate(geometry) if coords[:, 1].mean() < footer]
        confidence = np.asarray([scores[i] for i in body_indices], dtype=float)
        if len(confidence) < 6:
            return result
        if not np.isfinite(confidence).all():
            return result
        weights = np.asarray([max(1, len(re.sub(r'\W', '', texts[i]))) for i in body_indices])
        low_confidence_share = float(weights[confidence < .8].sum() / weights.sum())
        confidence_ok = float(np.median(confidence)) >= .92 and low_confidence_share <= .05
        from .pdf_integrity import numeric_tokens
        numeric_uncertain = any(float(scores[i]) < .90 and numeric_tokens(texts[i])
                                for i in body_indices)
        confidence_ok = confidence_ok and not numeric_uncertain
        chars = sum(len(re.sub(r'\W', '', texts[i])) for i in body_indices)
        long_chars = 0
        mask = np.zeros(pixels.shape[:2], dtype=np.uint8)
        formula_lines = 0
        for i in body_indices:
            coords, text = geometry[i], texts[i]
            x0, y0 = np.floor(coords.min(axis=0)).astype(int)
            x1, y1 = np.ceil(coords.max(axis=0)).astype(int)
            letters = sum(ch.isalpha() for ch in text)
            count = len(re.sub(r'\W', '', text))
            if count >= 15 and letters / max(1, count) >= .65 and (x1-x0) > 3*(y1-y0):
                long_chars += count
            if re.search(r'[=√∑∫]|(?:\b(?:sin|cos|sqrt)\b)', text, re.IGNORECASE):
                formula_lines += 1
            polygon = np.rint(coords).astype(np.int32)
            cv2.fillPoly(mask, [polygon], 1)
        mask = cv2.dilate(mask, np.ones((7, 7), dtype=np.uint8))
        ink = np.min(pixels, axis=2) < 190
        # Exclude only the outside frame, not title blocks or diagrams in the page body.
        edge_y, edge_x = max(1, h//50), max(1, w//50)
        ink[:edge_y] = False
        ink[-edge_y:] = False
        ink[:, :edge_x] = False
        ink[:, -edge_x:] = False
        ink[footer:] = False
        # Remove long outer frame rules, not interior table or diagram rules.
        # Scanned documents often have a frame well inside the physical edge.
        for kernel, axis, size in [((1, max(3, h//2)), 0, w), ((max(3, w*3//4), 1), 1, h)]:
            frame_ink = cv2.dilate(ink.astype(np.uint8), np.ones((5, 5), np.uint8))
            lines = cv2.morphologyEx(frame_ink, cv2.MORPH_OPEN,
                                     cv2.getStructuringElement(cv2.MORPH_RECT, kernel))
            count, labels, stats, _ = cv2.connectedComponentsWithStats(lines, 8)
            for component in range(1, count):
                pos = stats[component, axis]
                if pos < size*.12 or pos > size*.88:
                    frame = cv2.dilate((labels == component).astype(np.uint8), np.ones((9, 9), np.uint8))
                    ink[frame != 0] = False
        residual = (ink & (mask == 0)).astype(np.uint8)
        structural_counts = []
        for kernel in [(max(3, w//5), 1), (1, max(3, h//12))]:
            lines = cv2.morphologyEx(residual, cv2.MORPH_OPEN,
                                     cv2.getStructuringElement(cv2.MORPH_RECT, kernel))
            count, _, _, _ = cv2.connectedComponentsWithStats(lines, 8)
            structural_counts.append(max(0, count-1))
        structural_lines = structural_counts[0] >= 2 and structural_counts[1] >= 1
        if footer < h:
            count, labels, stats, _ = cv2.connectedComponentsWithStats(residual, 8)
            for component in range(1, count):
                x, y, cw, ch, _ = stats[component]
                # Small wordless signatures just above an identified title
                # block require no narration. Text-bearing graphics stay.
                if y > h*.68 and cw < w*.22 and ch < h*.12:
                    contains_text = any(
                        x <= coords[:, 0].mean() <= x+cw and y <= coords[:, 1].mean() <= y+ch
                        for coords in geometry)
                    if not contains_text:
                        residual[labels == component] = 0
        ratio = float(residual.sum() / max(1, ink.sum()))
        _, _, stats, _ = cv2.connectedComponentsWithStats(residual, 8)
        largest = int(stats[1:, cv2.CC_STAT_AREA].max()) if len(stats) > 1 else 0
        result.update(chars=chars, text_line_share=round(long_chars/max(1, chars), 3),
                      median_confidence=round(float(np.median(confidence)), 3),
                      residual_ink=round(ratio, 3), largest_graphic=largest,
                      footer_excluded=footer < h, structural_lines=structural_counts,
                      low_confidence_share=round(low_confidence_share, 3),
                      numeric_uncertain=bool(numeric_uncertain),
                      formula_lines=formula_lines)
        if (confidence_ok and chars >= 350 and long_chars/max(1, chars) >= .75
                and formula_lines < 2 and not structural_lines
                and ratio <= .20 and largest < h*w*.005):
            result['route'] = 'ocr_text'
    except Exception:  # noqa: BLE001 - optional screening must never discard recognised OCR text
        return result
    return result
