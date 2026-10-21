"""Create a byte-identical, correctly named copy of a pre-upload local file."""

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path


INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def validate_name(name: str, source: Path) -> None:
    if (not name or name in {".", ".."} or INVALID.search(name)
            or name.endswith((" ", ".")) or len(name) > 240
            or Path(name).name != name):
        raise ValueError("The proposed filename is unsafe or too long.")
    if name.split(".", 1)[0].upper() in RESERVED:
        raise ValueError("The proposed filename is reserved on Windows.")
    if Path(name).suffix.casefold() != source.suffix.casefold():
        raise ValueError("The proposed filename must keep the original extension.")


def prepare(source: Path, name: str, output_dir: Path | None = None) -> dict:
    source = source.expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("Source must be a regular file.")
    validate_name(name, source)
    output_dir = (output_dir or source.parent / "知识库待上传").expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / name
    if target.resolve() == source:
        raise ValueError("The destination must differ from the source.")
    source_hash = digest(source)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file() or digest(target) != source_hash:
            raise FileExistsError(f"A different file already uses the proposed name: {target}")
        status = "identical_copy_exists"
    else:
        created = False
        try:
            with source.open("rb") as input_file, target.open("xb") as output_file:
                created = True
                shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            if digest(target) != source_hash:
                raise IOError("The prepared copy failed byte-for-byte verification.")
        except Exception:
            if created and target.exists():
                target.unlink()
            raise
        status = "copied"
    return {"source": str(source), "prepared": str(target),
            "sha256": source_hash, "status": status}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("name", help="Proposed filename including the original extension")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.source, args.name, args.output_dir)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
