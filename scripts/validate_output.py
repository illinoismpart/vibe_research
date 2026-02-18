#!/usr/bin/env python3
"""
Validate an AI-generated response against the chain of custody.

TWO MODES — select with --mode or the shorthand flags:

  --mode research  (default, alias: omit flag)
  ─────────────────────────────────────────────────────────────────────────────
  • Citation Density threshold : 0.7  (70% of claim sentences must have a SHA256)
  • Unverified citations        : warns, does not block
  • Audit log                   : always appended
  • On failure                  : exits 1 (unless --draft is set)

  --mode compliance  (alias: --strict)
  ─────────────────────────────────────────────────────────────────────────────
  • Citation Density threshold : 1.0  (100%, no exceptions)
  • Unverified citations        : hard failure, exits 1
  • Audit log                   : always appended
  • Note: a PASS here means structural integrity only, not analytical validity.
    The researcher remains the sole authority for interpretive correctness.

  --draft  (can be combined with either mode)
  ─────────────────────────────────────────────────────────────────────────────
  • Runs all checks and prints Tip-style fix-it hints.
  • Always exits 0 — never blocks your writing session.
  • Audit log records the run as <MODE>-DRAFT.

THREE LAYERS OF CHECKING:

  1. CITATION VERIFICATION — every SHA256 and filename cited in the response is
     looked up in manifest.json. Unrecognized values are flagged as potential
     hallucinations.

  2. CITATION DENSITY (POS-heuristic) — sentences are classified as "claims" if
     they contain:
       NNP  — a capitalized proper noun mid-sentence (e.g. "Illinois", "CMS")
       CD   — a cardinal number or percentage (e.g. "five", "2023", "40%")
       JJR  — a comparative or superlative form (e.g. "higher", "most")
     Claims without a manifest-backed SHA256 receive a Tip-style fix-it hint.

  3. AUDIT LOG — every run appends a row to data/audit_log.csv:
       Timestamp, Git_Commit, Mode, Citation_Score, Status

Exit codes:
  0  — all checks passed (or --draft mode regardless of result)
  1  — one or more checks failed
  2  — manifest could not be loaded or input unreadable

Usage:
  python scripts/validate_output.py --input response.txt
  python scripts/validate_output.py --input response.txt --mode compliance
  python scripts/validate_output.py --input response.txt --draft
  python scripts/validate_output.py --input response.txt --mode compliance --draft
  python scripts/validate_output.py --input response.txt --json-report
"""
import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── Constants ─────────────────────────────────────────────────────────────────

RESEARCH_THRESHOLD = 0.70
COMPLIANCE_THRESHOLD = 1.00


# ── Patterns ──────────────────────────────────────────────────────────────────

SHA256_PATTERN = re.compile(r"\b([0-9a-f]{64})\b")

FILENAME_PATTERN = re.compile(
    r"""
    (?:
        "filename"\s*:\s*"([^"]+)"
        |
        'filename'\s*:\s*'([^']+)'
        |
        \bfile[:\s]+([^\s,;\"\']+\.[a-zA-Z0-9]+)
        |
        \b([\w.\-]+\.(?:pdf|PDF|docx|DOCX|txt|TXT|json|JSON|csv|CSV|xlsx|XLSX))\b
    )
    """,
    re.VERBOSE,
)

# ── POS-heuristic patterns ────────────────────────────────────────────────────

_FUNCTION_WORDS = frozenset(
    {
        "The", "A", "An", "This", "That", "These", "Those", "It", "He", "She",
        "We", "They", "I", "You", "His", "Her", "Its", "Our", "Their",
        "If", "When", "While", "Although", "Because", "Since", "After",
        "Before", "And", "But", "Or", "Nor", "So", "Yet", "For",
        "In", "On", "At", "By", "To", "Of", "With", "From", "As",
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    }
)

_CAPITAL_WORD = re.compile(r"\b([A-Z][a-z]{1,})\b")

_NUMBER_PATTERN = re.compile(
    r"""
    \b(?:
        \d[\d,]*(?:\.\d+)?
        | \d+%
        | (?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve
           |thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen
           |twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety
           |hundred|thousand|million|billion|trillion)
        | percent
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

_COMPARATIVE_PATTERN = re.compile(
    r"""
    \b(?:
        \w+er | \w+est
        | more\s+\w+ | most\s+\w+
        | fewer\s+\w+ | least\s+\w+
        | greater\s+than | higher\s+than | lower\s+than
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

SENTENCE_SPLITTER = re.compile(r"(?<=[.!?])\s+")


# ── Utilities ─────────────────────────────────────────────────────────────────

def get_git_commit() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return r.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "NO_GIT_COMMIT"


# ── Manifest ──────────────────────────────────────────────────────────────────

def load_manifest(manifest_path: Path) -> tuple[set[str], set[str]]:
    if not manifest_path.exists():
        print(f"[ERROR] Manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(2)
    with open(manifest_path) as f:
        manifest = json.load(f)
    known_sha256s = {e["sha256"] for e in manifest if "sha256" in e}
    known_filenames = {e["filename"] for e in manifest if "filename" in e}
    return known_sha256s, known_filenames


# ── Citation verification ─────────────────────────────────────────────────────

def extract_sha256s(text: str) -> set[str]:
    return set(SHA256_PATTERN.findall(text))


def extract_filenames(text: str) -> set[str]:
    filenames: set[str] = set()
    for match in FILENAME_PATTERN.finditer(text):
        for group in match.groups():
            if group:
                filenames.add(group.strip())
    return filenames


def verify_citations(
    response_text: str, known_sha256s: set[str], known_filenames: set[str]
) -> tuple[list[dict], list[dict]]:
    verified: list[dict] = []
    unverified: list[dict] = []

    for h in sorted(extract_sha256s(response_text)):
        if h in known_sha256s:
            verified.append({"type": "sha256", "value": h, "status": "VERIFIED"})
        else:
            unverified.append({
                "type": "sha256", "value": h, "status": "UNVERIFIED",
                "detail": "SHA256 not found in manifest. Never ingested or fabricated.",
            })

    for name in sorted(extract_filenames(response_text)):
        if name in known_filenames:
            verified.append({"type": "filename", "value": name, "status": "VERIFIED"})
        else:
            unverified.append({
                "type": "filename", "value": name, "status": "UNVERIFIED",
                "detail": "Filename not found in manifest. Never ingested or fabricated.",
            })

    return verified, unverified


# ── POS-heuristic claim detection ─────────────────────────────────────────────

def _pos_signals_with_examples(sentence: str) -> tuple[list[str], dict[str, list[str]]]:
    """
    Return (signal_names, examples) where examples maps signal → list of
    triggering tokens. Used to generate specific fix-it hints.
    """
    signals: list[str] = []
    examples: dict[str, list[str]] = {}

    tokens = sentence.split()
    if not tokens:
        return signals, examples

    # NNP: capitalized mid-sentence words that are not common function words
    rest = " ".join(tokens[1:])
    nnp_hits = [
        m.group(1) for m in _CAPITAL_WORD.finditer(rest)
        if m.group(1) not in _FUNCTION_WORDS
    ]
    if nnp_hits:
        signals.append("NNP")
        examples["NNP"] = nnp_hits[:3]

    # CD: numeric forms
    cd_hits = [m.group() for m in _NUMBER_PATTERN.finditer(sentence)]
    if cd_hits:
        signals.append("CD")
        examples["CD"] = cd_hits[:3]

    # JJR/JJS: comparative/superlative
    jjr_hits = [m.group() for m in _COMPARATIVE_PATTERN.finditer(sentence)]
    if jjr_hits:
        signals.append("JJR")
        examples["JJR"] = jjr_hits[:3]

    return signals, examples


def is_claim_sentence(sentence: str) -> tuple[bool, list[str], dict[str, list[str]]]:
    signals, examples = _pos_signals_with_examples(sentence)
    return bool(signals), signals, examples


def sentence_has_sha256_citation(sentence: str, known_sha256s: set[str]) -> bool:
    return bool(extract_sha256s(sentence) & known_sha256s)


def compute_citation_density(response_text: str, known_sha256s: set[str]) -> dict:
    sentences = [s.strip() for s in SENTENCE_SPLITTER.split(response_text) if s.strip()]
    claim_records: list[dict] = []
    naked_claims: list[dict] = []
    cited_count = 0
    non_claim_count = 0

    for sentence in sentences:
        is_claim, signals, examples = is_claim_sentence(sentence)
        if is_claim:
            has_cite = sentence_has_sha256_citation(sentence, known_sha256s)
            record = {
                "sentence": sentence,
                "signals": signals,
                "signal_examples": examples,
                "cited": has_cite,
            }
            claim_records.append(record)
            if has_cite:
                cited_count += 1
            else:
                naked_claims.append(record)
        else:
            non_claim_count += 1

    factual_count = len(claim_records)
    density = cited_count / factual_count if factual_count > 0 else None

    return {
        "factual_count": factual_count,
        "cited_count": cited_count,
        "naked_claims": naked_claims,
        "citation_density": density,
        "non_factual_count": non_claim_count,
        "total_sentences": len(sentences),
    }


# ── Fix-it hints ──────────────────────────────────────────────────────────────

def build_fixit_hint(record: dict, manifest_filenames: set[str], mode: str = "RESEARCH") -> str:
    """
    Build a human-readable hint explaining why this sentence was flagged and
    how to fix it. Names the specific triggering tokens.
    """
    parts: list[str] = []
    examples = record.get("signal_examples", {})

    if "NNP" in examples:
        nouns = ", ".join(f"'{w}'" for w in examples["NNP"])
        parts.append(f"proper noun(s) {nouns} (NNP)")
    if "CD" in examples:
        nums = ", ".join(f"'{w}'" for w in examples["CD"])
        parts.append(f"number(s) {nums} (CD)")
    if "JJR" in examples:
        comps = ", ".join(f"'{w}'" for w in examples["JJR"])
        parts.append(f"comparative/superlative {comps} (JJR)")

    trigger_str = " and ".join(parts) if parts else "a claim signal"

    # Suggest the first few manifest filenames as candidate sources
    candidates = sorted(manifest_filenames)[:2]
    if candidates:
        cand_str = " or ".join(f"'{c}'" for c in candidates)
        source_hint = (
            f"Look up the relevant passage in {cand_str} (or another manifest document), "
            f"then append its SHA256 from data/manifest.json to this sentence."
        )
    else:
        source_hint = (
            "Find the source document in data/manifest.json and append its SHA256 to this sentence."
        )

    if mode == "COMPLIANCE":
        mode_note = "In Compliance Mode every claim sentence must carry a citation."
    else:
        mode_note = "In Research Mode this is a warning. Address before switching to --mode compliance."

    return (
        f"  Tip: This sentence mentions {trigger_str}.\n"
        f"       {source_hint}\n"
        f"       {mode_note}"
    )


# ── Audit log ─────────────────────────────────────────────────────────────────

def append_audit_log(
    log_path: Path,
    git_commit: str,
    mode: str,
    citation_density: float | None,
    passed: bool,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not log_path.exists()
    timestamp = datetime.now(tz=timezone.utc).isoformat()
    score_str = f"{citation_density:.4f}" if citation_density is not None else "N/A"
    status_str = "PASS" if passed else "FAIL"

    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["Timestamp", "Git_Commit", "Mode", "Citation_Score", "Status"])
        writer.writerow([timestamp, git_commit, mode, score_str, status_str])


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_report(
    verified: list[dict],
    unverified: list[dict],
    density_result: dict,
    threshold: float,
    mode: str,
    draft: bool,
    manifest_filenames: set[str],
) -> None:
    density = density_result["citation_density"]
    factual = density_result["factual_count"]
    cited = density_result["cited_count"]
    naked = density_result["naked_claims"]

    mode_label = f"{'DRAFT — ' if draft else ''}{mode}"
    print(f"\n── Validation Report  [{mode_label}] ──────────────────────────────")

    # ── Citation Verification ─────────────────────────────────────────────
    total_citations = len(verified) + len(unverified)
    print("\n  Citation Verification")
    if total_citations == 0:
        print("  [INFO] No SHA256 hashes or filenames found.")
    else:
        print(f"  ✓ Verified   : {len(verified)}")
        print(f"  ✗ Unverified : {len(unverified)}")
        if verified:
            for item in verified:
                print(f"    [✓] {item['type']:8s}  {item['value']}")
        if unverified:
            dest = sys.stderr if mode == "COMPLIANCE" else sys.stdout
            print("\n  Unverified citations (potential hallucinations):", file=dest)
            for item in unverified:
                print(f"    [✗] {item['type']:8s}  {item['value']}", file=dest)
                print(f"         → {item['detail']}", file=dest)

    # ── Citation Density ──────────────────────────────────────────────────
    print("\n  Citation Density  [NNP · CD · JJR/JJS heuristic]")
    print(f"  Total sentences : {density_result['total_sentences']}")
    print(f"  Claim sentences : {factual}")
    print(f"  Cited           : {cited}")
    print(f"  Need citation   : {len(naked)}")

    if density is None:
        print("  Score           : N/A (no claim sentences)")
    else:
        pct = density * 100
        verdict = "PASS" if density >= threshold else ("NOTE" if draft else "FAIL")
        print(f"  Score           : {pct:.1f}%  [{verdict}]  (threshold: {threshold * 100:.0f}%)")

    # ── Fix-it hints for naked claims ─────────────────────────────────────
    if naked:
        dest = sys.stderr if (mode == "COMPLIANCE" and not draft) else sys.stdout
        print(f"\n  {'─' * 60}", file=dest)
        header = "CITATION NEEDED" if draft else ("NAKED CLAIMS" if mode == "COMPLIANCE" else "CITATION NEEDED")
        print(f"  {header} — {len(naked)} sentence(s) require a manifest SHA256\n", file=dest)
        for i, record in enumerate(naked, 1):
            truncated = record["sentence"][:100] + ("…" if len(record["sentence"]) > 100 else "")
            print(f"  [{i}] \"{truncated}\"", file=dest)
            hint = build_fixit_hint(record, manifest_filenames, mode)
            print(hint, file=dest)
            print(file=dest)

        if not draft:
            if mode == "COMPLIANCE":
                print(
                    "  Every claim sentence must carry a manifest SHA256 in Compliance Mode.\n"
                    "  Use --draft during active writing to see hints without blocking progress.",
                    file=dest,
                )
            else:
                print(
                    f"  Research Mode target is {threshold * 100:.0f}% citation coverage.\n"
                    f"  Work through these tips, then run --mode compliance before finalizing.",
                    file=dest,
                )


def build_json_report(
    verified: list[dict],
    unverified: list[dict],
    density_result: dict,
    threshold: float,
    mode: str,
    draft: bool,
) -> dict:
    density = density_result["citation_density"]
    density_pass = (density is None) or (density >= threshold)
    citations_pass = len(unverified) == 0 if mode == "COMPLIANCE" else True

    naked_export = [
        {
            "sentence": r["sentence"],
            "signals": r["signals"],
            "signal_examples": r.get("signal_examples", {}),
        }
        for r in density_result["naked_claims"]
    ]

    return {
        "mode": mode,
        "draft": draft,
        "passed": (citations_pass and density_pass) or draft,
        "citation_verification": {
            "verified_count": len(verified),
            "unverified_count": len(unverified),
            "verified": verified,
            "unverified": unverified,
            "passed": citations_pass,
        },
        "citation_density": {
            "detector": "POS-heuristic (NNP|CD|JJR)",
            "total_sentences": density_result["total_sentences"],
            "factual_count": density_result["factual_count"],
            "cited_count": density_result["cited_count"],
            "naked_claim_count": len(density_result["naked_claims"]),
            "naked_claims": naked_export,
            "citation_density": density,
            "threshold": threshold,
            "passed": density_pass,
        },
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an AI response against the chain of custody.\n\n"
            "  --mode research  (default) — 70%% threshold, warnings only, draft-friendly.\n"
            "  --mode compliance          — 100%% threshold, hard exits, no naked claims.\n"
            "  --draft                    — shows all tips, always exits 0.\n\n"
            "Note: a PASS in Compliance Mode confirms structural integrity only.\n"
            "Interpretive validity and regulatory conclusions remain the researcher's responsibility."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="File containing the AI response (default: stdin).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifest.json"),
        help="Path to manifest file (default: data/manifest.json).",
    )
    parser.add_argument(
        "--mode",
        choices=["research", "compliance"],
        default=None,
        help=(
            "Rigor mode. 'research' (default): 70%% threshold, warnings only. "
            "'compliance': 100%% threshold, hard exits. "
            "Overridden by --strict if both are supplied."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Shorthand for --mode compliance.",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        default=False,
        help=(
            "Print all Tip-style fix-it hints but always exit 0. "
            "Compatible with either mode. Ideal during active writing."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help=(
            "Override the citation density threshold (0.0–1.0). "
            "Defaults: research=0.70, compliance=1.00."
        ),
    )
    parser.add_argument(
        "--audit-log",
        type=Path,
        default=Path("data/audit_log.csv"),
        help="Path to the append-only audit log (default: data/audit_log.csv).",
    )
    parser.add_argument(
        "--json-report",
        action="store_true",
        help="Emit a machine-readable JSON report.",
    )
    args = parser.parse_args()

    # --strict is an alias for --mode compliance
    compliance = args.strict or (args.mode == "compliance")
    mode = "COMPLIANCE" if compliance else "RESEARCH"
    default_threshold = COMPLIANCE_THRESHOLD if compliance else RESEARCH_THRESHOLD
    threshold = args.threshold if args.threshold is not None else default_threshold

    if not (0.0 <= threshold <= 1.0):
        print("[ERROR] --threshold must be between 0.0 and 1.0", file=sys.stderr)
        sys.exit(2)

    # --draft is compatible with any mode; --strict does not suppress it
    draft = args.draft

    # Read response text
    if args.input:
        if not args.input.exists():
            print(f"[ERROR] Input file not found: {args.input}", file=sys.stderr)
            sys.exit(2)
        response_text = args.input.read_text(encoding="utf-8")
    else:
        response_text = sys.stdin.read()

    known_sha256s, known_filenames = load_manifest(args.manifest.resolve())

    verified, unverified = verify_citations(response_text, known_sha256s, known_filenames)
    density_result = compute_citation_density(response_text, known_sha256s)

    density = density_result["citation_density"]
    density_pass = (density is None) or (density >= threshold)
    # Unverified citations are only hard failures in Compliance Mode
    citations_pass = (len(unverified) == 0) if mode == "COMPLIANCE" else True
    overall_pass = citations_pass and density_pass

    if args.json_report:
        report = build_json_report(verified, unverified, density_result, threshold, mode, draft)
        print(json.dumps(report, indent=2))
    else:
        print_report(
            verified, unverified, density_result, threshold,
            mode, draft, known_filenames,
        )

    # ── Audit log ────────────────────────────────────────────────────────
    git_commit = get_git_commit()
    audit_status = overall_pass or draft  # Draft runs are always logged as informational
    append_audit_log(args.audit_log, git_commit, mode + ("-DRAFT" if draft else ""), density, audit_status)
    if not args.json_report:
        print(f"\n── Audit Log ─────────────────────────────────────────────────────")
        print(f"   Row appended to : {args.audit_log}")
        print(f"   Mode            : {mode}{' (DRAFT)' if draft else ''}")
        print(f"   Status          : {'PASS' if audit_status else 'FAIL'}")

    # Draft mode always exits 0
    sys.exit(0 if (overall_pass or draft) else 1)


if __name__ == "__main__":
    main()
