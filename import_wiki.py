"""Rebuild offline tables from four downloaded index HTML files."""

import argparse
import json
from pathlib import Path
from wiki_catalog import build_catalog, merge_names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    catalog = build_catalog(args.source_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    names_path = args.output_dir / "names.json"
    names = (
        json.loads(names_path.read_text(encoding="utf-8"))
        if names_path.exists()
        else {}
    )
    overrides = json.loads(
        Path(__file__).with_name("name_overrides.json").read_text(encoding="utf-8")
    )
    for filename, data in [
        ("catalog.json", catalog),
        ("names.json", merge_names(names, catalog, overrides)),
    ]:
        (args.output_dir / filename).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print({key: len(rows) for key, rows in catalog["categories"].items()})


if __name__ == "__main__":
    main()
