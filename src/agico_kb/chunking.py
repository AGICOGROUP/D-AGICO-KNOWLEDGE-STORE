"""Keep search chunks inside the active embedding model's token window."""

from tokenizers import Tokenizer

from .ingestion import Chunk


def _hard_split(text, counter, budget):
    """Split an unusually long row or sentence at token boundaries without dropping its tail."""
    offsets = counter.encode(text, add_special_tokens=False).offsets
    if not offsets:
        return [text]
    pieces = []
    start = 0
    # A few tokenizers add special tokens during embedding, so reserve headroom.
    for index in range(budget - 1, len(offsets), budget):
        end = offsets[index][1]
        if end > start:
            pieces.append(text[start:end])
            start = end
    if start < len(text):
        pieces.append(text[start:])
    return pieces


def _split_text(text, counter, budget):
    if len(counter.encode(text).ids) <= budget:
        return [text]
    # Prefer complete lines, which correspond to table rows in Word and Excel.
    if "\n" not in text:
        return _hard_split(text, counter, budget)
    lines = text.splitlines()
    header = lines[0] if len(lines) > 1 else ""
    repeat_header = bool(header) and len(counter.encode(header).ids) <= min(32, budget // 3)
    result = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(counter.encode(candidate).ids) <= budget:
            current = candidate
            continue
        if current:
            result.append(current)
        prefix = header + "\n" if repeat_header and result and line != header else ""
        if len(counter.encode(prefix + line).ids) <= budget:
            current = prefix + line
        else:
            room = budget - len(counter.encode(prefix).ids) if prefix else budget
            pieces = _hard_split(line, counter, max(1, room))
            result.extend(prefix + part for part in pieces[:-1])
            current = prefix + pieces[-1]
    if current:
        result.append(current)
    return result


def split_for_embedding(chunks: list[Chunk], tokenizer: Tokenizer) -> list[Chunk]:
    """Split only overflowing chunks, retaining source locators and full readable text."""
    truncation = tokenizer.truncation
    if not truncation:
        return chunks
    limit = truncation["max_length"]
    budget = max(1, min(limit - 1, int(limit * 0.85)))
    counter = Tokenizer.from_str(tokenizer.to_str())
    counter.no_truncation()
    result = []
    for chunk in chunks:
        if len(counter.encode(chunk.text).ids) <= limit:
            result.append(chunk)
            continue
        pieces = _split_text(chunk.text, counter, budget)
        for index, piece in enumerate(pieces, 1):
            result.append(
                Chunk(piece, {**chunk.locator, "part": index, "parts": len(pieces)})
            )
    return result
