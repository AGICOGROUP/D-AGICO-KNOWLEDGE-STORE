import logging
import re
import unicodedata
from functools import lru_cache


@lru_cache(maxsize=1)
def tokenizer():
    import jieba

    jieba.setLogLevel(logging.WARNING)
    return jieba.Tokenizer()


def normalize(value):
    return unicodedata.normalize("NFKC", value or "").strip().casefold()


def segmented(text):
    normalized = normalize(text)
    # Keep full product identifiers alongside Chinese search segmentation.
    tokens = list(tokenizer().cut_for_search(normalized))
    tokens.extend(re.findall(r"[a-z0-9]+(?:[-_.][a-z0-9]+)+", normalized))
    return " ".join(t for t in tokens if t.strip())
