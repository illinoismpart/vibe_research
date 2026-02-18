# Example research prompts

Use these as templates. Replace placeholders (e.g. "[your corpus]", "[topic]") with your own domain. The comments explain why each prompt is written this way.

---

## 1. Extraction with provenance

**Prompt:**

> Using only documents listed in `data/manifest.json`, extract every mention of [topic] from the ingested corpus. For each mention, give: the exact phrase or sentence, the document filename, the SHA256 hash for that document from the manifest, and the page or section if available. If a document has no mentions, do not include it. If the corpus has no mentions, say so clearly.

**Why it works:** Restricts the agent to the manifest, asks for exact provenance (filename + hash + location), and instructs it to report absence rather than guess.

---

## 2. Comparison across documents

**Prompt:**

> Compare how [concept A] and [concept B] are defined or used across the ingested documents. For each document, cite the relevant passage with filename and SHA256. Summarize similarities and differences in a short table: document, definition or usage, source location. If a document does not address both concepts, note that. Do not use definitions from outside the corpus.

**Why it works:** Asks for a structured summary (table), requires citation for every row, and forbids using external definitions.

---

## 3. Timeline or sequence

**Prompt:**

> Using only the ingested corpus, list changes to [policy / criteria / section] over time. For each change, cite the document and SHA256, and the date or version if present in the document. If the documents do not contain dates or versions, say so and report what you can (e.g. relative order if inferable). Do not invent dates.

**Why it works:** Frames the question in terms of "in the corpus," asks for explicit provenance, and tells the agent to admit when temporal information is missing.

---

## 4. Gap check

**Prompt:**

> I am looking for information about [specific question]. Search only the parsed documents (those in the manifest). List every relevant passage with filename, SHA256, and location. If there is no relevant material, say "The ingested corpus does not contain information that answers this." Do not infer an answer from general knowledge.

**Why it works:** Asks for a negative signal when the corpus doesn't support an answer, and forbids filling in from the model's training data.

---

## 5. Suggest next steps

**Prompt:**

> Based on what is currently in the manifest and the questions we've been asking, what additional documents or data would most strengthen this inquiry? List 3–5 concrete suggestions. For each, say what question it would help answer and why. Do not suggest documents that are already in the manifest.

**Why it works:** Keeps the agent in an advisory role (suggesting what to add) without inventing content from uningested sources.
