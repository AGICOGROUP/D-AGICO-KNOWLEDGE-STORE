"""Isolated, read-only opening excerpt for the bounded naming preflight."""

import sys

from .naming import _content_hint


def main():
    try:
        excerpt = _content_hint(sys.argv[1], sys.argv[2])
    except Exception:  # noqa: BLE001 - malformed input must end this isolated preflight
        return 1
    sys.stdout.buffer.write(excerpt.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
