"""Score fresh ingestion against source-located, independently reviewed facts."""

import argparse
import json
from pathlib import Path

from agico_kb.quality import evaluate_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSON file with source paths and reference checks")
    parser.add_argument("--vision-config", type=Path, help="Existing local vision key file")
    parser.add_argument("--output", type=Path, help="Write the score report to this path")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = evaluate_manifest(manifest, vision_config_file=args.vision_config)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
