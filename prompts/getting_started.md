# Getting Started: Your First Three Prompts

These prompts are designed to be run immediately after ingesting and parsing your first document. Each one tests a different layer of the pipeline's citation logic, so you can see the guardrails working in real time.

Before you begin:

```bash
python scripts/ingest.py data/raw/your-document.pdf
python scripts/parse.py data/raw/your-document.pdf
```

Then open a session with your AI assistant (Cursor, Claude, Copilot, etc.) in this repository. The agent will read `AGENTS.md` and operate as your Collaborative Research Partner.

---

## Prompt 1 — "What's in the corpus?"

**Purpose:** Verify that the agent reads only from ingested documents and cites provenance correctly. This is your citation-logic smoke test.

**Paste this prompt to your agent:**

```
You are operating as a Collaborative Research Partner in Research Mode.

I have ingested one or more documents into this project. Please do the following:

1. List every document currently in data/manifest.json — show the filename, SHA256, and ingestion date for each.
2. For the first document in the manifest, summarize what it covers in 2–3 sentences. Cite the document filename and SHA256.
3. If you cannot find a manifest entry to support any part of your summary, append [CITATION NEEDED] to that sentence.

Do not use any information from your training data. Only use what is in the ingested corpus.
```

**What to look for:**
- The agent should list real filenames and hashes from `data/manifest.json`, not invented ones.
- The summary should include a citation block with `"sha256"` matching what's in your manifest.
- If the agent invents a hash or filename, that's a hallucination. Run `validate_output.py` on the response to catch it:

```bash
# Save the response to a file, then:
python scripts/validate_output.py --input response.txt --draft
```

---

## Prompt 2 — "Find me a specific claim"

**Purpose:** Test that the agent can locate a precise passage in a parsed document and cite it with a structural location label (not just a page number). This exercises the Docling layout-aware parsing.

**Paste this prompt to your agent:**

```
You are operating as a Collaborative Research Partner in Research Mode.

Using only the documents in data/parsed/, answer the following:

[Replace this line with a specific factual question about your document.
Example: "What eligibility criteria are described in the first document?"]

For your answer:
- Quote the relevant passage directly (do not paraphrase without attribution).
- Cite: filename, SHA256 from the manifest, and the structural location from the parsed JSON
  (prefer a section heading or table identifier over a bare page number).
- If you cannot find a passage that directly supports the answer, say:
  "The ingested corpus does not contain a clear answer to this question."
  Do not guess or fill in from general knowledge.
```

**What to look for:**
- The citation should include a `location` field like `"Section 2 — Eligibility"` or `"Table 1"`, not just `"page 3"`.
- If the location is `UNKNOWN_STRUCTURE`, it means Docling couldn't identify a structural label for that passage (common in scanned documents). That's expected — the agent should note it rather than invent a label.
- Run the response through Research Mode validation:

```bash
python scripts/validate_output.py --input response.txt --draft
```

Any sentence with a number, proper noun, or comparative that lacks a hash will get a `Tip:` hint.

---

## Prompt 3 — "Help me find what's missing"

**Purpose:** Test the agent's "Silence on Uncertainty" behavior — confirming it will flag gaps rather than fill them in. This is the most important research integrity test.

**Paste this prompt to your agent:**

```
You are operating as a Collaborative Research Partner in Research Mode.

I want to understand the scope of what this corpus can and cannot answer.

1. Based on the documents currently in data/manifest.json, what topics or questions
   are well-supported by the ingested material? List 2–3 examples with citations.

2. What is a related topic or question that the corpus does NOT currently support?
   Be specific — tell me what kind of document I would need to ingest to answer it.

3. For any claim in your response that you cannot yet tie to a manifest SHA256,
   append [CITATION NEEDED: describe the document that would support this].

Do not fill gaps with general knowledge. Identifying what we don't yet have is
useful research work — treat it as such.
```

**What to look for:**
- The agent should give concrete `[CITATION NEEDED]` tags rather than making up citations.
- The "what's missing" answer should describe a *type* of document, not a vague disclaimer.
- This output is a great candidate for a `--draft` validation run to see the citation density score:

```bash
python scripts/validate_output.py --input response.txt --draft
```

A Research Mode score of 70%+ means you're on track. Use the `Tip:` hints to tighten before switching to `--mode compliance`.

---

## Next Steps

Once you're comfortable with these three prompts:

- See `prompts/example_inquiry.md` for more advanced, domain-specific prompt patterns.
- When a draft is ready for final review, run: `python scripts/validate_output.py --input final.txt --mode compliance`
- The audit log (`data/audit_log.csv`) records every validation run — it travels with your repo and is part of your research record.
