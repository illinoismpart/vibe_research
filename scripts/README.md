# Scripts

Three scripts form this pipeline. Run them from the **repository root** (the folder that contains `data/` and `scripts/`).

---

## ingest.py — Add a document to the chain of custody

**What it does:** Reads a file, computes its SHA256 hash, and appends a new entry to `data/manifest.json`. That entry records the filename, hash, timestamp, and path. It also regenerates `data/manifest.lock`—a SHA256 hash of the entire manifest file—so any out-of-band edits to `manifest.json` are detectable. Until a document is in the manifest, it is not part of the chain of custody.

**When to use it:** Run it once for each new source document you want to include in your research corpus.

**How to run:**

```bash
python scripts/ingest.py <path-to-file>
```

**Examples:**

```bash
# Ingest a PDF in data/raw/
python scripts/ingest.py data/raw/state_plan_2024.pdf

# Ingest from anywhere (path is stored in manifest)
python scripts/ingest.py /Users/me/Downloads/report.pdf
```

**Output:** Prints the filename, file SHA256, manifest path, and manifest SHA256 written to `manifest.lock`.

**Options:**

- `--manifest PATH` — Use a different manifest file (default: `data/manifest.json`).

---

## parse.py — Parse a document with Docling

**What it does:** **Hard enforces** the chain of custody before any parsing begins. It computes the file's SHA256 and compares it to the manifest. If the hashes differ, the script prints a `PROVENANCE BREACH` alert and exits with code 1—no parsing occurs. If verified, it runs Docling for layout-aware parsing and writes a strictly-typed JSON output to `data/parsed/` with a provenance block and Docling hierarchical location labels. If Docling cannot identify a structural label, the location field is set to `UNKNOWN_STRUCTURE` rather than a guessed value. It also runs a PII/PHI smoke-test before writing any output, quarantining documents that appear to contain sensitive identifiers.

**Rigor modes:** In Research Mode (default), an unsigned manifest is a warning and parsing continues. In Compliance Mode (`--mode compliance`), GPG signing of `manifest.json` is required and the script exits 1 if unsigned.

**When to use it:** Run it after `ingest.py` for each document you want in structured form for search or analysis.

**How to run:**

```bash
# Research Mode (default) — GPG unsigned is a warning
python scripts/parse.py data/raw/state_plan_2024.pdf

# Compliance Mode — GPG signature required
python scripts/parse.py data/raw/state_plan_2024.pdf --mode compliance
```

**Output:** Creates a file in `data/parsed/` named after the source (e.g. `state_plan_2024.json`). Prints the output path and SHA256. If the file is not in the manifest, the script exits with an error and asks you to run `ingest.py` first.

**Options:**

- `--manifest PATH` — Use a different manifest file (default: `data/manifest.json`).
- `--output-dir PATH` — Write parsed files to a different directory (default: `data/parsed`).
- `--mode {research,compliance}` — Rigor mode (default: `research`). Compliance mode requires GPG signing.
- `--strict` — Shorthand for `--mode compliance`.

---

## validate_output.py — Verify an AI response against the manifest

**What it does:** Scans an AI-generated response for SHA256 hashes and filenames and checks every one against `data/manifest.json`. Also runs a POS-heuristic Citation Density check: sentences containing proper nouns, numbers, or comparatives are classified as "claim sentences" and must carry a manifest SHA256. Sentences without citations receive a **Tip-style fix-it hint** naming the triggering token and suggesting the right manifest document. Every run is appended to `data/audit_log.csv`.

**Rigor modes:**

| Mode | Flag | Threshold | On unverified citation | On missing citation | Exit on fail |
|---|---|---|---|---|---|
| Research | default | 70% | Warning | Tip hint | 1 (unless `--draft`) |
| Compliance | `--mode compliance` | 100% | Hard error | Tip hint + fail | 1 |
| Draft | `--draft` | any | Tip hint | Tip hint | always 0 |

> A PASS in Compliance Mode confirms *structural integrity*. It does not confirm that interpretations are analytically correct. That remains the researcher's responsibility.

**When to use it:** After any AI response that makes factual claims. Use `--draft` while writing; use `--mode compliance` before finalizing.

**How to run:**

```bash
# Research Mode — 70% threshold, warnings only
python scripts/validate_output.py --input response.txt

# Compliance Mode — 100% threshold, hard exits
python scripts/validate_output.py --input response.txt --mode compliance

# Draft Mode — shows all Tip hints, never exits 1
python scripts/validate_output.py --input response.txt --draft

# Draft + Compliance Mode — compliance checks with fix-it hints, always exits 0
python scripts/validate_output.py --input response.txt --mode compliance --draft

# Machine-readable JSON report
python scripts/validate_output.py --input response.txt --json-report
```

**Options:**

- `--input PATH` — Path to the response file (default: stdin).
- `--manifest PATH` — Use a different manifest file (default: `data/manifest.json`).
- `--mode {research,compliance}` — Rigor mode (default: `research`). Alias: `--strict` = `--mode compliance`.
- `--draft` — Show all tips and always exit 0. Compatible with any mode.
- `--threshold FLOAT` — Override the citation density threshold (0.0–1.0).
- `--audit-log PATH` — Write audit rows to a different CSV (default: `data/audit_log.csv`).
- `--json-report` — Emit a JSON object instead of human-readable text.

---

## Typical workflow

1. Put source documents in `data/raw/` (or keep them elsewhere and pass the path).
2. For each document: `python scripts/ingest.py data/raw/myfile.pdf`
3. For each document: `python scripts/parse.py data/raw/myfile.pdf`
4. Use the parsed JSON (and optionally an index like Cloudflare AutoRAG) to query. When you use an AI assistant, point it at this repo so it reads `AGENTS.md` and cites only manifest-backed sources.
5. **While drafting** — validate with tip hints, never blocked:
   ```bash
   python scripts/validate_output.py --input response.txt --draft
   ```
6. **Before finalizing** — run full compliance check:
   ```bash
   python scripts/parse.py data/raw/myfile.pdf --mode compliance
   python scripts/validate_output.py --input response.txt --mode compliance
   ```

If `validate_output.py --mode compliance` exits with code 1, address the flagged Tip hints before submitting. A PASS confirms structural integrity; interpretive validity requires peer review.
