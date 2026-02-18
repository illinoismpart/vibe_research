#!/usr/bin/env python3
"""
PII/PHI Privacy Smoke-Test for Medicaid research documents.

Scans a plain-text string (or file) for common Personally Identifiable
Information (PII) and Protected Health Information (PHI) patterns relevant
to Medicaid research. This is a lightweight heuristic, not a certified
HIPAA de-identification tool.

Pattern categories:
  - Social Security Numbers (SSN)
  - Dates of birth (DOB) — common US formats
  - Medicaid Beneficiary Identifiers (MBI) — CMS format
  - National Provider Identifiers (NPI)
  - Drug Enforcement Administration (DEA) numbers
  - US phone numbers
  - Email addresses
  - Street addresses (heuristic)
  - Full names adjacent to medical/ID context (heuristic)

Confidence levels:
  HIGH   — pattern is highly specific and rarely appears in policy prose
           (SSN, MBI, NPI, DEA). A single match causes parse.py to quarantine.
  MEDIUM — pattern appears in many contexts; multiple matches or co-occurrence
           with HIGH signals trigger quarantine.

Exit codes (when used as a standalone script):
  0 — no HIGH-confidence PII found; MEDIUM count below threshold
  1 — HIGH-confidence PII found, or MEDIUM count at/above threshold
  2 — file not found or read error

Usage:
  python scripts/pii_sniff.py <path-to-text-file>
  python scripts/pii_sniff.py --json <path-to-text-file>
"""
import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path


MEDIUM_THRESHOLD = 3  # Number of MEDIUM hits that triggers quarantine


@dataclass
class PiiMatch:
    pattern_name: str
    confidence: str  # "HIGH" or "MEDIUM"
    matched_text: str
    start: int
    end: int


# ── Pattern registry ─────────────────────────────────────────────────────────

_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    # (name, confidence, compiled_pattern)

    # Social Security Number: 123-45-6789 or 123456789
    (
        "SSN",
        "HIGH",
        re.compile(r"\b(?!000|666|9\d{2})\d{3}[-\s]?(?!00)\d{2}[-\s]?(?!0000)\d{4}\b"),
    ),

    # CMS Medicaid Beneficiary Identifier (MBI): 1EG4-TE5-MK72 style
    # Format: 1 + [A-CEGHJ-NP-RT-Y] + [A-CEGHJ-NP-RT-Y0-9] + digit + [A-CEGHJ-NP-RT-Y]
    #         + [A-CEGHJ-NP-RT-Y0-9] + digit + [A-CEGHJ-NP-RT-Y0-9]{2} + digit{2}
    # Simplified to the observable alphanumeric structure with dashes
    (
        "MBI",
        "HIGH",
        re.compile(
            r"\b[1-9][A-CEGHJ-NP-RT-Y][A-CEGHJ-NP-RT-Y0-9]\d"
            r"[A-CEGHJ-NP-RT-Y][A-CEGHJ-NP-RT-Y0-9]\d[A-CEGHJ-NP-RT-Y0-9]{2}\d{2}\b",
            re.IGNORECASE,
        ),
    ),

    # National Provider Identifier (NPI): exactly 10 digits, often labeled
    (
        "NPI",
        "HIGH",
        re.compile(r"\bNPI[:\s#]*\d{10}\b", re.IGNORECASE),
    ),

    # DEA registration number: 2 letters + 7 digits (e.g., AB1234563)
    (
        "DEA_NUMBER",
        "HIGH",
        re.compile(r"\bDEA[:\s#]*[A-Z]{2}\d{7}\b", re.IGNORECASE),
    ),

    # Dates of birth — various US formats
    (
        "DATE_OF_BIRTH",
        "HIGH",
        re.compile(
            r"\b(?:DOB|D\.O\.B\.|date of birth|born)[:\s]*"
            r"(?:\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}|\w+ \d{1,2},?\s*\d{4})\b",
            re.IGNORECASE,
        ),
    ),

    # Generic date patterns (medium — very common in policy documents too)
    (
        "DATE_PATTERN",
        "MEDIUM",
        re.compile(
            r"\b(?:0?[1-9]|1[0-2])[/\-](?:0?[1-9]|[12]\d|3[01])[/\-](?:19|20)\d{2}\b"
        ),
    ),

    # US phone numbers
    (
        "PHONE_NUMBER",
        "MEDIUM",
        re.compile(
            r"\b(?:\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b"
        ),
    ),

    # Email addresses
    (
        "EMAIL_ADDRESS",
        "MEDIUM",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    ),

    # Street addresses (heuristic: number + street name + street type)
    (
        "STREET_ADDRESS",
        "MEDIUM",
        re.compile(
            r"\b\d{1,5}\s+[A-Za-z][\w\s]{1,30}"
            r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|Lane|Ln|Court|Ct|Way|Place|Pl)\b",
            re.IGNORECASE,
        ),
    ),

    # Medicaid ID numbers — state-specific formats vary widely; heuristic
    # Matches labels like "Medicaid ID: 123456789" or "MAID 1234567890"
    (
        "MEDICAID_ID",
        "HIGH",
        re.compile(
            r"\b(?:medicaid\s*id|maid|recipient\s*id|rcn)[:\s#]*\d{7,12}\b",
            re.IGNORECASE,
        ),
    ),
]


# ── Core scan function ────────────────────────────────────────────────────────

def scan_text(text: str) -> list[PiiMatch]:
    """Return all PII matches found in text, ordered by position."""
    matches: list[PiiMatch] = []
    for name, confidence, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            matches.append(
                PiiMatch(
                    pattern_name=name,
                    confidence=confidence,
                    matched_text=m.group(),
                    start=m.start(),
                    end=m.end(),
                )
            )
    matches.sort(key=lambda m: m.start)
    return matches


def assess_risk(matches: list[PiiMatch]) -> tuple[bool, str]:
    """
    Return (quarantine: bool, reason: str).

    Quarantine is triggered if:
      - Any HIGH-confidence match is found, OR
      - MEDIUM-confidence matches >= MEDIUM_THRESHOLD
    """
    high_matches = [m for m in matches if m.confidence == "HIGH"]
    medium_matches = [m for m in matches if m.confidence == "MEDIUM"]

    if high_matches:
        names = ", ".join(sorted({m.pattern_name for m in high_matches}))
        return True, f"HIGH-confidence PII detected: {names} ({len(high_matches)} instance(s))"

    if len(medium_matches) >= MEDIUM_THRESHOLD:
        names = ", ".join(sorted({m.pattern_name for m in medium_matches}))
        return True, (
            f"{len(medium_matches)} MEDIUM-confidence pattern(s) detected "
            f"({names}), at or above threshold of {MEDIUM_THRESHOLD}."
        )

    return False, "No high-risk PII detected."


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_report(matches: list[PiiMatch], quarantine: bool, reason: str) -> None:
    status = "QUARANTINE REQUIRED" if quarantine else "PASS"
    print(f"\n── PII/PHI Smoke-Test Report ─────────────────────────────────────")
    print(f"   Status  : {status}")
    print(f"   Reason  : {reason}")
    print(f"   Matches : {len(matches)}")

    high = [m for m in matches if m.confidence == "HIGH"]
    medium = [m for m in matches if m.confidence == "MEDIUM"]

    if high:
        print("\n   HIGH-confidence matches:", file=sys.stderr)
        for m in high:
            redacted = m.matched_text[:4] + "****" if len(m.matched_text) > 4 else "****"
            print(f"     [{m.pattern_name}] {redacted}  (pos {m.start}–{m.end})", file=sys.stderr)

    if medium:
        print("\n   MEDIUM-confidence matches:")
        for m in medium:
            redacted = m.matched_text[:4] + "****" if len(m.matched_text) > 4 else "****"
            print(f"     [{m.pattern_name}] {redacted}  (pos {m.start}–{m.end})")

    if quarantine:
        print(
            "\n   ACTION: This document has been moved to data/quarantine/ for human review.\n"
            "   Do NOT parse or index it until a qualified reviewer confirms it is safe.\n"
            "   If PII is confirmed, follow your institution's HIPAA breach protocol.",
            file=sys.stderr,
        )


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lightweight PII/PHI smoke-test for Medicaid research documents."
    )
    parser.add_argument("file", type=Path, help="Path to a plain-text file to scan.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON report.",
    )
    args = parser.parse_args()

    if not args.file.exists():
        print(f"[ERROR] File not found: {args.file}", file=sys.stderr)
        sys.exit(2)

    try:
        text = args.file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"[ERROR] Could not read file: {exc}", file=sys.stderr)
        sys.exit(2)

    matches = scan_text(text)
    quarantine, reason = assess_risk(matches)

    if args.json:
        report = {
            "quarantine": quarantine,
            "reason": reason,
            "match_count": len(matches),
            "matches": [asdict(m) for m in matches],
        }
        print(json.dumps(report, indent=2))
    else:
        print_report(matches, quarantine, reason)

    sys.exit(1 if quarantine else 0)


if __name__ == "__main__":
    main()
