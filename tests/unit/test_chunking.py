from tokenizers import Tokenizer, models, pre_tokenizers

from agico_kb.chunking import split_for_embedding
from agico_kb.ingestion import Chunk


def short_tokenizer():
    vocabulary = {"[UNK]": 0, "HEADER": 1}
    vocabulary.update({f"item{i}": i + 2 for i in range(60)})
    tokenizer = Tokenizer(models.WordLevel(vocabulary, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    tokenizer.enable_truncation(max_length=12)
    return tokenizer


def test_long_table_splits_on_rows_without_losing_values():
    tokenizer = short_tokenizer()
    rows = [f"item{i} item{i + 1}" for i in range(0, 30, 2)]
    original = Chunk("HEADER\n" + "\n".join(rows), {"kind": "table", "table": 3})

    parts = split_for_embedding([original], tokenizer)

    assert len(parts) > 1
    assert all(len(tokenizer.encode(part.text).ids) <= 12 for part in parts)
    assert all(part.locator["table"] == 3 for part in parts)
    assert all(part.locator["part"] == index for index, part in enumerate(parts, 1))
    assert all(any(row in part.text for part in parts) for row in rows)
    assert all(part.text.startswith("HEADER\n") for part in parts)
    assert tokenizer.truncation["max_length"] == 12


def test_long_paragraph_splits_without_dropping_the_tail():
    tokenizer = short_tokenizer()
    original = Chunk(" ".join(f"item{i}" for i in range(40)), {"kind": "paragraph", "paragraph": 7})

    parts = split_for_embedding([original], tokenizer)

    assert len(parts) > 1
    assert all(len(tokenizer.encode(part.text).ids) <= 12 for part in parts)
    assert "".join(part.text for part in parts) == original.text
    assert all(part.locator["paragraph"] == 7 for part in parts)
