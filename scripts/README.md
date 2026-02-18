# Scripts

Two scripts form the core of the ingestion pipeline. Run them from the **repository root** (the folder that contains `data/` and `scripts/`).

---

## ingest.py — Add a document to the chain of custody

**What it does:** Reads a file, computes its SHA256 hash, and appends a new entry to `data/manifest.json`. That entry records the filename, hash, timestamp, and path. Until a document is in the manifest, it is not part of the chain of custody and should not be used as a source for factual claims (per AGENTS.md).

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

**Output:** Prints the filename, SHA256 hash, and path to the manifest. The manifest file is updated in place.

**Options:**

- `--manifest PATH` — Use a different manifest file (default: `data/manifest.json`).

---

## parse.py — Parse a document with Docling

**What it does:** Checks that the file is already in the manifest (chain of custody). If it is, runs Docling to do layout-aware parsing and exports the result as markdown. Saves a JSON file in `data/parsed/` that contains both the parsed content and the provenance metadata (filename, SHA256, ingested_at, source_path) from the manifest.

**When to use it:** Run it after `ingest.py` for each document you want in structured form for search or analysis.

**How to run:**

```bash
python scripts/parse.py <path-to-file>
```

**Examples:**

```bash
# Parse a PDF you already ingested
python scripts/parse.py data/raw/state_plan_2024.pdf
```

**Output:** Creates a file in `data/parsed/` named after the source (e.g. `state_plan_2024.json`). Prints the output path and SHA256. If the file is not in the manifest, the script exits with an error and asks you to run `ingest.py` first.

**Options:**

- `--manifest PATH` — Use a different manifest file (default: `data/manifest.json`).
- `--output-dir PATH` — Write parsed files to a different directory (default: `data/parsed`).

---

## Typical workflow

1. Put source documents in `data/raw/` (or keep them elsewhere and pass the path).
2. For each document: `python scripts/ingest.py data/raw/myfile.pdf`
3. For each document: `python scripts/parse.py data/raw/myfile.pdf`
4. Use the parsed JSON (and optionally an index like Cloudflare AutoRAG) to query. When you use an AI assistant, point it at this repo so it reads AGENTS.md and cites only manifest-backed sources.
