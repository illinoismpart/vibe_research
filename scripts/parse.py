#!/usr/bin/env python3
"""
Parse an ingested document with Docling (layout-aware). Requires the file to be
in the chain of custody (manifest). Saves structured output to data/parsed/
with provenance metadata.
Run from the repository root: python scripts/parse.py <path-to-file>
"""
import argparse
import hashlib
import json
from pathlib import Path


def get_file_sha256(path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse a document with Docling. File must already be in the manifest (ingest first)."
    )
    parser.add_argument("file", type=Path, help="Path to the document (e.g. data/raw/myfile.pdf)")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifest.json"),
        help="Path to manifest file (default: data/manifest.json)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/parsed"),
        help="Directory for parsed output (default: data/parsed)",
    )
    args = parser.parse_args()

    path = args.file.resolve()
    if not path.exists():
        raise SystemExit(f"File not found: {path}")

    # Load manifest
    manifest_path = args.manifest.resolve()
    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}. Run ingest.py first.")
    with open(manifest_path) as f:
        manifest = json.load(f)

    digest = get_file_sha256(path)
    entry = None
    for e in manifest:
        if e.get("sha256") == digest or e.get("source_path") == str(path):
            entry = e
            break

    if not entry:
        raise SystemExit(
            f"File is not in the chain of custody (manifest). Run ingest.py on this file first.\n  {path}"
        )

    # Docling parse
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(path))
    doc = result.document
    content_md = doc.export_to_markdown()

    # Build output with provenance
    output = {
        "provenance": {
            "filename": entry["filename"],
            "sha256": entry["sha256"],
            "ingested_at": entry["ingested_at"],
            "source_path": entry["source_path"],
        },
        "content_md": content_md,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_name = path.stem + ".json"
    out_path = args.output_dir / out_name
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Parsed: {path.name}")
    print(f"  SHA256: {digest}")
    print(f"  Output: {out_path}")


if __name__ == "__main__":
    main()
