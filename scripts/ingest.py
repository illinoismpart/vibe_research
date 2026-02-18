#!/usr/bin/env python3
"""
Ingest a document into the chain of custody: compute SHA256, append to manifest.
Run from the repository root: python scripts/ingest.py <path-to-file>
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a document and add it to the manifest (chain of custody).")
    parser.add_argument("file", type=Path, help="Path to the document (e.g. data/raw/myfile.pdf)")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifest.json"),
        help="Path to manifest file (default: data/manifest.json)",
    )
    args = parser.parse_args()

    path = args.file.resolve()
    if not path.exists():
        raise SystemExit(f"File not found: {path}")

    # Compute SHA256
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    digest = sha256.hexdigest()

    # Load manifest
    manifest_path = args.manifest.resolve()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
    else:
        manifest = []

    # Append entry
    entry = {
        "filename": path.name,
        "sha256": digest,
        "ingested_at": datetime.now(tz=timezone.utc).isoformat(),
        "source_path": str(path),
    }
    manifest.append(entry)

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Ingested: {path.name}")
    print(f"  SHA256: {digest}")
    print(f"  Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
