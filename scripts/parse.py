#!/usr/bin/env python3
"""
Parse an ingested document with Docling (layout-aware).

ENFORCEMENT PIPELINE (in order):
  0. GPG signature check  — if manifest.json.sig exists it is verified; if not,
     a WARNING: UNVERIFIED DATA STATE is printed and parsing continues.
     The SHA256 manifest.lock still guarantees content integrity without GPG.
  1. SHA256 verification  — file hash must match manifest (hard exit on mismatch).
  2. PII/PHI smoke-test   — pii_sniff.py runs on extracted text before any output
     is written. HIGH-confidence PII quarantines the file and halts parsing.
  3. Schema validation    — output must conform to the required field structure.
     Location fields are UNKNOWN_STRUCTURE when Docling finds no structural label.

Run from the repository root: python scripts/parse.py <path-to-file>
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OUTPUT_SCHEMA_VERSION = "1.0"


def get_file_sha256(path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def check_manifest_signature(manifest_path: Path, compliance_mode: bool = False) -> str:
    """
    Check whether manifest.json has a valid GPG detached signature.

    Returns one of:
      "SIGNED"    — signature exists and GPG verified it successfully
      "UNSIGNED"  — no .sig file found
      "INVALID"   — .sig file exists but GPG verification failed

    Behaviour by mode:
      Research Mode  : UNSIGNED → WARNING (non-blocking). Parsing continues.
      Compliance Mode: UNSIGNED → CRITICAL hard exit (code 1). GPG is required.
      Either mode    : INVALID  → CRITICAL hard exit (code 1). Tampering is assumed.
    """
    sig_path = Path(str(manifest_path) + ".sig")

    if not sig_path.exists():
        if compliance_mode:
            print(
                "\n"
                "╔══════════════════════════════════════════════════════════════╗\n"
                "║  CRITICAL: UNSIGNED MANIFEST IN COMPLIANCE MODE             ║\n"
                "╠══════════════════════════════════════════════════════════════╣\n"
                f"║  manifest.json has no GPG signature ({sig_path.name:<27})║\n"
                "║  Compliance Mode requires a signed manifest for full        ║\n"
                "║  provenance attribution.                                    ║\n"
                "╠══════════════════════════════════════════════════════════════╣\n"
                "║  Fix: python scripts/ingest.py --sign <file>                ║\n"
                "║       or: gpg --detach-sign --armor data/manifest.json      ║\n"
                "║  Then re-run parse.py.                                      ║\n"
                "╚══════════════════════════════════════════════════════════════╝\n",
                file=sys.stderr,
            )
            sys.exit(1)
        # Research Mode: unsigned is a warning only — SHA256 manifest.lock still
        # guarantees content integrity. GPG adds identity attribution, not content integrity.
        print(
            "\n[WARNING] UNVERIFIED DATA STATE\n"
            f"          manifest.json has no GPG signature ({sig_path.name} not found).\n"
            "          Your SHA256 manifest.lock still guarantees content integrity.\n"
            "          To add identity attribution: gpg --detach-sign --armor data/manifest.json\n"
            "          Or re-run:  python scripts/ingest.py --sign <file>\n"
            "          Parsing will continue with manifest_signature = UNSIGNED.\n"
            "          Use --mode compliance to make GPG signing required.\n",
            file=sys.stderr,
        )
        return "UNSIGNED"

    # Signature file exists — verify with GPG
    try:
        result = subprocess.run(
            ["gpg", "--verify", str(sig_path), str(manifest_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # Extract signer info from GPG output if available
            signer_line = next(
                (l for l in result.stderr.splitlines() if "Good signature" in l or "key" in l.lower()),
                "signature verified"
            )
            print(f"[OK] Manifest GPG-signed and verified — {signer_line.strip()}")
            return "SIGNED"
        else:
            print(
                "\n"
                "╔══════════════════════════════════════════════════════════════╗\n"
                "║  CRITICAL: MANIFEST SIGNATURE INVALID                       ║\n"
                "╠══════════════════════════════════════════════════════════════╣\n"
                "║  GPG verification FAILED for manifest.json.                 ║\n"
                "║  The manifest may have been tampered with after signing.    ║\n"
                f"║  GPG says: {result.stderr.strip()[:50]:<50}║\n"
                "╠══════════════════════════════════════════════════════════════╣\n"
                "║  CHAIN OF CUSTODY BREACH — parsing is halted.               ║\n"
                "║  Re-ingest documents and re-sign to restore integrity.      ║\n"
                "╚══════════════════════════════════════════════════════════════╝\n",
                file=sys.stderr,
            )
            sys.exit(1)
    except FileNotFoundError:
        print(
            "[WARN] GPG not available — cannot verify manifest signature.\n"
            "       Install GPG to enable manifest verification.\n"
            "       Treating manifest as UNTRUSTED.",
            file=sys.stderr,
        )
        return "UNSIGNED"


def load_manifest(manifest_path: Path) -> list[dict]:
    if not manifest_path.exists():
        print(f"[ERROR] Manifest not found: {manifest_path}. Run ingest.py first.", file=sys.stderr)
        sys.exit(1)
    with open(manifest_path) as f:
        return json.load(f)


def find_manifest_entry(manifest: list[dict], filename: str, source_path: str) -> dict | None:
    for entry in manifest:
        if entry.get("filename") == filename or entry.get("source_path") == source_path:
            return entry
    return None


def verify_sha256(actual: str, expected: str, path: Path) -> None:
    """Hard enforcement gate. Exits with code 1 if hashes do not match."""
    if actual != expected:
        print(
            "\n"
            "╔══════════════════════════════════════════════════════════════╗\n"
            "║  CRITICAL: PROVENANCE BREACH DETECTED                       ║\n"
            "╠══════════════════════════════════════════════════════════════╣\n"
            f"║  File     : {path.name:<50}║\n"
            f"║  On disk  : {actual:<50}║\n"
            f"║  Manifest : {expected:<50}║\n"
            "╠══════════════════════════════════════════════════════════════╣\n"
            "║  The file has been modified, corrupted, or substituted      ║\n"
            "║  since it was ingested. Parsing is HALTED.                  ║\n"
            "║  Re-ingest the correct file to restore chain of custody.    ║\n"
            "╚══════════════════════════════════════════════════════════════╝\n",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"[OK] Provenance verified: {path.name}")
    print(f"     SHA256: {actual}")


def extract_elements(doc: Any) -> list[dict]:
    """
    Walk Docling document elements and produce a list of structured items,
    each with a strict location field.

    If Docling does not provide a recognizable structural label (section
    heading, table identifier, figure label), the location is set to
    UNKNOWN_STRUCTURE so downstream consumers can never mistake a guessed
    label for a verified one.
    """
    elements = []

    # Attempt to use Docling's element iteration if available
    doc_body = getattr(doc, "body", None)
    if doc_body is not None and hasattr(doc_body, "children"):
        _walk_docling_body(doc_body.children, elements, current_section="UNKNOWN_STRUCTURE")
    else:
        # Fallback: export as markdown and represent as a single unstructured block
        elements.append(
            {
                "type": "text",
                "location": "UNKNOWN_STRUCTURE",
                "content": doc.export_to_markdown(),
            }
        )

    return elements if elements else [
        {
            "type": "text",
            "location": "UNKNOWN_STRUCTURE",
            "content": doc.export_to_markdown(),
        }
    ]


def _walk_docling_body(children: list, elements: list, current_section: str) -> str:
    """Recursively walk Docling body children, tracking section context."""
    for child in children:
        label = getattr(child, "label", None)
        text = getattr(child, "text", None) or ""

        if label in ("section_header", "title"):
            # Use the heading text as the running section label
            current_section = text.strip() or "UNKNOWN_STRUCTURE"
            elements.append(
                {
                    "type": "section_header",
                    "location": current_section,
                    "content": text.strip(),
                }
            )

        elif label == "table":
            # Prefer a caption or fall back to UNKNOWN_STRUCTURE
            caption = getattr(child, "caption", None) or ""
            table_label = caption.strip() if caption.strip() else "UNKNOWN_STRUCTURE"
            elements.append(
                {
                    "type": "table",
                    "location": f"{current_section} > {table_label}",
                    "content": text.strip(),
                }
            )

        elif label in ("figure", "picture"):
            caption = getattr(child, "caption", None) or ""
            fig_label = caption.strip() if caption.strip() else "UNKNOWN_STRUCTURE"
            elements.append(
                {
                    "type": "figure",
                    "location": f"{current_section} > {fig_label}",
                    "content": caption.strip() or "",
                }
            )

        elif label in ("text", "paragraph", "list_item", "code"):
            elements.append(
                {
                    "type": label or "text",
                    "location": current_section,
                    "content": text.strip(),
                }
            )

        # Recurse into nested children
        child_children = getattr(child, "children", []) or []
        if child_children:
            current_section = _walk_docling_body(child_children, elements, current_section)

    return current_section


def build_output(entry: dict, elements: list[dict], digest: str, sig_status: str) -> dict:
    """Assemble the strictly-typed output document."""
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "provenance": {
            "filename": entry["filename"],
            "sha256": digest,
            "ingested_at": entry["ingested_at"],
            "git_commit": entry.get("git_commit", "UNKNOWN"),
            "source_path": entry["source_path"],
            "parsed_at": datetime.now(tz=timezone.utc).isoformat(),
            "verified": True,
            "manifest_signature": sig_status,
        },
        "elements": elements,
    }


def validate_output_schema(output: dict) -> None:
    """
    Lightweight schema check before writing. Raises ValueError on violation.
    Full jsonschema validation can be added here; this guards the required fields.
    """
    required_top = {"schema_version", "provenance", "elements"}
    required_provenance = {"filename", "sha256", "ingested_at", "source_path", "verified", "manifest_signature"}

    missing_top = required_top - set(output.keys())
    if missing_top:
        raise ValueError(f"Output missing required top-level fields: {missing_top}")

    missing_prov = required_provenance - set(output.get("provenance", {}).keys())
    if missing_prov:
        raise ValueError(f"Output provenance missing required fields: {missing_prov}")

    for i, el in enumerate(output.get("elements", [])):
        if "location" not in el:
            raise ValueError(f"Element {i} missing 'location' field")
        if "content" not in el:
            raise ValueError(f"Element {i} missing 'content' field")
        # Enforce: location must never be an empty string
        if el["location"] == "":
            raise ValueError(
                f"Element {i} has empty 'location'. Must be a structural label or UNKNOWN_STRUCTURE."
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Parse a document with Docling.\n\n"
            "Enforcement pipeline: GPG check → SHA256 verification → PII smoke-test → schema validation.\n\n"
            "  --mode research   (default) — GPG unsigned is a warning; parsing continues.\n"
            "  --mode compliance           — GPG signing required; exits 1 if unsigned.\n\n"
            "Note: a successful parse confirms provenance and structural integrity only.\n"
            "Interpretive conclusions remain the researcher's responsibility."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
    parser.add_argument(
        "--mode",
        choices=["research", "compliance"],
        default="research",
        help=(
            "Rigor mode. 'research' (default): GPG unsigned is a warning. "
            "'compliance': GPG signature required, exits 1 if unsigned."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Shorthand for --mode compliance.",
    )
    args = parser.parse_args()

    compliance_mode = args.strict or (args.mode == "compliance")

    path = args.file.resolve()
    if not path.exists():
        print(f"[ERROR] File not found: {path}", file=sys.stderr)
        sys.exit(1)

    manifest_path = args.manifest.resolve()

    # ── Step 0: GPG signature check ────────────────────────────────────────
    # Research Mode : UNSIGNED → warning only (SHA256 lock still guarantees integrity)
    # Compliance Mode: UNSIGNED → hard exit (GPG attribution required)
    sig_status = check_manifest_signature(manifest_path, compliance_mode=compliance_mode)

    # ── Step 1: load manifest ─────────────────────────────────────────────
    manifest = load_manifest(manifest_path)

    # ── Step 2: find manifest entry by name or path ───────────────────────
    entry = find_manifest_entry(manifest, path.name, str(path))
    if not entry:
        print(
            f"[ERROR] File is not in the chain of custody (manifest).\n"
            f"        Run ingest.py on this file first.\n  {path}",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Step 3: HARD ENFORCEMENT — verify SHA256 before any parsing ────────
    actual_digest = get_file_sha256(path)
    verify_sha256(actual_digest, entry["sha256"], path)

    # ── Step 4: Docling parse ──────────────────────────────────────────────
    from docling.document_converter import DocumentConverter

    print(f"[INFO] Parsing: {path.name}")
    converter = DocumentConverter()
    result = converter.convert(str(path))
    doc = result.document

    # ── Step 4b: PII/PHI smoke-test (pre-write hook) ───────────────────────
    # Extract plain text from the parsed doc and run the sniffer before
    # writing any output. If PII is detected, quarantine and halt.
    try:
        from scripts.pii_sniff import scan_text, assess_risk
    except ImportError:
        # Support running as a top-level script; adjust sys.path
        import importlib.util
        _spec = importlib.util.spec_from_file_location(
            "pii_sniff", Path(__file__).parent / "pii_sniff.py"
        )
        _mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
        _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
        scan_text = _mod.scan_text
        assess_risk = _mod.assess_risk

    plain_text = doc.export_to_markdown()
    pii_matches = scan_text(plain_text)
    quarantine_flag, pii_reason = assess_risk(pii_matches)

    if quarantine_flag:
        quarantine_dir = path.parent.parent / "quarantine"
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        dest = quarantine_dir / path.name
        shutil.move(str(path), str(dest))
        print(
            "\n"
            "╔══════════════════════════════════════════════════════════════╗\n"
            "║  WARNING: PII/PHI DETECTED — DOCUMENT QUARANTINED           ║\n"
            "╠══════════════════════════════════════════════════════════════╣\n"
            f"║  File     : {path.name:<50}║\n"
            f"║  Reason   : {pii_reason[:50]:<50}║\n"
            f"║  Moved to : {str(dest)[:50]:<50}║\n"
            "╠══════════════════════════════════════════════════════════════╣\n"
            "║  Parsing is HALTED. A qualified reviewer must inspect the   ║\n"
            "║  document before it re-enters the pipeline.                 ║\n"
            "║  Follow your institution's HIPAA breach protocol if PII     ║\n"
            "║  is confirmed in a document that should not contain it.     ║\n"
            "╚══════════════════════════════════════════════════════════════╝\n",
            file=sys.stderr,
        )
        sys.exit(1)

    if pii_matches:
        print(
            f"[INFO] PII smoke-test: {len(pii_matches)} low-risk pattern(s) detected "
            f"(below quarantine threshold). Review manually if unexpected."
        )
    else:
        print("[OK] PII smoke-test: no patterns detected.")

    # ── Step 5: Extract structured elements with strict location labels ────
    elements = extract_elements(doc)
    unknown_count = sum(1 for el in elements if el["location"] == "UNKNOWN_STRUCTURE")
    if unknown_count:
        print(
            f"[WARN] {unknown_count} element(s) could not be assigned a structural location "
            f"and are labeled UNKNOWN_STRUCTURE. This is expected for unstructured or "
            f"scanned documents. Do not infer section labels for these elements.",
            file=sys.stderr,
        )

    # ── Step 6: Build and validate output schema ───────────────────────────
    output = build_output(entry, elements, actual_digest, sig_status)
    try:
        validate_output_schema(output)
    except ValueError as exc:
        print(f"[ERROR] Output schema validation failed: {exc}", file=sys.stderr)
        sys.exit(1)

    # ── Step 7: Write ──────────────────────────────────────────────────────
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / (path.stem + ".json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"[OK] Parsed and written: {out_path}")
    print(f"     Elements: {len(elements)} ({unknown_count} UNKNOWN_STRUCTURE)")


if __name__ == "__main__":
    main()
